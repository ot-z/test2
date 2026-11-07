"""应用入口：FastAPI + Jinja2 页面 + 启动装配。

- 仅监听 127.0.0.1（由启动脚本传入）；
- 启动时自动执行 Alembic 升级 → 种子数据 → 补齐今日计划 → 每日备份；
- 页面只消费 /api 的 JSON，未来换成 React 无需改后端（SRS 3.1）。
"""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, seed
from .api import api_router
from .config import ROOT
from .db import Base, SessionLocal, engine
from .errors import AppError
from .services import backup, plan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
)
logger = logging.getLogger("quiz-assistant")

WEB_DIR = Path(__file__).resolve().parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))

_SECRET_RE = re.compile(r"sk-[A-Za-z0-9]{8,}")


def _scrub(text: str) -> str:
    return _SECRET_RE.sub("sk-***REDACTED***", str(text))


def init_database() -> dict:
    """升级 schema → 装配参考数据 → 保证今日有计划 → 每日备份。"""
    alembic_cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    migration_result = "upgraded"
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as exc:  # noqa: BLE001 —— 单机应用不能因为迁移问题直接起不来
        logger.warning("Alembic 升级失败，回退到 create_all：%s", _scrub(exc))
        Base.metadata.create_all(engine)
        migration_result = "fallback-create_all"

    db = SessionLocal()
    try:
        seeded = seed.seed_all(db)
        today = plan.ensure_today(db)
    finally:
        db.close()
    backup_result = backup.daily_backup()
    logger.info("启动装配完成：migration=%s seed=%s today_items=%d backup=%s",
                migration_result, seeded, len(today.get("items", [])), backup_result.get("path") or backup_result.get("reason"))
    return {"migration": migration_result, "seed": seeded, "backup": backup_result}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    yield


app = FastAPI(
    title="刷题学习助手 API",
    version=__version__,
    description="本地单机的刷题计划、复习调度、笔记与复盘文档服务",
    lifespan=lifespan,
)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.exception_handler(AppError)
async def handle_app_error(_request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": _scrub(exc.message)}},
    )


@app.exception_handler(RequestValidationError)
async def handle_validation(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR",
                           "message": "请求参数不合法",
                           "detail": _scrub(str(exc.errors()))}},
    )


@app.exception_handler(Exception)
async def handle_unexpected(_request: Request, exc: Exception):
    logger.exception("未处理异常：%s", _scrub(exc))
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR",
                           "message": "服务内部错误，请查看控制台日志（已脱敏）"}},
    )


# ---------------------------------------------------------------- 页面路由

PAGES = {
    "today": "今日任务",
    "problems": "题库目录",
    "knowledge": "知识点与标签",
    "syllabus": "大纲与题单",
    "history": "历史与统计",
    "notes": "思路笔记",
    "ai": "AI 助手",
    "export": "导出复盘",
    "settings": "设置",
}


def _render(request: Request, page: str, **context):
    return TEMPLATES.TemplateResponse(
        request=request,
        name=f"{page}.html",
        context={
            "page": page,
            "page_title": PAGES[page],
            "pages": PAGES,
            "version": __version__,
            **context,
        },
    )


@app.get("/", include_in_schema=False)
def page_today(request: Request):
    return _render(request, "today")


@app.get("/problems", include_in_schema=False)
def page_problems(request: Request):
    return _render(request, "problems")


@app.get("/knowledge", include_in_schema=False)
def page_knowledge(request: Request):
    return _render(request, "knowledge")


@app.get("/syllabus", include_in_schema=False)
def page_syllabus(request: Request):
    return _render(request, "syllabus")


@app.get("/history", include_in_schema=False)
def page_history(request: Request):
    return _render(request, "history")


@app.get("/notes", include_in_schema=False)
def page_notes(request: Request):
    return _render(request, "notes")


@app.get("/ai", include_in_schema=False)
def page_ai(request: Request):
    return _render(request, "ai")


@app.get("/export", include_in_schema=False)
def page_export(request: Request):
    return _render(request, "export")


@app.get("/settings", include_in_schema=False)
def page_settings(request: Request):
    return _render(request, "settings")
