"""首次启动种子数据装配（幂等）。

只写入「参考配置」与「题目元信息」，不伪造任何个人学习历史。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import CONFIG_DIR, DEFAULT_PLAN_CONFIG, SYLLABUS_DIR
from .db import _utcnow
from .models import (
    KnowledgeNode, PlanConfig, Problem, ProblemKnowledge, ProblemList,
    ProblemListItem, Setting, Syllabus, SyllabusItem, TagMapping,
)
from .seed_data import KNOWLEDGE_TREE, STARTER_PROBLEMS


def _node_index(db: Session) -> dict[str, int]:
    return {n.name: n.id for n in db.scalars(select(KnowledgeNode)).all()}


def seed_knowledge_tree(db: Session) -> int:
    if db.scalar(select(func.count()).select_from(KnowledgeNode)):
        return 0
    count = 0

    def add(items, parent_id, inherited_category):
        nonlocal count
        for pos, (name, children) in enumerate(items):
            node = KnowledgeNode(
                name=name, parent_id=parent_id, category=inherited_category,
                sort_order=pos, description="",
            )
            db.add(node)
            db.flush()
            count += 1
            if children:
                add(children, node.id, inherited_category)

    for pos, (name, category, children) in enumerate(KNOWLEDGE_TREE):
        root = KnowledgeNode(name=name, parent_id=None, category=category, sort_order=pos, description="")
        db.add(root)
        db.flush()
        count += 1
        add(children, root.id, category)
    db.commit()
    return count


def seed_tag_mappings(db: Session) -> int:
    path: Path = CONFIG_DIR / "tag_mappings.yaml"
    if not path.exists() or db.scalar(select(func.count()).select_from(TagMapping)):
        return 0
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    nodes = _node_index(db)
    count = 0
    for item in data.get("mappings", []):
        node_id = nodes.get(item.get("node"))
        if not node_id:
            continue
        row = TagMapping(source=item.get("source", "*"), external_tag=item["tag"], node_id=node_id)
        # 同 tag 映射到不同节点是允许的，仅避免完全重复
        exists = db.scalar(select(func.count()).select_from(TagMapping).where(
            TagMapping.source == row.source,
            TagMapping.external_tag == row.external_tag,
            TagMapping.node_id == row.node_id,
        ))
        if not exists:
            db.add(row)
            count += 1
    db.commit()
    return count


def seed_syllabi(db: Session) -> int:
    if db.scalar(select(func.count()).select_from(Syllabus)):
        return 0
    nodes = _node_index(db)
    count = 0
    for path in sorted(SYLLABUS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        syllabus = Syllabus(
            name=data["name"], track=data.get("track", "软件赛"),
            group_name=data.get("group_name", "大学 B 组"), version=data["version"],
            source_url=data.get("source_url", ""), description=data.get("description", ""),
            effective_date=date.fromisoformat(data["effective_date"]) if data.get("effective_date") else None,
            is_active=True,
        )
        db.add(syllabus)
        db.flush()
        count += 1
        for item in data.get("items", []):
            node_id = nodes.get(item["node"])
            if not node_id:
                continue
            db.add(SyllabusItem(
                syllabus_id=syllabus.id, node_id=node_id,
                difficulty_min=item.get("difficulty_min", 1),
                difficulty_max=item.get("difficulty_max", 5),
                priority=item.get("priority", 5),
                coefficient=item.get("coefficient", 5),
            ))
    db.commit()
    return count


def seed_problems(db: Session) -> int:
    if db.scalar(select(func.count()).select_from(Problem)):
        return 0
    nodes = _node_index(db)
    count = 0
    for item in STARTER_PROBLEMS:
        problem = Problem(
            source=item["source"], source_key=item["source_key"], title=item["title"],
            url=item["url"], difficulty_raw=item["difficulty_raw"],
            difficulty_level=item["difficulty_level"], external_tags=list(item["external_tags"]),
            summary=item.get("summary", ""), is_active=True, manual_edited=False,
            sync_status="seed",
        )
        db.add(problem)
        db.flush()
        count += 1
        for name in dict.fromkeys(item.get("nodes", [])):
            node_id = nodes.get(name)
            if node_id:
                db.add(ProblemKnowledge(
                    problem_id=problem.id, node_id=node_id,
                    confidence="manual", verified=True,
                ))
    db.commit()
    return count


def seed_plan_config(db: Session) -> int:
    if db.scalar(select(func.count()).select_from(PlanConfig)):
        return 0
    cfg = PlanConfig(
        start_date=date.today(),
        **{k: v for k, v in DEFAULT_PLAN_CONFIG.items() if k in (
            "name", "new_per_day", "review_per_day", "review_delay_days",
            "review_repeat", "selection_strategy", "review_mode",
            "rest_weekdays", "pause_ranges",
        )},
        is_active=True,
    )
    db.add(cfg)
    db.commit()
    return 1


def seed_starter_list(db: Session) -> int:
    """创建一份已确认的初始题单，保证首次打开就有新题可刷（仍可在界面重新生成/调整）。"""
    if db.scalar(select(func.count()).select_from(ProblemList).where(ProblemList.is_active.is_(True))):
        return 0
    problems = db.scalars(
        select(Problem).where(Problem.is_active.is_(True)).order_by(
            Problem.difficulty_level.asc(), Problem.source_key.asc()
        )
    ).all()
    if not problems:
        return 0
    plist = ProblemList(
        name="初始题单（按难度排序，可重新生成）", status="confirmed",
        is_active=True, note="首次启动自动创建，仅含题目元信息与外链。",
        confirmed_at=_utcnow(),
    )
    db.add(plist)
    db.flush()
    for pos, problem in enumerate(problems):
        db.add(ProblemListItem(list_id=plist.id, problem_id=problem.id, position=pos, consumed=False))
    db.commit()
    return 1


def seed_settings(db: Session) -> int:
    if db.scalar(select(func.count()).select_from(Setting)):
        return 0
    from .services.settings import put
    put(db, "app", {"ui": {"theme": "light", "page_size": 20},
                    "sync": {"codeforces_enabled": True, "atcoder_enabled": False, "request_interval_seconds": 1.2},
                    "export": {"default_format": "md", "include_ai": True, "include_code": True}})
    return 1


def seed_all(db: Session) -> dict:
    """按顺序装配，返回各类新增数量。"""
    return {
        "knowledge_nodes": seed_knowledge_tree(db),
        "tag_mappings": seed_tag_mappings(db),
        "syllabi": seed_syllabi(db),
        "problems": seed_problems(db),
        "plan_configs": seed_plan_config(db),
        "problem_lists": seed_starter_list(db),
        "settings": seed_settings(db),
    }
