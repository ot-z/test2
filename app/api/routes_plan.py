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
def generate(days: int = Query(30, ge=1, le=365),
             from_date: str | None = Query(None, description="可选起始日期 YYYY-MM-DD；默认取计划配置的 start_date"),
             db: Session = Depends(get_db)):
    """预生成 N 天计划（幂等，已存在的日期整日跳过）。"""
    return plan.generate(db, days=days, from_date=parse_date(from_date))


@router.post("/regenerate")
def regenerate(days: int = Query(30, ge=1, le=365),
               from_date: str | None = Query(None, description="可选起始日期 YYYY-MM-DD；默认今天"),
               db: Session = Depends(get_db)):
    """重建未来 N 天计划：窗口从今天起算，已有日期按当前配置补齐任务缺口。

    与 /generate 的区别见 plan.regenerate 文档：不删除任何已有任务，
    队列耗尽只标记「题单不足」（FR-10）。
    """
    return plan.regenerate(db, days=days, from_date=parse_date(from_date))


@router.post("/refill-queue")
def refill_queue(limit: int = Query(500, ge=1, le=2000), db: Session = Depends(get_db)):
    """题单耗尽时，由用户显式点击把题库中未排过的题补入激活题单。"""
    return plan.refill_queue(db, limit=limit)


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
