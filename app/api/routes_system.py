"""系统接口：健康检查、设置、导出、快照备份、演示数据（FR-22/24/25/26）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import __version__
from ..db import get_db
from ..errors import ValidationError
from ..models import DailyPlan, DailyPlanItem, Note, Problem, StudyRecord
from ..services import backup, demo, exporter, settings as settings_svc, syncer
from ..services import ai as ai_svc, plan as plan_svc

router = APIRouter(prefix="/api", tags=["系统"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
        db_error = ""
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = type(exc).__name__
    return {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "database": {"ok": db_ok, "error": db_error},
        "ai": {"status": ai_svc.status(db), "configured": ai_svc.status(db) != "not_configured"},
        "queue_remaining": plan_svc.queue_remaining(db),
        "problems": int(db.scalar(select(func.count()).select_from(Problem)) or 0),
        "plans": int(db.scalar(select(func.count()).select_from(DailyPlan)) or 0),
        "plan_items": int(db.scalar(select(func.count()).select_from(DailyPlanItem)) or 0),
        "notes": int(db.scalar(select(func.count()).select_from(Note)) or 0),
        "records": int(db.scalar(select(func.count()).select_from(StudyRecord)) or 0),
        "last_sync": syncer.last_sync(db),
        "last_backup": backup.list_backups()[0] if backup.list_backups() else None,
    }


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    data = settings_svc.get_all(db)
    data["ai"] = settings_svc.ai_config(db)
    return data


def _deep_merge(base: dict, patch: dict) -> dict:
    """递归合并，避免提交 app.sync 的部分字段时把整个子字典覆盖掉。"""
    out = dict(base) if isinstance(base, dict) else {}
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@router.put("/settings")
def put_settings(payload: dict = Body(...), db: Session = Depends(get_db)):
    for key in ("app", "plan_overrides"):
        if key in payload:
            merged = _deep_merge(settings_svc.get(db, key) or {}, payload[key])
            settings_svc.put(db, key, merged)
    return get_settings(db)


@router.get("/settings/ai")
def get_ai_settings(db: Session = Depends(get_db)):
    return settings_svc.ai_config(db)


@router.put("/settings/ai")
def put_ai_settings(payload: dict = Body(...), db: Session = Depends(get_db)):
    return settings_svc.save_ai_config(db, payload)


# ---------------------------------------------------------------- 导出

@router.post("/exports")
def create_export(payload: dict = Body(...), db: Session = Depends(get_db)):
    return exporter.export_document(db, payload)


@router.get("/exports")
def list_exports(db: Session = Depends(get_db)):
    return {"items": exporter.list_exports(db)}


@router.get("/exports/{export_id}/download")
def download_export(export_id: int, db: Session = Depends(get_db)):
    path, media = exporter.export_file(db, export_id)
    return FileResponse(str(path), media_type=media, filename=path.name)


# ---------------------------------------------------------------- 快照与备份

@router.post("/backup/snapshot")
def make_snapshot(db: Session = Depends(get_db)):
    return exporter.snapshot_export(db)


@router.get("/backup/snapshot")
def get_snapshot(db: Session = Depends(get_db)):
    return exporter.snapshot_export(db)


@router.post("/backup/restore")
def restore_snapshot(payload: dict = Body(...), db: Session = Depends(get_db)):
    snapshot = payload.get("snapshot") if "snapshot" in payload else payload
    return exporter.snapshot_import(db, snapshot, clear=bool(payload.get("clear", True)))


@router.get("/backup/files")
def backup_files():
    return {"items": backup.list_backups()}


@router.post("/backup/run")
def run_backup():
    return backup.daily_backup()


# ---------------------------------------------------------------- 演示数据 / 重置（显式确认）

@router.post("/system/demo")
def load_demo(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    if payload.get("confirm") is not True:
        raise ValidationError("载入演示数据会清空现有业务数据，请传 confirm=true 显式确认")
    return demo.load_demo_data(db)


@router.post("/system/reset")
def reset(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    if payload.get("confirm") is not True:
        raise ValidationError("重置会清空全部业务数据，请传 confirm=true 显式确认")
    return demo.reset_all_data(db)
