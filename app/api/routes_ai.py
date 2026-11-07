"""数据源同步与 AI 智能体接口（FR-04 / FR-17 ～ FR-21）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import ai as ai_svc, syncer

router = APIRouter(prefix="/api", tags=["同步与AI"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("/sync/{source}")
def sync(source: str, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    return syncer.sync(db, source, payload.get("limit"))


@router.get("/sync/logs")
def sync_logs(limit: int = 20, db: Session = Depends(get_db)):
    return {"items": syncer.logs(db, limit)}


@router.get("/sync/last")
def sync_last(db: Session = Depends(get_db)):
    return {"last": syncer.last_sync(db)}


@router.post("/ai/analyze")
async def analyze(payload: dict = Body(...)):
    return StreamingResponse(ai_svc.analyze_stream(payload),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/ai/debug")
async def debug(payload: dict = Body(...)):
    return StreamingResponse(ai_svc.debug_stream(payload),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/ai/suggest-tags")
def suggest_tags(payload: dict = Body(...), db: Session = Depends(get_db)):
    return ai_svc.suggest_tags(db, payload)


@router.get("/ai/usage")
def ai_usage(db: Session = Depends(get_db)):
    return ai_svc.usage(db)


@router.post("/ai/test")
def ai_test(db: Session = Depends(get_db)):
    return ai_svc.test_connection(db)
