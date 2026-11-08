"""个人能力画像（FR-32）验收测试：掌握度、匹配、新知识标记与计划兼容性。"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from app.models import KnowledgeNode, Problem, ProblemKnowledge
from app.services import ability


def _node(db) -> KnowledgeNode:
    return db.scalar(select(KnowledgeNode).order_by(KnowledgeNode.id))


def _problem(db) -> Problem:
    return db.scalar(select(Problem).order_by(Problem.id))


def test_profile_defaults_without_evaluation(client, db):
    d = client.get("/api/ability/profile").json()
    assert d["stats"]["total"] > 0
    assert d["stats"]["evaluated"] == 0
    assert d["config"]["enabled"] is True
    assert d["default_level"] == 2
    assert all(i["level"] == 2 and not i["evaluated"] for i in d["items"])


def test_save_levels_and_config(client, db):
    node = _node(db)
    resp = client.put("/api/ability/levels",
                      json={"items": [{"node_id": node.id, "level": 0, "note": "完全没学过"}]})
    assert resp.status_code == 200
    d = resp.json()
    assert d["stats"]["evaluated"] == 1
    assert d["stats"]["new_count"] >= 1
    row = next(i for i in d["items"] if i["node_id"] == node.id)
    assert row["level"] == 0 and row["note"] == "完全没学过" and row["evaluated"] is True

    assert client.put("/api/ability/levels",
                      json={"items": [{"node_id": node.id, "level": 9}]}).status_code == 422
    assert client.put("/api/ability/levels",
                      json={"items": [{"node_id": 999999, "level": 3}]}).status_code == 422

    cfg = client.put("/api/ability/config",
                     json={"enabled": False, "challenge_offset": 2, "max_new_per_day": 0}).json()
    assert cfg["enabled"] is False and cfg["challenge_offset"] == 2


def test_match_marks_new_knowledge_and_prompt_explains_it(client, db):
    node, problem = _node(db), _problem(db)
    db.add(ProblemKnowledge(problem_id=problem.id, node_id=node.id,
                            confidence="manual", verified=True))
    db.commit()
    client.put("/api/ability/levels",
               json={"items": [{"node_id": node.id, "level": 0, "note": ""}]})

    m = client.get(f"/api/ability/match/{problem.id}").json()
    assert m["verdict"] == "含新知识"
    assert any(n["node_id"] == node.id for n in m["new_knowledge"])

    suffix = ability.prompt_suffix(db, problem.id)
    assert "【新知识】" in suffix
    assert "大白话定义" in suffix
    assert node.name in suffix
    assert "不要跳步" in suffix

    detail = client.get(f"/api/problems/{problem.id}/workbench").json()
    assert detail["ability"]["verdict"] == "含新知识"


def test_rank_queue_is_noop_without_profile(client, db):
    q = [SimpleNamespace(problem_id=1, position=0, id=1),
         SimpleNamespace(problem_id=2, position=1, id=2)]
    assert ability.rank_queue(db, q) is q, "未建立画像时必须保持原有选题顺序"


def test_rank_queue_prefers_difficulty_matched_problem(client, db):
    easy = Problem(source="other", source_key="ABILITY-EASY", title="易", difficulty_level=1)
    hard = Problem(source="other", source_key="ABILITY-HARD", title="难", difficulty_level=5)
    db.add_all([easy, hard])
    db.commit()
    client.put("/api/ability/levels",
               json={"items": [{"node_id": _node(db).id, "level": 3}]})
    db.expire_all()

    q = [SimpleNamespace(problem_id=hard.id, position=0, id=1),
         SimpleNamespace(problem_id=easy.id, position=1, id=2)]
    ranked = ability.rank_queue(db, q)
    assert ranked[0].problem_id == easy.id
    assert len(ranked) == 2, "重排不得增减候选题数量"


def test_match_unknown_problem_returns_404(client):
    assert client.get("/api/ability/match/999999").status_code == 404
