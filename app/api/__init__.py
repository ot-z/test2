"""REST API（统一前缀 /api，页面只消费 JSON，便于日后替换前端）。"""
from fastapi import APIRouter

from . import (routes_ai, routes_catalog, routes_knowledge, routes_link_rules,
               routes_plan, routes_records, routes_system)

api_router = APIRouter()
for module in (routes_catalog, routes_knowledge, routes_plan, routes_records,
               routes_system, routes_ai, routes_link_rules):
    api_router.include_router(module.router)

__all__ = ["api_router"]
