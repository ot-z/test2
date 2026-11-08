"""FR-30 洛谷题面采集的离线验收测试（不发任何外部请求）。"""
from __future__ import annotations

import json

from sqlalchemy import func, select

from app.models import Problem, ProblemKnowledge, ProblemSnapshot
from app.services import luogu, luogu_store


def _tagbook() -> luogu.TagBook:
    return luogu.TagBook({"tags": [
        {"id": 1, "name": "模拟", "type": 2, "parent": 110},
        {"id": 3, "name": "动态规划 DP", "type": 2, "parent": None},
        {"id": 60, "name": "背包 DP", "type": 2, "parent": 3},
        {"id": 14, "name": "1997", "type": 4, "parent": None},
        {"id": 110, "name": "基础算法", "type": 6, "parent": None},
        {"id": 999, "name": "新出的线段树技巧", "type": 2, "parent": None},
    ]})


def _page(pid: str = "P1001", status: int = 200, template: str = "problem.show",
          description: str = "输入两个整数，输出它们的和。") -> str:
    ctx = {
        "status": status, "template": template, "locale": "zh-CN",
        "data": {} if status != 200 else {
            "problem": {
                "pid": pid, "type": "P", "name": "A+B Problem", "difficulty": 2,
                "fullScore": 100, "tags": [1, 3, 60, 14],
                "totalSubmit": 100, "totalAccepted": 60,
                "content": {
                    "name": "A+B Problem", "background": "背景说明",
                    "description": description, "formatI": "两个整数",
                    "formatO": "一个整数", "hint": "数据范围",
                },
                "samples": [["20 30\n", "50\n"]],
                "limits": {"time": [1000], "memory": [131072]},
            },
            "translations": {},
        },
    }
    payload = json.dumps(ctx, ensure_ascii=False)
    return f'<html><script id="lentille-context" type="application/json">{payload}</script></html>'


def test_parse_problem_page_extracts_everything():
    cfg = luogu.load_config()
    item = luogu.parse_problem_page(_page(), "P1001", cfg, tagbook=_tagbook())
    assert item["pid"] == "P1001"
    assert item["difficulty"]["level"] == 2
    assert item["tags"]["all"] == ["模拟", "动态规划 DP", "背包 DP", "1997"]
    assert "1997" not in item["tags"]["knowledge"]  # 年份不算知识点
    assert "动态规划" in item["knowledge_nodes"]
    assert "背包DP" in item["knowledge_nodes"]
    md = item["content_md"]
    assert "# P1001 A+B Problem" in md
    assert "## 题目描述" in md and "输入两个整数" in md
    assert "## 输入格式" in md and "## 输出格式" in md
    assert "### 样例 1" in md and "50" in md
    assert "仅供个人本地学习使用" in md


def test_parse_missing_problem_raises():
    cfg = luogu.load_config()
    try:
        luogu.parse_problem_page(_page(status=404, template="error"), "P99999", cfg,
                                 tagbook=_tagbook())
    except luogu.ProblemNotFound:
        pass
    else:
        raise AssertionError("404 页面应抛 ProblemNotFound")


def test_difficulty_uses_official_names_and_local_levels():
    cfg = luogu.load_config()
    names = {"4": "普及+/提高−", "8": "NOI/NOI+/CTS"}
    assert luogu.difficulty_info(4, cfg, names)["name"] == "普及+/提高−"
    assert luogu.difficulty_info(4, cfg, names)["level"] == 3
    assert luogu.difficulty_info(8, cfg, names)["level"] == 5
    assert luogu.difficulty_info(1, cfg)["raw"] == "洛谷1·入门"
    assert luogu.difficulty_info(None, cfg)["value"] == 0


def test_tagbook_parent_only_for_unmapped_tags():
    cfg = luogu.load_config()
    names = _tagbook().names([1, 60, 14], cfg)
    # 「模拟」有精确映射，不再借父级「基础算法」；「背包 DP」有精确映射，不重复加父级
    assert "基础算法" not in names["knowledge"]
    assert "1997" in names["all"] and "1997" not in names["knowledge"]


def test_map_knowledge_exact_first_then_keyword():
    cfg = luogu.load_config()
    result = luogu.map_knowledge(["动态规划 DP", "背包 DP", "新出的线段树技巧"], cfg)
    assert result[:2] == ["动态规划", "背包DP"]
    assert "线段树" in result
    assert len(result) == len(set(result))  # 去重


