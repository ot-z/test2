"""知识点树、标签映射、大纲与候选题单（FR-05 / FR-06 / FR-07 / FR-08）。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import NotFound, ValidationError
from ..models import (
    DailyPlanItem, KnowledgeNode, Problem, ProblemKnowledge, ProblemList,
    ProblemListItem, Syllabus, SyllabusItem, TagMapping,
)
from ..utils import model_dict, parse_date

CATEGORIES = {"knowledge": "知识点", "data_structure": "数据结构", "algorithm": "算法"}
LEARNED_STATUSES = ["learned", "ac", "reviewed"]


def _problem_counts(db: Session) -> dict[int, int]:
    rows = db.execute(
        select(ProblemKnowledge.node_id, func.count())
        .where(ProblemKnowledge.verified.is_(True))
        .group_by(ProblemKnowledge.node_id)
    ).all()
    return {int(k): int(v) for k, v in rows}


def tree(db: Session) -> list[dict]:
    nodes = db.scalars(select(KnowledgeNode).order_by(KnowledgeNode.sort_order, KnowledgeNode.id)).all()
    counts = _problem_counts(db)
    by_parent: dict[int | None, list] = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)

    def build(parent_id):
        return [{
            "id": n.id, "name": n.name, "category": n.category,
            "category_label": CATEGORIES.get(n.category, n.category),
            "sort_order": n.sort_order, "description": n.description,
            "problem_count": counts.get(n.id, 0),
            "children": build(n.id),
        } for n in by_parent.get(parent_id, [])]

    return build(None)


def flat_nodes(db: Session) -> list[dict]:
    return [
        model_dict(n) | {"category_label": CATEGORIES.get(n.category, n.category)}
        for n in db.scalars(select(KnowledgeNode).order_by(KnowledgeNode.id)).all()
    ]


def create_node(db: Session, payload: dict) -> dict:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValidationError("节点名称必填")
    category = payload.get("category", "knowledge")
    if category not in CATEGORIES:
        raise ValidationError("category 不合法")
    parent_id = payload.get("parent_id")
    if parent_id:
        parent = db.get(KnowledgeNode, int(parent_id))
        if parent is None:
            raise NotFound("父节点不存在")
    dup = db.scalar(select(KnowledgeNode).where(
        KnowledgeNode.name == name, KnowledgeNode.parent_id == (int(parent_id) if parent_id else None)
    ))
    if dup:
        raise ValidationError("同级下已存在同名节点")
    node = KnowledgeNode(
        name=name, category=category,
        parent_id=int(parent_id) if parent_id else None,
        sort_order=int(payload.get("sort_order") or 0),
        description=str(payload.get("description") or ""),
    )
    db.add(node)
    db.commit()
    return model_dict(node)


def update_node(db: Session, node_id: int, payload: dict) -> dict:
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise NotFound("节点不存在")
    for key in ("name", "category", "sort_order", "description", "parent_id"):
        if key in payload:
            val = payload[key]
            if key == "parent_id":
                val = int(val) if val else None
                if val == node_id:
                    raise ValidationError("不能把节点设为自己的父节点")
            if key == "category" and val not in CATEGORIES:
                raise ValidationError("category 不合法")
            setattr(node, key, val)
    db.commit()
    return model_dict(node)


def delete_node(db: Session, node_id: int, force: bool = False) -> dict:
    """默认阻止删除含子节点的父节点（FR-05 默认策略）；force=True 时子节点上移到父级。"""
    node = db.get(KnowledgeNode, node_id)
    if node is None:
        raise NotFound("节点不存在")
    children = db.scalars(select(KnowledgeNode).where(KnowledgeNode.parent_id == node_id)).all()
    if children and not force:
        raise ValidationError("该节点含子节点，默认阻止删除；可先迁移子节点或传 force=true 上移")
    if children:
        for child in children:
            child.parent_id = node.parent_id
        db.flush()
    # 关联数据一并清理，避免悬挂引用
    for link in db.scalars(select(ProblemKnowledge).where(ProblemKnowledge.node_id == node_id)).all():
        db.delete(link)
    for mapping in db.scalars(select(TagMapping).where(TagMapping.node_id == node_id)).all():
        db.delete(mapping)
    for item in db.scalars(select(SyllabusItem).where(SyllabusItem.node_id == node_id)).all():
        db.delete(item)
    db.delete(node)
    db.commit()
    return {"id": node_id, "deleted": True, "children_moved": len(children)}


# ---------------------------------------------------------------- 标签映射

def list_tag_mappings(db: Session) -> list[dict]:
    nodes = {n.id: n.name for n in db.scalars(select(KnowledgeNode)).all()}
    return [
        model_dict(m) | {"node_name": nodes.get(m.node_id, "?")}
        for m in db.scalars(select(TagMapping).order_by(TagMapping.source, TagMapping.external_tag)).all()
    ]


def put_tag_mapping(db: Session, payload: dict) -> dict:
    source = str(payload.get("source") or "*")
    tag = str(payload.get("external_tag") or "").strip().lower()
    node_id = int(payload.get("node_id") or 0)
    if not tag or not db.get(KnowledgeNode, node_id):
        raise ValidationError("external_tag 与有效的 node_id 必填")
    dup = db.scalar(select(TagMapping).where(
        TagMapping.source == source, TagMapping.external_tag == tag, TagMapping.node_id == node_id
    ))
    if dup:
        return model_dict(dup)
    row = TagMapping(source=source, external_tag=tag, node_id=node_id)
    db.add(row)
    db.commit()
    return model_dict(row)


def delete_tag_mapping(db: Session, mapping_id: int) -> dict:
    row = db.get(TagMapping, mapping_id)
    if row is None:
        raise NotFound("映射不存在")
    db.delete(row)
    db.commit()
    return {"id": mapping_id, "deleted": True}


# ---------------------------------------------------------------- 大纲

def list_syllabi(db: Session) -> list[dict]:
    return [syllabus_detail(db, s) for s in db.scalars(
        select(Syllabus).order_by(Syllabus.effective_date.desc(), Syllabus.id.desc())
    ).all()]


def syllabus_detail(db: Session, syllabus: Syllabus) -> dict:
    out = model_dict(syllabus)
    nodes = {n.id: n.name for n in db.scalars(select(KnowledgeNode)).all()}
    out["items"] = [
        model_dict(i) | {"node_name": nodes.get(i.node_id, "?")}
        for i in db.scalars(select(SyllabusItem).where(SyllabusItem.syllabus_id == syllabus.id)
                           .order_by(SyllabusItem.priority.desc())).all()
    ]
    return out


def create_syllabus(db: Session, payload: dict) -> dict:
    name = str(payload.get("name") or "").strip()
    version = str(payload.get("version") or "").strip()
    if not name or not version:
        raise ValidationError("name 与 version 必填")
    if db.scalar(select(Syllabus).where(Syllabus.name == name, Syllabus.version == version)):
        raise ValidationError("同名同版本大纲已存在")
    syllabus = Syllabus(
        name=name, version=version,
        track=str(payload.get("track") or "软件赛"),
        group_name=str(payload.get("group_name") or "大学 B 组"),
        source_url=str(payload.get("source_url") or ""),
        description=str(payload.get("description") or ""),
        effective_date=parse_date(payload.get("effective_date")) or date.today(),
        is_active=bool(payload.get("is_active", True)),
    )
    db.add(syllabus)
    db.flush()
    for item in payload.get("items", []):
        node = db.scalar(select(KnowledgeNode).where(KnowledgeNode.name == item.get("node")))
        if not node and item.get("node_id"):
            node = db.get(KnowledgeNode, int(item["node_id"]))
        if not node:
            continue
        db.add(SyllabusItem(
            syllabus_id=syllabus.id, node_id=node.id,
            difficulty_min=int(item.get("difficulty_min") or 1),
            difficulty_max=int(item.get("difficulty_max") or 5),
            priority=int(item.get("priority") or 5),
            coefficient=int(item.get("coefficient") or 5),
        ))
    db.commit()
    return syllabus_detail(db, syllabus)


def _descendants(db: Session, node_ids: list[int]) -> set[int]:
    all_nodes = db.execute(select(KnowledgeNode.id, KnowledgeNode.parent_id)).all()
    children: dict[int, list[int]] = {}
    for nid, pid in all_nodes:
        if pid is not None:
            children.setdefault(pid, []).append(nid)
    result = set(node_ids)
    stack = list(node_ids)
    while stack:
        cur = stack.pop()
        for child in children.get(cur, []):
            if child not in result:
                result.add(child)
                stack.append(child)
    return result


def generate_candidate_list(db: Session, payload: dict) -> dict:
    """FR-08：大纲版本 + 难度区间 → 未学题目 → 按优先级/系数排序 → draft 题单。"""
    syllabus_id = int(payload.get("syllabus_id") or 0)
    syllabus = db.get(Syllabus, syllabus_id) if syllabus_id else db.scalar(
        select(Syllabus).where(Syllabus.is_active.is_(True)).order_by(Syllabus.id.desc())
    )
    if syllabus is None:
        raise NotFound("大纲不存在")
    d_min = int(payload.get("difficulty_min") or 1)
    d_max = int(payload.get("difficulty_max") or 5)
    limit = max(1, min(int(payload.get("limit") or 100), 1000))

    items = db.scalars(select(SyllabusItem).where(SyllabusItem.syllabus_id == syllabus.id)).all()
    if not items:
        raise ValidationError("该大纲没有知识点条目")
    node_ids = _descendants(db, [i.node_id for i in items])
    priority: dict[int, tuple[int, int]] = {}
    for item in items:
        for nid in _descendants(db, [item.node_id]):
            # 同一节点被多个大纲条目覆盖时，取优先级更高者
            if nid not in priority or item.priority > priority[nid][0]:
                priority[nid] = (item.priority, item.coefficient)

    learned_ids = set(db.scalars(
        select(DailyPlanItem.problem_id).where(DailyPlanItem.status.in_(LEARNED_STATUSES))
    ).all())
    linked = db.scalars(
        select(ProblemKnowledge).where(
            ProblemKnowledge.node_id.in_(node_ids),
            ProblemKnowledge.verified.is_(True),  # AI 建议未确认不参与组题
        )
    ).all()
    candidates: dict[int, tuple[int, int]] = {}
    for link in linked:
        prio, coef = priority.get(link.node_id, (5, 5))
        current = candidates.get(link.problem_id)
        if current is None or (prio, -coef) > (current[0], -current[1]):
            candidates[link.problem_id] = (prio, coef)

    problem_ids = [pid for pid in candidates if pid not in learned_ids]
    problems = {p.id: p for p in db.scalars(
        select(Problem).where(
            Problem.id.in_(problem_ids),
            Problem.is_active.is_(True),
            Problem.difficulty_level >= d_min,
            Problem.difficulty_level <= d_max,
        )
    ).all()} if problem_ids else {}
    ordered = sorted(
        problems.values(),
        key=lambda p: (-candidates[p.id][0], candidates[p.id][1], p.difficulty_level, p.source_key),
    )[:limit]

    plist = ProblemList(
        syllabus_id=syllabus.id,
        name=str(payload.get("name") or f"{syllabus.name} {syllabus.version} · 候选题单"),
        status="draft", is_active=False,
        note=f"难度 {d_min}-{d_max}；候选 {len(ordered)} 题；确认后成为新题队列",
    )
    db.add(plist)
    db.flush()
    for pos, problem in enumerate(ordered):
        db.add(ProblemListItem(list_id=plist.id, problem_id=problem.id, position=pos))
    db.commit()
    return list_detail(db, plist.id)


def list_detail(db: Session, list_id: int) -> dict:
    plist = db.get(ProblemList, list_id)
    if plist is None:
        raise NotFound("题单不存在")
    out = model_dict(plist)
    rows = db.scalars(
        select(ProblemListItem).where(ProblemListItem.list_id == list_id)
        .order_by(ProblemListItem.position, ProblemListItem.id)
    ).all()
    out["items"] = []
    for row in rows:
        problem = db.get(Problem, row.problem_id)
        out["items"].append({
            "id": row.id, "position": row.position, "consumed": row.consumed,
            "problem": model_dict(problem) if problem else None,
        })
    out["count"] = len(rows)
    return out


def list_lists(db: Session) -> list[dict]:
    out = []
    for plist in db.scalars(select(ProblemList).order_by(ProblemList.id.desc())).all():
        count = int(db.scalar(select(func.count()).select_from(ProblemListItem)
                              .where(ProblemListItem.list_id == plist.id)) or 0)
        remaining = int(db.scalar(select(func.count()).select_from(ProblemListItem)
                                 .where(ProblemListItem.list_id == plist.id,
                                        ProblemListItem.consumed.is_(False))) or 0)
        out.append(model_dict(plist) | {"count": count, "remaining": remaining})
    return out


def remove_list_item(db: Session, item_id: int) -> dict:
    row = db.get(ProblemListItem, item_id)
    if row is None:
        raise NotFound("题单项不存在")
    plist = db.get(ProblemList, row.list_id)
    if plist and plist.status == "confirmed":
        raise ValidationError("已确认题单不可剔除；请重新生成候选题单")
    db.delete(row)
    db.commit()
    return {"id": item_id, "deleted": True}


def reorder_list(db: Session, list_id: int, order: list[int]) -> dict:
    rows = {r.id: r for r in db.scalars(
        select(ProblemListItem).where(ProblemListItem.list_id == list_id)
    ).all()}
    if not rows:
        raise NotFound("题单为空或不存在")
    for pos, item_id in enumerate(order):
        if int(item_id) in rows:
            rows[int(item_id)].position = pos
    db.commit()
    return list_detail(db, list_id)


def confirm_list(db: Session, list_id: int) -> dict:
    plist = db.get(ProblemList, list_id)
    if plist is None:
        raise NotFound("题单不存在")
    if not db.scalar(select(func.count()).select_from(ProblemListItem)
                     .where(ProblemListItem.list_id == list_id)):
        raise ValidationError("空题单不能确认")
    for other in db.scalars(select(ProblemList).where(ProblemList.is_active.is_(True))).all():
        other.is_active = False
    plist.status = "confirmed"
    plist.is_active = True
    from ..db import _utcnow
    plist.confirmed_at = _utcnow()
    db.commit()
    return list_detail(db, list_id)


def set_active_list(db: Session, list_id: int) -> dict:
    plist = db.get(ProblemList, list_id)
    if plist is None or plist.status != "confirmed":
        raise ValidationError("只能激活已确认的题单")
    for other in db.scalars(select(ProblemList).where(ProblemList.is_active.is_(True))).all():
        other.is_active = False
    plist.is_active = True
    db.commit()
    return list_detail(db, list_id)
