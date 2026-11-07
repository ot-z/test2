"""TC-05 外链、TC-11 单题快照边界；以及附录 C 导入契约额外用例。"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.errors import ValidationError
from app.models import Problem, ProblemKnowledge, ProblemSnapshot
from app.services import catalog, exporter, plan
from app.utils import is_http_url

START = date(2026, 11, 9)
MARKER = "SNAPSHOT_SECRET_MARKER_9527"


def _count(db, model):
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _configure(db, **overrides):
    payload = dict(start_date=START.isoformat(), new_per_day=1, review_per_day=1,
                   review_delay_days=7, review_repeat=1)
    payload.update(overrides)
    return plan.update_config(db, payload)


def test_tc05_external_links(db):
    """题库中题目的 url 均为 http(s) 且与 source 的链接规则匹配。"""
    host_rules = {
        "luogu": "luogu.com.cn",
        "codeforces": "codeforces.com",
        "leetcode": "leetcode.cn",
        "atcoder": "atcoder.jp",
        "lanqiao": "lanqiao.cn",
    }
    problems = db.scalars(select(Problem)).all()
    assert len(problems) >= 20  # 种子题库应有一批可抽查的题目
    for p in problems:
        assert is_http_url(p.url), f"{p.source}/{p.source_key} 的 url 不是 http(s): {p.url!r}"
        assert host_rules[p.source] in p.url, f"{p.source} 的 url 与来源规则不符: {p.url}"
        expected = catalog.derive_url(p.source, p.source_key)
        assert expected and is_http_url(expected)
        assert p.url == expected, f"{p.source}/{p.source_key}: {p.url} != {expected}"


def test_tc11_snapshot_boundary(client, db):
    """单题快照正文不进入 Markdown/DOCX 导出、不被批量导入自动创建、
    不进入 JSON 快照（excluded_tables）。"""
    problem = db.scalars(select(Problem)).first()

    # 1) 保存 1 条含唯一标记的单题快照
    catalog.save_snapshot(db, problem.id, {
        "content_md": f"题面原文，含唯一标记 {MARKER}，不得外泄",
        "source_url": problem.url,
    })
    assert _count(db, ProblemSnapshot) == 1
    detail = catalog.problem_detail(db, problem)
    assert detail["snapshots_count"] == 1  # 题库详情可见快照计数
    listed = catalog.list_snapshots(db, problem.id)
    assert listed[0]["source_url"] and listed[0]["captured_at"]  # 带来源标注与时间

    # 2) 生成计划，保证导出报告有实质内容
    _configure(db)
    plan.generate(db, days=3, from_date=START)
    db.commit()
    date_from, date_to = START.isoformat(), (START + timedelta(days=2)).isoformat()

    # 3) Markdown 导出：正文中检索不到标记
    r = client.post("/api/exports", json={"date_from": date_from, "date_to": date_to,
                                          "format": "md"})
    assert r.status_code == 200, r.text
    md_path = Path(r.json()["file_path"])
    md_text = md_path.read_text(encoding="utf-8")
    assert "刷题复盘报告" in md_text  # 确实生成了报告正文
    assert MARKER not in md_text

    # 4) DOCX 导出：原始字节与解析后的段落文本都检索不到标记
    r2 = client.post("/api/exports", json={"date_from": date_from, "date_to": date_to,
                                           "format": "docx"})
    assert r2.status_code == 200, r2.text
    docx_path = Path(r2.json()["file_path"])
    assert MARKER.encode("utf-8") not in docx_path.read_bytes()
    from docx import Document
    doc = Document(str(docx_path))
    assert MARKER not in "\n".join(par.text for par in doc.paragraphs)

    # 5) 批量导入 10 题：快照表仍只有 1 条
    payload = {
        "schema_version": "1.0",
        "problems": [
            {"source": "luogu", "source_key": f"TC11_P{i}",
             "title": f"批量题 {i}", "difficulty_level": 2}
            for i in range(10)
        ],
    }
    r3 = client.post("/api/problems/import", json=payload)
    assert r3.status_code == 200, r3.text
    body = r3.json()
    assert body["added"] == 10
    assert body["snapshots_created"] == 0
    assert _count(db, ProblemSnapshot) == 1

    # 6) JSON 快照：排除 problem_snapshots，序列化内容不含标记
    snap = exporter.snapshot_export(db)
    assert "problem_snapshots" in snap["excluded_tables"]
    assert "problem_snapshots" not in snap["tables"]
    assert MARKER not in json.dumps(snap, ensure_ascii=False)


def test_extra_import_batch_contract(db):
    """附录 C 契约：schema_version=1.0、confidence=ai -> verified=false、
    dry_run 不写库、非法 schema_version 被拒。"""
    def _payload(version="1.0"):
        return {"data": {"schema_version": version, "problems": [
            {"source": "luogu", "source_key": "CONTRACT_AI_1", "title": "AI 标注题",
             "url": "https://www.luogu.com.cn/problem/CONTRACT_AI_1",
             "knowledge_nodes": ["基础语法", "模拟"],
             "confidence": "ai", "verified": True},
            {"source": "luogu", "source_key": "CONTRACT_MANUAL_1", "title": "手工题",
             "url": "https://www.luogu.com.cn/problem/CONTRACT_MANUAL_1",
             "knowledge_nodes": ["基础语法"]},
        ]}}

    before = _count(db, Problem)

    # dry_run：只预览不写库
    preview = catalog.import_batch(db, _payload(), dry_run=True)
    assert preview["dry_run"] is True
    assert preview["committed"] is False
    assert preview["added"] == 2
    assert preview["snapshots_created"] == 0
    assert _count(db, Problem) == before

    # 正式导入
    res = catalog.import_batch(db, _payload(), dry_run=False)
    assert res["committed"] is True
    assert res["added"] == 2
    assert _count(db, Problem) == before + 2

    ai_problem = db.scalar(select(Problem).where(Problem.source_key == "CONTRACT_AI_1"))
    ai_links = db.scalars(select(ProblemKnowledge).where(
        ProblemKnowledge.problem_id == ai_problem.id)).all()
    assert ai_links, "confidence=ai 的题应带知识点关联"
    assert all(l.confidence == "ai" and l.verified is False for l in ai_links), \
        "confidence=ai 必须 verified=false，即使显式传 verified=true"

    manual = db.scalar(select(Problem).where(Problem.source_key == "CONTRACT_MANUAL_1"))
    manual_links = db.scalars(select(ProblemKnowledge).where(
        ProblemKnowledge.problem_id == manual.id)).all()
    assert manual_links and all(l.verified is True for l in manual_links)

    # 非法 schema_version 被拒（data 包装契约分支）
    with pytest.raises(ValidationError):
        catalog.import_batch(db, _payload("2.0"))
