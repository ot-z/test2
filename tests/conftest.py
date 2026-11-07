"""验收测试公共夹具：环境隔离 + 数据库/客户端。

关键约束：本文件必须在**任何 app 模块被 import 之前**把 QUIZ_DATABASE_URL /
DATABASE_PATH 指向 pytest 临时目录，否则 app.config / app.db 会连到真实数据库。

注意：应用原始状态下 ``import app.main`` 会抛
``ImportError: cannot import name 'seed' from 'app.services'``（见最终报告缺陷清单）。
因此这里按需求文档允许的做法「直接构造 FastAPI 实例」：只挂真实的
``app.api.api_router`` 并复刻 main.py 的异常处理器，不依赖 app.main 模块。
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from pathlib import Path

# ------------------------------------------------------------------ 环境隔离（必须最先执行）
# 用 mkdtemp 而非自动清理的 TemporaryDirectory：Windows 上 SQLite/WAL 句柄在
# 解释器退出时可能尚未释放，自动清理会抛错；测试临时目录留在系统 temp 即可。
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="quiz_acceptance_"))
_DB_FILE = _TMP_ROOT / "test.db"
_EXPORT_DIR = _TMP_ROOT / "exports"
_BACKUP_DIR = _TMP_ROOT / "backups"
for _d in (_EXPORT_DIR, _BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

os.environ["QUIZ_DATABASE_URL"] = f"sqlite:///{_DB_FILE.as_posix()}"
os.environ["DATABASE_PATH"] = str(_DB_FILE)
# 保证 AI 基线为 not_configured；config.py 的 load_dotenv(override=False)
# 遇到已存在的环境变量会跳过，因此不会被 .env / .env.local 覆盖。
os.environ["DEEPSEEK_API_KEY"] = ""

# ------------------------------------------------------------------ 现在才可以 import app
import pytest  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import seed  # noqa: E402  （seed 在 app 包根，不在 app.services）
from app.api import api_router  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.services import backup, exporter, settings as settings_svc  # noqa: E402

# 把会写文件的目录改到临时目录，避免污染项目的 exports/ backups/ .env.local
exporter.EXPORT_DIR = _EXPORT_DIR
backup.BACKUP_DIR = _BACKUP_DIR
backup.DATABASE_PATH = _DB_FILE
settings_svc.ENV_LOCAL = _TMP_ROOT / ".env.local"


# ------------------------------------------------------------------ 自建测试 app（不 import app.main）
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9]{8,}")


def _scrub(text) -> str:
    return _SECRET_RE.sub("sk-***REDACTED***", str(text))


fastapi_app = FastAPI(title="刷题学习助手 API（验收测试实例）")
fastapi_app.include_router(api_router)


@fastapi_app.exception_handler(AppError)
async def _handle_app_error(_request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": _scrub(exc.message)}},
    )


@fastapi_app.exception_handler(RequestValidationError)
async def _handle_validation(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR",
                           "message": "请求参数不合法",
                           "detail": _scrub(str(exc.errors()))}},
    )


@fastapi_app.exception_handler(Exception)
async def _handle_unexpected(_request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR",
                           "message": "服务内部错误，请查看控制台日志（已脱敏）"}},
    )


def wipe_all_tables() -> None:
    """删除临时库中所有表（含 alembic_version），用于函数级隔离与迁移演练。"""
    engine.dispose()
    conn = sqlite3.connect(str(_DB_FILE))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for name in names:
            conn.execute(f'DROP TABLE IF EXISTS "{name}"')
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _isolated_schema():
    """每个测试前重建干净 schema（函数级数据隔离）。"""
    wipe_all_tables()
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def wipe_db():
    """把 wipe_all_tables 暴露给需要做迁移演练的测试。"""
    return wipe_all_tables


@pytest.fixture
def db():
    """一个干净库 + 一次幂等种子装配（知识点树 / 题库 / 配置 / 初始题单）。"""
    session = SessionLocal()
    try:
        seed.seed_all(session)
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    """自建 FastAPI 实例的 TestClient（真实 api_router），无 lifespan 装配副作用。"""
    with TestClient(fastapi_app) as c:
        yield c
