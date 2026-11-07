"""学习记录、笔记、历史与统计接口（FR-14 / FR-15 / FR-16）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import records

router = APIRouter(prefix="/api", tags=["记录与笔记"])


@router.get("/records")
def list_records(problem_id: int | None = None, plan_item_id: int | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 db: Session = Depends(get_db)):
    return {"items": records.list_records(db, {
        "problem_id": problem_id, "plan_item_id": plan_item_id,
        "date_from": date_from, "date_to": date_to,
    })}


@router.post("/records")
def upsert_record(payload: dict = Body(...), db: Session = Depends(get_db)):
    return records.upsert_record(db, payload)


@router.get("/notes")
def list_notes(problem_id: int | None = None, keyword: str | None = None,
               db: Session = Depends(get_db)):
    return {"items": records.list_notes(db, {"problem_id": problem_id, "keyword": keyword})}


@router.post("/notes")
def create_note(payload: dict = Body(...), db: Session = Depends(get_db)):
    return records.create_note(db, payload)


@router.patch("/notes/{note_id}")
def update_note(note_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    return records.update_note(db, note_id, payload)


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    return records.delete_note(db, note_id)


@router.get("/stats/overview")
def stats(db: Session = Depends(get_db)):
    return records.stats_overview(db)


@router.get("/history")
def history(date_from: str | None = None, date_to: str | None = None,
            view: str = Query("day"), db: Session = Depends(get_db)):
    return records.history(db, {"date_from": date_from, "date_to": date_to, "view": view})
