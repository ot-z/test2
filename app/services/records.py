"""学习记录、思路笔记与统计（FR-14 / FR-15 / FR-16 / P2 看板）。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..errors import NotFound, ValidationError
from ..models import (
    DailyPlan, DailyPlanItem, KnowledgeNode, Note, Problem,
    ProblemKnowledge, StudyRecord,
)
from ..utils import model_dict, parse_date


def list_records(db: Session, q: dict) -> list[dict]:
    stmt = select(StudyRecord)
    if q.get("problem_id"):
        stmt = stmt.where(StudyRecord.problem_id == int(q["problem_id"]))
    if q.get("plan_item_id"):
        stmt = stmt.where(StudyRecord.plan_item_id == int(q["plan_item_id"]))
    if q.get("date_from"):
        stmt = stmt.where(StudyRecord.created_at >= str(q["date_from"])[:10])
    if q.get("date_to"):
        stmt = stmt.where(StudyRecord.created_at <= str(q["date_to"])[:10] + " 23:59:59")
    rows = db.scalars(stmt.order_by(StudyRecord.created_at.desc(), StudyRecord.id.desc())).all()
    problems = {p.id: p.title for p in db.scalars(select(Problem)).all()}
    return [model_dict(r) | {"problem_title": problems.get(r.problem_id, "?")} for r in rows]


def upsert_record(db: Session, payload: dict) -> dict:
    problem_id = int(payload.get("problem_id") or 0)
    if not db.get(Problem, problem_id):
        raise NotFound(f"题目 {problem_id} 不存在")
    plan_item_id = payload.get("plan_item_id")
    plan_item_id = int(plan_item_id) if plan_item_id else None
    row = None
    if plan_item_id:
        row = db.scalar(select(StudyRecord).where(StudyRecord.plan_item_id == plan_item_id))
    if row is None:
        row = StudyRecord(problem_id=problem_id, plan_item_id=plan_item_id)
        db.add(row)
    for key in ("result", "language", "code_snippet", "memo"):
        if key in payload:
            setattr(row, key, str(payload[key] or ""))
    for key in ("time_spent_min", "mastery"):
        if key in payload:
            try:
                val = int(payload[key] or 0)
            except (TypeError, ValueError):
                raise ValidationError(f"{key} 必须是整数") from None
            if key == "mastery":
                val = max(1, min(5, val))
            else:
                val = max(0, val)
            setattr(row, key, val)
    db.commit()
    return model_dict(row)


# ---------------------------------------------------------------- 笔记

def list_notes(db: Session, q: dict) -> list[dict]:
    stmt = select(Note)
    if q.get("problem_id"):
        stmt = stmt.where(Note.problem_id == int(q["problem_id"]))
    if q.get("keyword"):
        kw = f"%{q['keyword']}%"
        stmt = stmt.where(or_(Note.title.like(kw), Note.content_md.like(kw)))
    rows = db.scalars(stmt.order_by(Note.updated_at.desc(), Note.id.desc())).all()
    problems = {p.id: p.title for p in db.scalars(select(Problem)).all()}
    return [model_dict(n) | {"problem_title": problems.get(n.problem_id, "?")} for n in rows]


def create_note(db: Session, payload: dict) -> dict:
    problem_id = int(payload.get("problem_id") or 0)
    if not db.get(Problem, problem_id):
        raise NotFound(f"题目 {problem_id} 不存在")
    note = Note(
        problem_id=problem_id,
        title=str(payload.get("title") or "思路总结")[:300],
        content_md=str(payload.get("content_md") or ""),
        is_auto_saved=bool(payload.get("is_auto_saved", False)),
    )
    db.add(note)
    db.commit()
    return model_dict(note)


def update_note(db: Session, note_id: int, payload: dict) -> dict:
    note = db.get(Note, note_id)
    if note is None:
        raise NotFound("笔记不存在")
    if "title" in payload:
        note.title = str(payload["title"] or "思路总结")[:300]
    if "content_md" in payload:
        note.content_md = str(payload["content_md"] or "")
    note.is_auto_saved = bool(payload.get("is_auto_saved", True))
    db.commit()
    return model_dict(note)


def delete_note(db: Session, note_id: int) -> dict:
    note = db.get(Note, note_id)
    if note is None:
        raise NotFound("笔记不存在")
    db.delete(note)
    db.commit()
    return {"id": note_id, "deleted": True}


# ---------------------------------------------------------------- 统计 / 历史

def stats_overview(db: Session) -> dict:
    problems = int(db.scalar(select(func.count()).select_from(Problem)) or 0)
    active_problems = int(db.scalar(select(func.count()).select_from(Problem)
                                    .where(Problem.is_active.is_(True))) or 0)
    learned = int(db.scalar(select(func.count(func.distinct(DailyPlanItem.problem_id)))
                            .where(DailyPlanItem.status.in_(["learned", "ac", "reviewed"]))) or 0)
    reviewed = int(db.scalar(select(func.count()).select_from(DailyPlanItem)
                             .where(DailyPlanItem.item_type == "review",
                                    DailyPlanItem.status == "reviewed")) or 0)
    total_items = int(db.scalar(select(func.count()).select_from(DailyPlanItem)) or 0)
    done_items = int(db.scalar(select(func.count()).select_from(DailyPlanItem)
                               .where(DailyPlanItem.status.in_(["learned", "ac", "reviewed"]))) or 0)
    notes = int(db.scalar(select(func.count()).select_from(Note)) or 0)
    records = int(db.scalar(select(func.count()).select_from(StudyRecord)) or 0)
    avg_mastery = db.scalar(select(func.avg(StudyRecord.mastery))) or 0
    minutes = int(db.scalar(select(func.sum(StudyRecord.time_spent_min))) or 0)

    # 连续打卡天数（有任意完成任务的连续日期）
    done_dates = set(db.scalars(
        select(DailyPlan.plan_date).join(DailyPlanItem, DailyPlanItem.plan_id == DailyPlan.id)
        .where(DailyPlanItem.status.in_(["learned", "ac", "reviewed"]))
    ).all())
    streak = 0
    cur = date.today()
    if cur not in done_dates:
        cur = cur - timedelta(days=1)
    while cur in done_dates:
        streak += 1
        cur -= timedelta(days=1)

    diff_rows = db.execute(
        select(Problem.difficulty_level, func.count())
        .where(Problem.is_active.is_(True))
        .group_by(Problem.difficulty_level)
    ).all()
    difficulty_distribution = {int(k): int(v) for k, v in diff_rows}

    knowledge_rows = db.execute(
        select(ProblemKnowledge.node_id, func.count())
        .where(ProblemKnowledge.verified.is_(True))
        .group_by(ProblemKnowledge.node_id)
    ).all()
    node_names = {n.id: n.name for n in db.scalars(select(KnowledgeNode)).all()}
    knowledge_coverage = sorted(
        [{"node": node_names.get(int(k), "?"), "count": int(v)} for k, v in knowledge_rows],
        key=lambda x: -x["count"],
    )[:20]

    return {
        "problems": problems, "active_problems": active_problems, "learned_problems": learned,
        "reviewed_items": reviewed, "total_items": total_items, "done_items": done_items,
        "completion_rate": round(done_items / total_items, 4) if total_items else 0.0,
        "notes": notes, "records": records, "avg_mastery": round(float(avg_mastery), 2),
        "total_minutes": minutes, "streak_days": streak,
        "difficulty_distribution": difficulty_distribution,
        "knowledge_coverage": knowledge_coverage,
    }


def history(db: Session, q: dict) -> dict:
    date_from = parse_date(q.get("date_from")) or (date.today() - timedelta(days=30))
    date_to = parse_date(q.get("date_to")) or date.today()
    view = q.get("view", "day")
    plans = db.scalars(
        select(DailyPlan).where(DailyPlan.plan_date >= date_from, DailyPlan.plan_date <= date_to)
        .order_by(DailyPlan.plan_date)
    ).all()
    by_day: list[dict] = []
    by_problem: dict[int, dict] = defaultdict(lambda: {"count": 0, "statuses": [], "dates": []})
    by_knowledge: dict[str, int] = defaultdict(int)

    for plan in plans:
        items = db.scalars(select(DailyPlanItem).where(DailyPlanItem.plan_id == plan.id)).all()
        by_day.append({
            "date": plan.plan_date.isoformat(), "note": plan.note,
            "items": [model_dict(i) for i in items],
        })
        for item in items:
            bucket = by_problem[item.problem_id]
            bucket["count"] += 1
            bucket["statuses"].append(item.status)
            bucket["dates"].append(plan.plan_date.isoformat())

    problems = {p.id: p for p in db.scalars(select(Problem)).all()}
    links = db.scalars(select(ProblemKnowledge).where(ProblemKnowledge.verified.is_(True))).all()
    node_names = {n.id: n.name for n in db.scalars(select(KnowledgeNode)).all()}
    for link in links:
        if link.problem_id in by_problem:
            by_knowledge[node_names.get(link.node_id, "?")] += by_problem[link.problem_id]["count"]

    if view == "problem":
        result = [
            {"problem_id": pid, "title": problems.get(pid).title if problems.get(pid) else "?",
             "source": problems.get(pid).source if problems.get(pid) else "",
             "url": problems.get(pid).url if problems.get(pid) else "",
             **data}
            for pid, data in sorted(by_problem.items(), key=lambda x: -x[1]["count"])
        ]
    elif view == "knowledge":
        result = [{"node": k, "count": v} for k, v in
                  sorted(by_knowledge.items(), key=lambda x: -x[1])]
    else:
        result = by_day
    return {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "view": view, "items": result}
