"""题目工作台（FR-31）：单题聚合视图 + 代码草稿。

只做本地数据聚合，不发起任何外部请求：
- 题面来自用户主动保存的 problem_snapshots（仅本地，不参与导出）；
- 学习记录 / 笔记 / AI 历史沿用既有表，AI 调用本身仍由 ai_svc 落库；
- 代码草稿按题目唯一，保存与编辑都在本机 SQLite。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound, ValidationError
from ..models import (
    AiCall,
    AiMessage,
    CodeDraft,
    DailyPlanItem,
    Problem,
    ProblemSnapshot,
)
from ..utils import model_dict
from . import ability, catalog, records

LANGUAGES = ("C++", "C", "Python", "Java", "Go", "JavaScript", "其他")
MAX_CODE_LEN = 200_000
MAX_AI_CALLS = 12
MAX_SNAPSHOTS = 8

SCENE_LABELS = {
    "analyze": "思路引导",
    "debug": "代码诊断",
    "suggest_tags": "知识点标注",
}


def _snapshots(db: Session, problem_id: int) -> list[dict]:
    rows = db.scalars(
        select(ProblemSnapshot)
        .where(ProblemSnapshot.problem_id == problem_id)
        .order_by(ProblemSnapshot.captured_at.desc(), ProblemSnapshot.id.desc())
        .limit(MAX_SNAPSHOTS)
    ).all()
    return [model_dict(s) | {"content_md": s.content_md} for s in rows]


def _draft(db: Session, problem_id: int) -> dict:
    row = db.scalar(select(CodeDraft).where(CodeDraft.problem_id == problem_id))
    if row is None:
        return {"problem_id": problem_id, "language": "C++", "code": "",
                "updated_at": None, "exists": False}
    return model_dict(row) | {"exists": True}


def _ai_history(db: Session, problem_id: int) -> list[dict]:
    calls = db.scalars(
        select(AiCall).where(AiCall.problem_id == problem_id)
        .order_by(AiCall.id.desc()).limit(MAX_AI_CALLS)
    ).all()
    out = []
    for call in calls:
        answer = db.scalar(
            select(AiMessage).where(AiMessage.call_id == call.id,
                                    AiMessage.role == "assistant")
            .order_by(AiMessage.seq)
        )
        out.append(model_dict(call) | {
            "answer": answer.content if answer else "",
            "scene_label": SCENE_LABELS.get(call.scene, call.scene),
        })
    return out


def _plan_items(db: Session, problem_id: int) -> list[dict]:
    rows = db.scalars(
        select(DailyPlanItem).where(DailyPlanItem.problem_id == problem_id)
        .order_by(DailyPlanItem.id.desc()).limit(5)
    ).all()
    return [model_dict(r) for r in rows]


def detail(db: Session, problem_id: int) -> dict:
    """一次请求取齐工作台五个面板所需的数据。"""
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise NotFound(f"题目 {problem_id} 不存在")
    snaps = _snapshots(db, problem_id)
    return {
        "problem": catalog.problem_detail(db, problem),
        "snapshots": snaps,
        "latest_snapshot": snaps[0] if snaps else None,
        "draft": _draft(db, problem_id),
        "records": records.list_records(db, {"problem_id": problem_id})[:20],
        "notes": records.list_notes(db, {"problem_id": problem_id})[:20],
        "ai_calls": _ai_history(db, problem_id),
        "plan_items": _plan_items(db, problem_id),
        "ability": ability.match(db, problem_id),
        "languages": list(LANGUAGES),
    }


def save_draft(db: Session, problem_id: int, payload: dict) -> dict:
    """保存 / 更新代码草稿。题面与代码都不参与文档导出。"""
    if db.get(Problem, problem_id) is None:
        raise NotFound(f"题目 {problem_id} 不存在")
    language = str(payload.get("language") or "C++").strip()[:40] or "C++"
    code = str(payload.get("code") or "")
    if len(code) > MAX_CODE_LEN:
        raise ValidationError(f"代码超过 {MAX_CODE_LEN} 字符上限，请精简后再保存")
    row = db.scalar(select(CodeDraft).where(CodeDraft.problem_id == problem_id))
    if row is None:
        row = CodeDraft(problem_id=problem_id, language=language, code=code)
        db.add(row)
    else:
        row.language = language
        row.code = code
    db.commit()
    return model_dict(row) | {"exists": True}
