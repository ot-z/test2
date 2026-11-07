"""题号链接规则、自动扫描录入与题库修复的验收测试（FR-29）。

重点覆盖用户反馈的问题：蓝桥杯 A+B 题号不是 1、链接不对；
以及「只配置链接规则就能新增站点」这一核心约束。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app import seed
from app.errors import ValidationError
from app.models import Problem, ProblemSnapshot
from app.services import catalog, collector, link_rules as lr

CUSTOM_RULE_YAML = """version: 1
rules:
  nowcoder:
    label: 牛客网
    aliases: [nowcoder, nc, 牛客]
    key_pattern: '^\\d{1,8}$'
    url_template: "https://ac.nowcoder.com/acm/problem/{key}"
    url_regexes:
      - 'https?://ac\\.nowcoder\\.com/acm/problem/(\\d+)'
    url_key_template: '{1}'
    scan_regexes:
      - '\\bNC(\\d{1,8})\\b'
    range_enabled: true
    examples: ["NC12345"]
"""


# ---------------------------------------------------------------- 题号与链接规则

def test_lanqiao_ab_problem_is_254_not_1():
    """用户反馈：A+B 问题【OJ 示例题目】的官方编号是 254，不是 1。"""
    assert lr.derive_url("lanqiao", "254") == "https://www.lanqiao.cn/problems/254/learning/"
    assert lr.derive_url("lanqiao", "1") != lr.derive_url("lanqiao", "254")
    assert lr.check("lanqiao", "254", lr.derive_url("lanqiao", "254"))["ok"] is True
    # 题号 254 出现在内置起始题库，且不再存在虚构的 1~6
    from app.seed_data import STARTER_PROBLEMS, LEGACY_LANQIAO_FIX
    keys = {p["source_key"] for p in STARTER_PROBLEMS if p["source"] == "lanqiao"}
    assert "254" in keys
    assert set(LEGACY_LANQIAO_FIX).isdisjoint(keys)
    assert "A+B" in next(p for p in STARTER_PROBLEMS
                         if p["source"] == "lanqiao" and p["source_key"] == "254")["title"]


def test_lanqiao_legacy_variant():
    assert lr.derive_url("lanqiao", "T1") == "https://lx.lanqiao.cn/problem.page?gpid=T1"
    assert lr.derive_url("lanqiao", "BEGIN-1") == "https://lx.lanqiao.cn/problem.page?gpid=BEGIN-1"
    assert lr.derive_url("lanqiao", "LQ254") == "https://www.lanqiao.cn/problems/254/learning/"
    assert lr.normalize_key("lanqiao", "0254") == "254"


def test_luogu_key_rules():
    assert lr.normalize_key("luogu", "1001") == "P1001"
    assert lr.normalize_key("luogu", "p8649") == "P8649"
    assert lr.derive_url("luogu", "1001") == "https://www.luogu.com.cn/problem/P1001"
    assert lr.is_valid_key("luogu", "P16232") is True       # 蓝桥杯 2026 省 B 真题
    assert lr.is_valid_key("luogu", "CF1234A") is True      # 洛谷 Remote Judge
    assert lr.is_valid_key("luogu", "T1") is False          # 团队题为 4~8 位数字；T1 归蓝桥 legacy


def test_url_parse_roundtrip():
    cases = [
        ("https://www.luogu.com.cn/problem/P1001", "luogu", "P1001"),
        ("https://www.lanqiao.cn/problems/254/learning/", "lanqiao", "254"),
        ("https://codeforces.com/problemset/problem/4/A", "codeforces", "4A"),
        ("https://atcoder.jp/contests/abc001/tasks/abc001_1", "atcoder", "abc001_1"),
        ("https://leetcode.cn/problems/two-sum/", "leetcode", "two-sum"),
        ("https://lx.lanqiao.cn/problem.page?gpid=T1", "lanqiao", "T1"),
    ]
    for url, source, key in cases:
        parsed = lr.parse_url(url)
        assert parsed is not None, url
        assert (parsed["source"], parsed["source_key"]) == (source, key)
        assert lr.check(source, key, url)["ok"] is True


def test_check_detects_wrong_link():
    result = lr.check("luogu", "P1001", "https://www.luogu.com.cn/problem/P1002")
    assert result["ok"] is False
    assert result["issue"] == "url_mismatch"
    assert result["expected_url"].endswith("P1001")


# ---------------------------------------------------------------- 扫描

def test_scan_lines_mixed_and_ranges():
    text = """# 混合清单
