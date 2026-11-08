"""洛谷公开题面采集接口（FR-30）。

Web 端一次最多 60 题（同步执行，避免长时间挂起）；全量 / 清单模式请用
``scripts/fetch_luogu.py --all``，支持断点续跑与随时中断。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import ValidationError
from ..services import luogu_store

router = APIRouter(prefix="/api/luogu", tags=["洛谷题面采集"])

MAX_WEB_ITEMS = 60


@router.get("/status")
def luogu_status(db: Session = Depends(get_db)):
    """采集进度、题面数量、标签字典与本地文件位置。"""
    return luogu_store.status(db)


@router.post("/fetch")
def luogu_fetch(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """下载一段连续题号（或已录入的洛谷题）的题面并补齐标签/知识点/难度。"""
    mode = str(payload.get("mode") or "range")
    if mode not in ("range", "existing"):
        raise ValidationError("Web 端仅支持 range（连续题号）与 existing（已录题）模式；全量请用命令行 --all")
    try:
        start = int(payload.get("start") or 1000)
        end = payload.get("end")
        end = int(end) if end not in (None, "") else start + int(payload.get("count") or 20) - 1
    except (TypeError, ValueError) as exc:
        raise ValidationError("起止题号必须是数字") from exc
    if start < 1 or end < start:
        raise ValidationError(f"题号范围不合法（当前 {start} ~ {end}）：起始需 ≥ 1，结束需 ≥ 起始")
    if end - start + 1 > MAX_WEB_ITEMS and mode == "range":
        raise ValidationError(
            f"Web 端单次最多 {MAX_WEB_ITEMS} 题（当前 {end - start + 1} 题），请缩小范围分批下载；"
            "需要全量请改用项目里的「下载全部洛谷题面.bat」"
        )
    if payload.get("interval") is not None:
        try:
            interval = float(payload["interval"])
        except (TypeError, ValueError) as exc:
            raise ValidationError("请求间隔必须是数字") from exc
        if interval < 0.8:
            raise ValidationError(
                f"请求间隔不得低于 0.8 秒（当前 {interval} 秒），避免给站点造成压力"
            )

    params = {
        "mode": mode,
        "start": start,
        "end": end if mode == "range" else None,
        "max_items": min(int(payload.get("max_items") or MAX_WEB_ITEMS), MAX_WEB_ITEMS),
        "interval": payload.get("interval"),
        "dry_run": bool(payload.get("dry_run")),
        "refresh": bool(payload.get("refresh")),
        "force": bool(payload.get("force")),
        "retry_missing": bool(payload.get("retry_missing")),
        "save_files": bool(payload.get("save_files", True)),
        "skip_missing": not bool(payload.get("retry_missing")),
    }
    return luogu_store.fetch_batch(db, params)
