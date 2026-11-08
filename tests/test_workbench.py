"""题目工作台（FR-31）验收测试：聚合详情、代码草稿、独立窗口数据。"""
from __future__ import annotations

from sqlalchemy import func, select

from app.models import CodeDraft, Problem


def _first_problem(db) -> Problem:
    return db.scalar(select(Problem).order_by(Problem.id))


def test_workbench_detail_aggregates_all_panels(client, db):
    p = _first_problem(db)
    resp = client.get(f"/api/problems/{p.id}/workbench")
    assert resp.status_code == 200
    d = resp.json()
    assert d["problem"]["id"] == p.id
    for key in ("snapshots", "latest_snapshot", "draft", "records", "notes",
                "ai_calls", "plan_items", "languages"):
        assert key in d, key
    assert d["draft"]["exists"] is False
    assert d["draft"]["language"] == "C++"
    assert "C++" in d["languages"]


def test_code_draft_is_upserted_per_problem(client, db):
    p = _first_problem(db)
    r1 = client.put(f"/api/problems/{p.id}/code",
                    json={"language": "C++", "code": "#include <bits/stdc++.h>\nint main(){}"})
    assert r1.status_code == 200
    assert r1.json()["code"].startswith("#include")

    r2 = client.put(f"/api/problems/{p.id}/code",
                    json={"language": "Python", "code": "print(1)"})
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"], "同一题应覆盖而非新增草稿"
    assert db.scalar(select(func.count()).select_from(CodeDraft)) == 1

    d = client.get(f"/api/problems/{p.id}/workbench").json()
    assert d["draft"]["exists"] is True
    assert d["draft"]["language"] == "Python"
    assert d["draft"]["code"] == "print(1)"


def test_workbench_unknown_problem_returns_404(client):
    assert client.get("/api/problems/999999/workbench").status_code == 404
    assert client.put("/api/problems/999999/code", json={"code": "x"}).status_code == 404


def test_workbench_includes_snapshot_note_and_record(client, db):
    p = _first_problem(db)
    client.post(f"/api/problems/{p.id}/snapshots",
                json={"content_md": "## 题面\n求 A+B", "source_url": ""})
    client.post("/api/notes", json={"problem_id": p.id, "title": "思路", "content_md": "直接相加"})
    client.post("/api/records", json={"problem_id": p.id, "result": "done",
                                     "code_snippet": "int main(){}"})
    d = client.get(f"/api/problems/{p.id}/workbench").json()
    assert d["latest_snapshot"]["content_md"] == "## 题面\n求 A+B"
    assert len(d["snapshots"]) == 1
    assert any(n["title"] == "思路" for n in d["notes"])
    assert any(r["code_snippet"] == "int main(){}" for r in d["records"])


def test_draft_rejects_oversized_code(client, db):
    p = _first_problem(db)
    resp = client.put(f"/api/problems/{p.id}/code",
                      json={"language": "C++", "code": "x" * 200_001})
    assert resp.status_code == 422
