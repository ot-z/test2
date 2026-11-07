"""设置与密钥管理（FR-17 / FR-25 / NFR-02）。

- 非敏感配置存 SQLite 的 settings 表；
- API Key 只存 .env.local（gitignore 排除），界面与日志一律脱敏；
- 模型 ID 允许自定义，预设仅作为下拉建议，不硬编码枚举。
"""
from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import (
    DEFAULT_AI_SETTINGS, DEFAULT_APP_SETTINGS, DEPRECATED_MODELS,
    ENV_LOCAL, MODEL_PRESETS,
)
from ..models import Setting
from ..utils import mask_secret


def _defaults() -> dict:
    return {
        "ai": deepcopy(DEFAULT_AI_SETTINGS),
        "app": deepcopy(DEFAULT_APP_SETTINGS),
        "plan_overrides": {},
    }


def get_all(db: Session) -> dict:
    result = _defaults()
    for setting in db.scalars(select(Setting)).all():
        stored = setting.value
        if setting.key in result and isinstance(result[setting.key], dict) and isinstance(stored, dict):
            result[setting.key].update(stored)
        else:
            result[setting.key] = stored
    return result


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return deepcopy(_defaults().get(key))
    return row.value


def put(db: Session, key: str, value: Any) -> Any:
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        db.add(row)
    else:
        # 强制赋新对象，避免 SQLAlchemy 对原地修改的 JSON 不追踪
        row.value = value
    db.commit()
    db.refresh(row)
    return row.value


def read_api_key() -> str:
    """优先进程环境变量，其次 .env.local（由 python-dotenv 在 config 导入时载入）。"""
    return (os.getenv("DEEPSEEK_API_KEY") or "").strip()


def write_api_key(api_key: str) -> None:
    """写入 .env.local，保留其他配置项；同时更新当前进程环境以便立即生效。"""
    api_key = (api_key or "").strip()
    lines: list[str] = []
    if ENV_LOCAL.exists():
        lines = ENV_LOCAL.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("DEEPSEEK_API_KEY="):
            out.append(f"DEEPSEEK_API_KEY={api_key}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"DEEPSEEK_API_KEY={api_key}")
    ENV_LOCAL.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    os.environ["DEEPSEEK_API_KEY"] = api_key


def ai_config(db: Session) -> dict:
    """页面可见的 AI 配置：密钥只返回掩码。"""
    cfg = dict(get_all(db).get("ai") or {})
    key = read_api_key()
    effective_model = (cfg.get("custom_model") or "").strip() or cfg.get("model") or "deepseek-flash"
    cfg.update({
        "api_key_masked": mask_secret(key),
        "api_key_configured": bool(key),
        "effective_model": effective_model,
        "model_presets": MODEL_PRESETS,
        "deprecated_models": sorted(DEPRECATED_MODELS),
        "model_is_deprecated": effective_model in DEPRECATED_MODELS,
    })
    return cfg


def save_ai_config(db: Session, payload: dict) -> dict:
    current = dict(get_all(db).get("ai") or {})
    allowed = {
        "model", "custom_model", "base_url", "temperature", "max_tokens",
        "thinking", "reasoning_effort", "stream", "timeout_seconds", "max_retries",
    }
    for k, v in payload.items():
        if k in allowed:
            current[k] = v
    put(db, "ai", current)
    # 空字符串视为「不修改」，避免表单里留空把已配置的密钥清掉；
    # 如需主动清除，须显式传 api_key_clear=true。
    if "api_key" in payload and str(payload["api_key"] or "").strip():
        write_api_key(str(payload["api_key"]).strip())
    elif payload.get("api_key_clear") is True:
        write_api_key("")
    return ai_config(db)


def model_capabilities(model: str) -> list[str]:
    for preset in MODEL_PRESETS:
        if preset["id"] == model:
            return list(preset["capabilities"])
    # 未知 / 自定义模型：保守能力集
    return ["temperature", "max_tokens", "stream"]