def test_fingerprint_ignores_capture_time():
    a = "标题\n> **本地采集时间**：2026-11-07 10:00:00\n正文"
    b = "标题\n> **本地采集时间**：2026-11-08 12:34:56\n正文"
    c = "标题\n> **本地采集时间**：2026-11-07 10:00:00\n正文变了"
    assert luogu_store.content_fingerprint(a) == luogu_store.content_fingerprint(b)
    assert luogu_store.content_fingerprint(a) != luogu_store.content_fingerprint(c)


def test_pid_helpers_and_range_resume(db):
    assert luogu_store.normalize_pid("1001") == "P1001"
    assert luogu_store.normalize_pid("p8649") == "P8649"
    assert luogu_store.normalize_pid("CF1234A") == "CF1234A"
    assert luogu_store.pid_number("P8649") == 8649
    pids = luogu_store.build_pid_list(db, {
        "mode": "range", "start": 1001, "count": 5,
        "state": {"done": ["P1002"], "missing": [], "failed": {}},
    }, None)
    assert pids == ["P1001", "P1003", "P1004", "P1005"]
    assert luogu_store.build_pid_list(db, {
        "mode": "range", "start": 1001, "count": 5, "max_items": 2,
        "state": {"done": [], "missing": [], "failed": {}},
    }, None) == ["P1001", "P1002"]


def test_store_problem_is_idempotent(db):
    cfg = luogu.load_config()
    item = luogu.parse_problem_page(_page(), "P1001", cfg, tagbook=_tagbook())
    first = luogu_store.store_problem(db, item, cfg, save_files=False)
    assert first["action"] in ("created", "updated")
    assert first["snapshot"] == "created"
    assert first["nodes_added"] > 0

    problem = db.scalar(select(Problem).where(Problem.source == "luogu",
                                              Problem.source_key == "P1001"))
    assert problem.sync_status == "crawled"
    assert problem.difficulty_raw == "洛谷2·普及−"
    assert "动态规划 DP" in problem.external_tags
    assert db.scalar(select(func.count()).select_from(ProblemKnowledge)
                     .where(ProblemKnowledge.problem_id == problem.id)) >= 3
    assert db.scalar(select(func.count()).select_from(ProblemSnapshot)
                     .where(ProblemSnapshot.problem_id == problem.id)) == 1

    # 同一份题面重复写入：不新增快照、不重复关联知识点
    again = luogu_store.store_problem(db, item, cfg, save_files=False)
    assert again["snapshot"] == "duplicate"
    assert again["nodes_added"] == 0
    assert db.scalar(select(func.count()).select_from(ProblemSnapshot)
                     .where(ProblemSnapshot.problem_id == problem.id)) == 1

    # 题面真的变了才留一份新快照
    changed = luogu.parse_problem_page(_page(description="题目已改版"), "P1001", cfg,
                                       tagbook=_tagbook())
    luogu_store.store_problem(db, changed, cfg, save_files=False)
    assert db.scalar(select(func.count()).select_from(ProblemSnapshot)
                     .where(ProblemSnapshot.problem_id == problem.id)) == 2


def test_api_status_endpoint(client):
    resp = client.get("/api/luogu/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "snapshots" in data and "state" in data and "catalog" in data
    assert data["config_version"] == 1


def test_api_rejects_oversized_batch(client):
    resp = client.post("/api/luogu/fetch", json={"start": 1001, "end": 2000})
    assert resp.status_code >= 400
    assert "60" in json.dumps(resp.json(), ensure_ascii=False)
    assert "当前" in resp.json()["error"]["message"]


def test_api_rejects_short_interval_with_detail(client):
    resp = client.post("/api/luogu/fetch",
                       json={"start": 1001, "end": 1005, "interval": 0.3})
    assert resp.status_code == 422
    assert "0.3" in resp.json()["error"]["message"]


def test_rebuild_index_degrades_when_file_locked(db, monkeypatch, tmp_path):
    """index.csv 被 Excel 等程序独占时，索引降级为备用文件，绝不抛异常中断采集。"""
    from pathlib import Path

    monkeypatch.setattr(luogu_store, "MD_DIR", tmp_path)
    monkeypatch.setattr(luogu_store, "INDEX_PATH", tmp_path / "index.csv")
    real_replace = Path.replace

    def locked_replace(self, target):
        if self.name.endswith(".csv.tmp") and Path(target).name == "index.csv":
            raise PermissionError(13, "Permission denied")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", locked_replace)
    result = luogu_store.rebuild_index(db)
    assert result["rows"] >= 0
    assert "warning" in result and "index.csv" in result["warning"]
    assert result["path"] and "index-" in result["path"]
    assert Path(result["path"]).exists()
