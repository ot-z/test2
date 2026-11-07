"""全局配置：路径、环境变量、常量。

密钥只从 .env.local / .env 或环境变量读取，绝不写入代码、日志或 git。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
EXPORT_DIR = ROOT / "exports"
BACKUP_DIR = ROOT / "backups"
CONFIG_DIR = ROOT / "config"
SYLLABUS_DIR = CONFIG_DIR / "syllabus"
ENV_LOCAL = ROOT / ".env.local"

for _dir in (DATA_DIR, EXPORT_DIR, BACKUP_DIR, CONFIG_DIR, SYLLABUS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# .env.local 优先于 .env；已存在的进程环境变量优先（便于测试注入）
load_dotenv(ENV_LOCAL, override=False)
load_dotenv(ROOT / ".env", override=False)


def env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)


APP_HOST = env("APP_HOST", "127.0.0.1") or "127.0.0.1"
APP_PORT = int(env("APP_PORT", "8765") or "8765")
APP_VERSION = "1.1.0"

_db_path = Path(env("DATABASE_PATH", "data/quiz.db") or "data/quiz.db")
if not _db_path.is_absolute():
    _db_path = ROOT / _db_path
DATABASE_PATH = _db_path
DATABASE_URL = env("QUIZ_DATABASE_URL") or f"sqlite:///{DATABASE_PATH.as_posix()}"

DEEPSEEK_BASE_URL = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com") or "https://api.deepseek.com"
DEEPSEEK_MODEL = env("DEEPSEEK_MODEL", "deepseek-flash") or "deepseek-flash"

# 计划参数默认值（D-05 / D-06 / D-07 / D-08）
DEFAULT_PLAN_CONFIG = {
    "name": "默认计划",
    "new_per_day": 1,
    "review_per_day": 1,
    "review_delay_days": 7,
    "review_repeat": 1,
    "selection_strategy": "earliest_due",
    "review_mode": "fixed",
    "rest_weekdays": [],
    "pause_ranges": [],
}

# AI 设置默认值（FR-17：模型 ID 可自定义，不硬编码枚举）
MODEL_PRESETS = [
    {"id": "deepseek-flash", "label": "deepseek-flash（默认·最省）", "capabilities": ["temperature", "max_tokens", "stream"]},
    {"id": "deepseek-v4-pro", "label": "deepseek-v4-pro（强推理）", "capabilities": ["temperature", "max_tokens", "stream", "thinking", "reasoning_effort"]},
]
DEPRECATED_MODELS = {"deepseek-chat", "deepseek-reasoner"}
DEFAULT_AI_SETTINGS = {
    "model": DEEPSEEK_MODEL,
    "custom_model": "",
    "base_url": DEEPSEEK_BASE_URL,
    "temperature": 0.3,
    "max_tokens": 2048,
    "thinking": False,
    "reasoning_effort": "medium",
    "stream": True,
    "timeout_seconds": 60,
    "max_retries": 2,
}

DEFAULT_APP_SETTINGS = {
    "ui": {"theme": "light", "page_size": 20},
    "sync": {"codeforces_enabled": True, "atcoder_enabled": False, "request_interval_seconds": 1.2},
    "export": {"default_format": "md", "include_ai": True, "include_code": True},
}

# 难度 5 档
DIFFICULTY_LABELS = {1: "入门", 2: "基础", 3: "进阶", 4: "提高", 5: "竞赛"}
SOURCES = {
    "codeforces": "Codeforces",
    "luogu": "洛谷",
    "leetcode": "力扣",
    "atcoder": "AtCoder",
    "lanqiao": "蓝桥杯",
    "other": "其他",
}

TASK_STATUSES = ["pending", "learning", "learned", "ac", "reviewed"]
TASK_STATUS_LABELS = {
    "pending": "待开始",
    "learning": "学习中",
    "learned": "已学题解",
    "ac": "独立通过",
    "reviewed": "已完成复习",
    "skipped": "已跳过",
    "overdue": "已逾期",
}
