"""每日计划与任务状态接口（FR-09 ～ FR-13）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import plan
from ..utils import parse_date

router = APIRouter(prefix="/api/plan", tags=["每日计划"])


@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    return plan.config_dict(plan.active_config(db))


@router.put("/config")
def put_config(payload: dict = Body(...), db: Session = Depends(get_db)):
    return plan.update_config(db, payload)


@router.post("/generate")
def generate(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    return plan.generate(db, days=days)


@router.get("/today")
def today(db: Session = Depends(get_db)):
    return plan.ensure_today(db)


@router.get("/calendar")
def calendar(date_from: str | None = None, date_to: str | None = None,
             db: Session = Depends(get_db)):
    from datetime import date, timedelta
    start = parse_date(date_from) or (date.today() - timedelta(days=30))
    end = parse_date(date_to) or date.today()
    return {"items": plan.calendar(db, start, end)}


@router.patch("/items/{item_id}")
def patch_item(item_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    status = str(payload.get("status") or "")
    score = payload.get("score")
    return plan.update_status(db, item_id, status, int(score) if score else None)
