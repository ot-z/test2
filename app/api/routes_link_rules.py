"""题号链接规则 / 自动录入扫描采集接口（FR-29）。

只处理本地文本与规则推导，不发起任何外部网络请求。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import collector, link_rules

router = APIRouter(prefix="/api/link-rules", tags=["题号规则与采集"])


@router.get("")
def list_rules():
    return link_rules.rules_summary()


@router.get("/examples")
def examples():
    return {"items": [
        {"id": key, "title": value["title"], "source": value["source"], "text": value["text"]}
        for key, value in collector.EXAMPLES.items()
    ]}


@router.get("/derive")
def derive(source: str, key: str):
    resolved = link_rules.resolve(source, key)
    return {
        "source": source, "input": key,
        "normalized_key": link_rules.normalize_key(source, key),
        "valid": resolved is not None,
        "result": resolved,
        "check": link_rules.check(source, key, resolved["url"] if resolved else ""),
    }


@router.post("/reload")
def reload_rules():
    link_rules.reload_rules()
    return {"ok": True, "sources": list(link_rules.get_rules()),
            "message": "链接规则已重新加载"}


@router.put("/custom")
def save_custom(payload: dict = Body(...)):
    content = payload.get("yaml")
    if not isinstance(content, str) or not content.strip():
        from ..errors import ValidationError
        raise ValidationError("yaml 内容不能为空")
    return link_rules.save_custom_yaml(content)


@router.post("/preview")
def preview(payload: dict = Body(...)):
    return collector.scan_preview(payload)


@router.post("/collect")
def collect(payload: dict = Body(...), dry_run: bool = Query(True),
            db: Session = Depends(get_db)):
    return collector.collect(db, payload, dry_run=dry_run)


@router.get("/audit")
def audit(source: str | None = None, include_inactive: bool = False,
          limit: int = 500, db: Session = Depends(get_db)):
    return collector.audit(db, source=source, include_inactive=include_inactive, limit=limit)


@router.post("/repair")
def repair(payload: dict = Body(default={}), dry_run: bool = Query(True),
           db: Session = Depends(get_db)):
    return collector.repair(db, payload, dry_run=dry_run)