254 A+B 问题【OJ 示例题目】
LQ605 年号字串
https://www.luogu.com.cn/problem/P8649 k 倍区间
P1001-P1003
T1 入门训练 A+B 问题
CF71A Way Too Long Words
"""
    out = lr.scan(text, source="auto", mode="lines", default_source="lanqiao")
    assert out["issues"] == []
    pairs = {(i["source"], i["source_key"]) for i in out["items"]}
    assert ("lanqiao", "254") in pairs
    assert ("lanqiao", "605") in pairs
    assert ("luogu", "P8649") in pairs
    assert {("luogu", "P1001"), ("luogu", "P1002"), ("luogu", "P1003")} <= pairs
    assert ("lanqiao", "T1") in pairs
    assert ("codeforces", "71A") in pairs
    assert all(i["url"].startswith("http") for i in out["items"])


def test_scan_text_mode_keeps_document_order():
    out = lr.scan("先看 P1001，再看 LQ254，最后是 https://www.luogu.com.cn/problem/P8649",
                  mode="text")
    assert [(i["source"], i["source_key"]) for i in out["items"]] == [
        ("luogu", "P1001"), ("lanqiao", "254"), ("luogu", "P8649")
    ]


def test_scan_bare_number_uses_default_source():
    luogu = lr.scan("254 A+B", source="auto", default_source="luogu")["items"]
    lanqiao = lr.scan("254 A+B", source="auto", default_source="lanqiao")["items"]
    assert (luogu[0]["source"], luogu[0]["source_key"]) == ("luogu", "P254")
    assert (lanqiao[0]["source"], lanqiao[0]["source_key"]) == ("lanqiao", "254")


# ---------------------------------------------------------------- 采集与修复

def test_collect_dry_run_then_commit(db):
    seed.seed_all(db)
    before = db.scalar(select(Problem).where(Problem.source == "lanqiao",
                                             Problem.source_key == "605"))
    assert before is not None  # 起始题库已含该题，用未入库的新编号演示

    payload = {
        "text": "LQ7777 自定义采集题\nP9999 洛谷采集题\nhttps://www.luogu.com.cn/problem/P8649 k 倍区间",
        "source": "auto", "mode": "lines", "default_source": "lanqiao",
    }
    preview = collector.collect(db, payload, dry_run=True)
    assert preview["scanned"] == 3
    assert preview["added"] == 2          # P8649 已存在 → 重复
    assert preview["committed"] is False
    assert preview["snapshots_created"] == 0
    assert db.scalar(select(Problem).where(Problem.source_key == "7777")) is None

    committed = collector.collect(db, payload, dry_run=False)
    assert committed["committed"] is True and committed["added"] == 2
    row = db.scalar(select(Problem).where(Problem.source == "lanqiao",
                                          Problem.source_key == "7777"))
    assert row is not None
    assert row.url == "https://www.lanqiao.cn/problems/7777/learning/"
    assert db.scalar(select(ProblemSnapshot).where(ProblemSnapshot.problem_id == row.id)) is None

    again = collector.collect(db, payload, dry_run=True)
    assert again["added"] == 0 and len(again["duplicates"]) == 3


def test_collect_with_tags_does_not_duplicate_knowledge(db):
    """回归：knowledge_nodes 与 external_tags 归一到同一节点时不得触发唯一约束。"""
    seed.seed_all(db)
    payload = {
        "text": "P9998 背包练习",
        "source": "luogu", "mode": "lines",
        "knowledge_nodes": "背包DP, 动态规划",
        "external_tags": "动态规划, dp",
    }
    result = collector.collect(db, payload, dry_run=False)
    assert result["added"] == 1
    row = db.scalar(select(Problem).where(Problem.source_key == "P9998"))
    assert row is not None


def test_audit_and_repair_url(db):
    seed.seed_all(db)
    row = db.scalar(select(Problem).where(Problem.source == "luogu",
                                          Problem.source_key == "P1001"))
    row.url = "https://www.luogu.com.cn/problem/P1002"
    db.commit()

    report = collector.audit(db)
    assert report["by_issue"].get("url_mismatch", 0) == 1

    preview = collector.repair(db, dry_run=True)
    assert preview["committed"] is False and len(preview["url_changes"]) == 1
    assert db.scalar(select(Problem).where(Problem.id == row.id)).url.endswith("P1002")

    applied = collector.repair(db, dry_run=False)
    assert applied["committed"] is True
    assert db.scalar(select(Problem).where(Problem.id == row.id)).url.endswith("P1001")
    assert collector.audit(db)["issue_count"] == 0


def test_audit_and_repair_legacy_lanqiao_seed(db):
    """旧库里虚构的 1 号 A+B 应被修正为 254。"""
    db.add(Problem(
        source="lanqiao", source_key="1", title="A+B 问题（入门）",
        url="https://www.lanqiao.cn/problems/1/learning/",
        difficulty_level=1, external_tags=[], note="", summary="",
        is_active=True, manual_edited=False, sync_status="seed",
    ))
    db.commit()

    report = collector.audit(db)
    assert report["by_issue"].get("legacy_seed", 0) == 1
    assert report["issues"][0]["expected_key"] == "254"

    applied = collector.repair(db, dry_run=False)
    assert applied["committed"] is True
    assert len(applied["legacy_changes"]) == 1
    # 种子库已含真实 254 题 → 重复的旧记录应被停用，不能被改成同题号造成重复
    legacy = db.scalar(select(Problem).where(Problem.source == "lanqiao",
                                             Problem.source_key == "1"))
    assert legacy is not None and legacy.is_active is False
    real = db.scalar(select(Problem).where(Problem.source == "lanqiao",
                                           Problem.source_key == "254"))
    assert real.url == "https://www.lanqiao.cn/problems/254/learning/"
    assert "A+B" in real.title
    assert collector.audit(db)["issue_count"] == 0


def test_fix_legacy_updates_key_when_no_duplicate():
    """库里没有真实 254 题时，旧记录直接改号为 254。"""
    from app.db import SessionLocal
    session = SessionLocal()
    try:
        session.add(Problem(
            source="lanqiao", source_key="1", title="A+B 问题（入门）",
            url="https://www.lanqiao.cn/problems/1/learning/",
            difficulty_level=1, external_tags=[], note="", summary="",
            is_active=True, manual_edited=False, sync_status="seed",
        ))
        session.commit()
        changed = seed.fix_legacy_lanqiao(session)
        assert changed and changed[0]["action"] == "updated"
        row = session.scalar(select(Problem).where(Problem.source == "lanqiao"))
        assert row.source_key == "254"
        assert row.url == "https://www.lanqiao.cn/problems/254/learning/"
        assert "A+B" in row.title
        assert seed.fix_legacy_lanqiao(session) == []  # 幂等
    finally:
        session.close()


# ---------------------------------------------------------------- 配置驱动

def test_custom_rule_can_add_source_without_code_change(db, tmp_path, monkeypatch):
    """核心约束：只写 YAML 就能新增来源，且能直接走导入接口。"""
    custom = tmp_path / "link_rules.custom.yaml"
    custom.write_text(CUSTOM_RULE_YAML, encoding="utf-8")
    monkeypatch.setattr(lr, "CUSTOM_PATH", custom)
    lr.reload_rules()
    try:
        assert "nowcoder" in lr.known_sources()
        assert lr.derive_url("nowcoder", "12345") == "https://ac.nowcoder.com/acm/problem/12345"
        assert lr.parse_url("https://ac.nowcoder.com/acm/problem/12345")["source"] == "nowcoder"
        text = "NC12345 牛客测试题"
        out = lr.scan(text, source="nowcoder", mode="lines")
        assert out["items"] and out["items"][0]["source"] == "nowcoder"
        result = catalog.import_batch(db, {"schema_version": "1.0", "problems": [
            {"source": "nowcoder", "source_key": "12345", "title": "牛客测试题",
             "url": lr.derive_url("nowcoder", "12345")},
        ]}, dry_run=False)
        assert result["added"] == 1
    finally:
        monkeypatch.undo()
        lr.reload_rules()
    assert "nowcoder" not in lr.known_sources()


def test_save_custom_yaml_rejects_bad_regex(tmp_path, monkeypatch):
    custom = tmp_path / "link_rules.custom.yaml"
    custom.write_text("version: 1\nrules: {}\n", encoding="utf-8")
    monkeypatch.setattr(lr, "CUSTOM_PATH", custom)
    lr.reload_rules()
    try:
        with pytest.raises(ValidationError):
            lr.save_custom_yaml("rules:\n  luogu:\n    key_pattern: '^('\n")
        assert "rules: {}" in custom.read_text(encoding="utf-8")  # 非法配置不落盘
    finally:
        monkeypatch.undo()
        lr.reload_rules()


def test_no_network_code_in_rule_and_collector_modules():
    """合规：规则引擎与采集模块不含任何网页抓取 / HTTP 客户端代码。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ("app/services/link_rules.py", "app/services/collector.py"):
        source = (root / name).read_text(encoding="utf-8")
        for forbidden in ("import requests", "import httpx", "urlopen(", "BeautifulSoup"):
            assert forbidden not in source, f"{name} 含 {forbidden}"


# ---------------------------------------------------------------- API

def test_api_rule_endpoints(client, db):
    seed.seed_all(db)
    assert client.get("/api/link-rules").status_code == 200
    assert client.get("/api/link-rules/examples").json()["items"]
    derived = client.get("/api/link-rules/derive", params={"source": "lanqiao", "key": "LQ254"}).json()
    assert derived["valid"] is True and derived["normalized_key"] == "254"

    preview = client.post("/api/link-rules/preview", json={
        "text": "254 A+B 问题\nP8649 k 倍区间", "source": "lanqiao", "mode": "lines",
    }).json()
    assert preview["total"] == 2

    dry = client.post("/api/link-rules/collect?dry_run=true", json={
        "text": "LQ7778 接口采集题", "source": "lanqiao", "mode": "lines",
    }).json()
    assert dry["added"] == 1 and dry["committed"] is False
    assert client.get("/api/link-rules/audit").json()["issue_count"] == 0
