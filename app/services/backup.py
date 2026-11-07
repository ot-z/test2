"""每日 SQLite 自动备份（NFR-03 / NFR-04）。"""
from __future__ import annotations

import sqlite3
from datetime import date

from sqlalchemy.orm import Session

from ..config import BACKUP_DIR, DATABASE_PATH


def daily_backup() -> dict:
    """用 SQLite 在线备份 API 生成一致性副本；同一天只备份一次。"""
    target = BACKUP_DIR / f"quiz_{date.today().isoformat()}.db"
    if target.exists():
        return {"skipped": True, "reason": "今日已备份", "path": str(target)}
    if not DATABASE_PATH.exists():
        return {"skipped": True, "reason": "数据库尚未创建", "path": ""}
    try:
        src = sqlite3.connect(str(DATABASE_PATH))
        dst = sqlite3.connect(str(target))
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
    except sqlite3.Error as exc:
        return {"skipped": True, "reason": f"备份失败：{exc}", "path": ""}
    return {"skipped": False, "path": str(target), "size": target.stat().st_size}


def list_backups() -> list[dict]:
    return [
        {"name": p.name, "size": p.stat().st_size, "path": str(p)}
        for p in sorted(BACKUP_DIR.glob("quiz_*.db"), reverse=True)
    ]
