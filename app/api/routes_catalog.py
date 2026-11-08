"""题目目录接口（FR-01 / FR-02 / FR-03 / FR-27 / FR-28）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import catalog, workbench

router = APIRouter(prefix="/api/problems", tags=["题目目录"])


@router.get("")
def list_problems(
    page: int = 1, page_size: int = 20, source: str | None = None,
    difficulty_min: int | None = None, difficulty_max: int | None = None,
    node_id: int | None = None, keyword: str | None = None,
    learned: str | None = None, sort: str = "id", include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    return catalog.list_problems(db, {
        "page": page, "page_size": page_size, "source": source,
        "difficulty_min": difficulty_min, "difficulty_max": difficulty_max,
        "node_id": node_id, "keyword": keyword, "learned": learned,
        "sort": sort, "include_inactive": include_inactive,
    })


@router.post("/import")
def import_problems(payload: dict = Body(default={}), dry_run: bool = Query(False),
                    db: Session = Depends(get_db)):
    """支持附录 C 契约 JSON、CSV、粘贴题号；dry_run=true 只预览不写库。"""
    return catalog.import_batch(db, payload, dry_run=dry_run)


@router.post("")
def create_problem(payload: dict = Body(...), db: Session = Depends(get_db)):
    return catalog.create_problem(db, payload)


@router.get("/{problem_id}")
def get_problem(problem_id: int, db: Session = Depends(get_db)):
    return catalog.get_problem(db, problem_id)


@router.patch("/{problem_id}")
def update_problem(problem_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    return catalog.update_problem(db, problem_id, payload)


@router.delete("/{problem_id}")
def deactivate_problem(problem_id: int, db: Session = Depends(get_db)):
    return catalog.deactivate_problem(db, problem_id)


@router.get("/{problem_id}/snapshots")
def list_snapshots(problem_id: int, db: Session = Depends(get_db)):
    return {"items": catalog.list_snapshots(db, problem_id)}


@router.post("/{problem_id}/snapshots")
def save_snapshot(problem_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    return catalog.save_snapshot(db, problem_id, payload)


@router.delete("/{problem_id}/snapshots/{snapshot_id}")
def delete_snapshot(problem_id: int, snapshot_id: int, db: Session = Depends(get_db)):
    return catalog.delete_snapshot(db, snapshot_id)


@router.get("/{problem_id}/workbench")
def workbench_detail(problem_id: int, db: Session = Depends(get_db)):
    """FR-31：题目工作台一次取齐题面 / 草稿 / 记录 / 笔记 / AI 历史。"""
    return workbench.detail(db, problem_id)


@router.put("/{problem_id}/code")
def save_code_draft(problem_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """保存该题目的代码草稿（一题一份，覆盖式）。"""
    return workbench.save_draft(db, problem_id, payload)
