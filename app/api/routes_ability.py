"""个人知识点掌握度 / 能力画像接口（FR-32）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import ability

router = APIRouter(prefix="/api/ability", tags=["能力画像"])


@router.get("/profile")
def get_profile(db: Session = Depends(get_db)):
    """全部知识点 + 掌握度 + 统计 + 全局配置。"""
    return ability.profile(db)


@router.put("/config")
def save_config(payload: dict = Body(...), db: Session = Depends(get_db)):
    return ability.save_config(db, payload)


@router.put("/levels")
def save_levels(payload: dict = Body(...), db: Session = Depends(get_db)):
    """批量保存 [{node_id, level, note}]；未提交的知识点保持原状。"""
    return ability.save_levels(db, payload.get("items") or [])


@router.get("/match/{problem_id}")
def get_match(problem_id: int, db: Session = Depends(get_db)):
    """单题能力匹配结论（工作台也通过详情接口内嵌返回）。"""
    return ability.match(db, problem_id)
