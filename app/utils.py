"""通用工具函数。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.inspection import inspect as sa_inspect


def mask_secret(value: str | None) -> str:
    """密钥脱敏：只保留前 3 与后 4 位，日志与页面均不得出现完整密钥。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}{'*' * 8}{value[-4:]}"


def parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def date_range(start: date, end: date):
    from datetime import timedelta
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def safe_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def is_http_url(url: str) -> bool:
    return bool(re.match(r"^https?://", (url or "").strip(), re.I))


def model_dict(obj, exclude: tuple[str, ...] = ()) -> dict:
    """把 ORM 对象序列化为可 JSON 化的 dict（日期转 ISO 字符串）。"""
    out: dict[str, Any] = {}
    for col in sa_inspect(obj).mapper.column_attrs:
        key = col.key
        if key in exclude:
            continue
        val = getattr(obj, key)
        if isinstance(val, datetime):
            val = val.isoformat(timespec="seconds")
        elif isinstance(val, date):
            val = val.isoformat()
        out[key] = val
    return out


def difficulty_from_cf_rating(rating: int | None) -> int:
    """Codeforces rating → 5 档难度。"""
    if not rating:
        return 3
    if rating < 1000:
        return 1
    if rating < 1400:
        return 2
    if rating < 1900:
        return 3
    if rating < 2400:
        return 4
    return 5


def difficulty_from_luogu(level: str | int) -> int:
    """洛谷难度（入门/普及-/普及/提高+/省选/NOI/CTS）→ 5 档。"""
    mapping = {
        "入门": 1, "普及-": 1, "普及/提高-": 2, "普及+/提高": 2,
        "普及": 2, "提高+/省选-": 4, "提高": 3, "省选/NOI-": 5,
        "省选": 5, "NOI/CTS": 5, "NOI": 5,
    }
    if isinstance(level, int):
        return max(1, min(5, level))
    for key, val in mapping.items():
        if key in str(level):
            return val
    return 3


def clean_text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]
