"""数据源同步（FR-04）：**仅官方公开 API 的 JSON 元信息**。

合规边界（D-20 / NFR-05 / RK-09）：
- 不含任何 HTML 页面解析、自动翻页、绕过反爬、复用登录 Cookie 的逻辑；
- 不调用 LeetCode 被 robots.txt 禁止的 /api/、/graphql 端点；
- 不下载或存储题面、样例、题解原文；
- 外部请求限速 ≥ 1 秒；失败时保留本地目录并标记 stale，绝不清空数据。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import _utcnow
from ..errors import ValidationError
from ..models import (
    KnowledgeNode, Problem, ProblemKnowledge, SyncLog, TagMapping,
)
from ..services.settings import get_all as get_settings
from ..utils import difficulty_from_cf_rating, model_dict

CF_API = "https://codeforces.com/api/problemset.problems"
AT_API = "https://kenkoooo.com/atcoder/resources/problems.json"
SUPPORTED = {"codeforces", "atcoder"}


def _node_names(db: Session) -> dict[str, int]:
    return {n.name: n.id for n in db.scalars(select(KnowledgeNode)).all()}


def _tag_map(db: Session, source: str) -> dict[str, list[int]]:
    rows = db.scalars(select(TagMapping).where(TagMapping.source.in_([source, "*"]))).all()
    out: dict[str, list[int]] = {}
    for row in rows:
        out.setdefault(row.external_tag.lower(), []).append(row.node_id)
    return out


def _attach_tags(db: Session, problem: Problem, source: str, tags: list[str]) -> int:
    mapping = _tag_map(db, source)
    added = 0
    for tag in tags:
        for node_id in mapping.get(str(tag).lower(), []):
            exists = db.scalar(select(ProblemKnowledge).where(
                ProblemKnowledge.problem_id == problem.id,
                ProblemKnowledge.node_id == node_id,
            ))
            if exists:
                continue
            db.add(ProblemKnowledge(
                problem_id=problem.id, node_id=node_id,
                confidence="mapped", verified=True,
            ))
            added += 1
    return added


def _fetch_cf() -> list[dict]:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resp = client.get(CF_API)
        resp.raise_for_status()
        payload = resp.json()
    if payload.get("status") != "OK":
        raise RuntimeError(f"Codeforces API 返回异常：{payload.get('comment', 'unknown')}")
    result = payload.get("result") or {}
    stats = {str(s.get("contestId")) + str(s.get("index")): s.get("solvedCount", 0)
             for s in result.get("problemStatistics", [])}
    rows = []
    for p in result.get("problems", []):
        key = f"{p.get('contestId')}{p.get('index')}"
        rows.append({
            "source": "codeforces", "source_key": key,
            "title": p.get("name") or key,
            "url": f"https://codeforces.com/problemset/problem/{p.get('contestId')}/{p.get('index')}",
            "difficulty_raw": str(p.get("rating") or ""),
            "difficulty_level": difficulty_from_cf_rating(p.get("rating")),
            "external_tags": list(p.get("tags") or []),
            "solved_count": int(stats.get(key) or 0),
        })
    return rows


def _fetch_at() -> list[dict]:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resp = client.get(AT_API)
        resp.raise_for_status()
        payload = resp.json()
    rows = []
    for p in payload or []:
        pid = p.get("id") or ""
        contest = p.get("contest_id") or ""
        if not pid or not contest:
            continue
        rows.append({
            "source": "atcoder", "source_key": pid,
            "title": p.get("title") or pid,
            "url": f"https://atcoder.jp/contests/{contest}/tasks/{pid}",
            "difficulty_raw": "",
            "difficulty_level": 3,  # 公开 JSON 无难度字段，人工校准
            "external_tags": [],
            "solved_count": 0,
        })
    return rows


def sync(db: Session, source: str, limit: int | None = None) -> dict:
    if source not in SUPPORTED:
        raise ValidationError(
            f"数据源 {source} 不支持自动同步。仅官方公开 API（codeforces / atcoder）允许同步；"
            "洛谷 / 力扣 / 蓝桥杯请使用手工录入或载体 B 结构化导入。"
        )
    settings = get_settings(db)
    switches = (settings.get("app") or {}).get("sync") or {}
    if not switches.get(f"{source}_enabled", source == "codeforces"):
        raise ValidationError(f"数据源 {source} 已在设置中关闭")
    interval = max(1.0, float(switches.get("request_interval_seconds", 1.2)))

    log = SyncLog(source=source, started_at=_utcnow(), status="running")
    db.add(log)
    db.commit()

    result: dict[str, Any] = {"source": source, "ok": False, "added": 0, "updated": 0,
                              "skipped_manual": 0, "failed": 0, "message": ""}
    try:
        time.sleep(interval)  # NFR-05：外部请求限速 ≥ 1 秒
        rows = _fetch_cf() if source == "codeforces" else _fetch_at()
        if limit:
            rows = rows[: int(limit)]
        existing = {(p.source, p.source_key): p for p in db.scalars(
            select(Problem).where(Problem.source == source)
        ).all()}
        tag_map = _tag_map(db, source)  # 全量同步时避免逐题查询映射表

        def attach(problem: Problem, tags: list[str]) -> None:
            for tag in tags:
                for node_id in tag_map.get(str(tag).lower(), []):
                    dup = db.scalar(select(ProblemKnowledge).where(
                        ProblemKnowledge.problem_id == problem.id,
                        ProblemKnowledge.node_id == node_id,
                    ))
                    if not dup:
                        db.add(ProblemKnowledge(
                            problem_id=problem.id, node_id=node_id,
                            confidence="mapped", verified=True,
                        ))

        for row in rows:
            key = (row["source"], row["source_key"])
            old = existing.get(key)
            if old is None:
                problem = Problem(
                    **{k: row[k] for k in (
                        "source", "source_key", "title", "url", "difficulty_raw",
                        "difficulty_level", "external_tags", "solved_count",
                    )},
                    is_active=True, manual_edited=False, sync_status="synced",
                )
                db.add(problem)
                db.flush()
                attach(problem, row["external_tags"])
                result["added"] += 1
            elif old.manual_edited:
                result["skipped_manual"] += 1  # 不覆盖人工编辑过的字段（FR-04 验收）
            else:
                for k in ("url", "difficulty_raw", "difficulty_level", "external_tags", "solved_count"):
                    setattr(old, k, row[k])
                old.sync_status = "synced"
                result["updated"] += 1
        db.commit()
        result["ok"] = True
        result["message"] = f"同步完成，共处理 {len(rows)} 条元信息"
        log.status = "ok"
    except Exception as exc:  # noqa: BLE001 —— 断网 / 改版必须降级而非崩溃
        db.rollback()
        result.update({"added": 0, "updated": 0, "skipped_manual": 0})
        # 保留既有目录，仅标记 stale
        for p in db.scalars(select(Problem).where(Problem.source == source)).all():
            p.sync_status = "stale"
        db.commit()
        result["message"] = f"同步失败，已保留本地目录并标记 stale：{type(exc).__name__}: {exc}"
        result["failed"] = 1
        log.status = "stale"
    log.finished_at = _utcnow()
    log.added = result["added"]
    log.updated = result["updated"]
    log.failed = result["failed"]
    log.message = result["message"]
    db.commit()
    result["log_id"] = log.id
    return result


def logs(db: Session, limit: int = 20) -> list[dict]:
    return [model_dict(r) for r in db.scalars(
        select(SyncLog).order_by(SyncLog.id.desc()).limit(limit)
    ).all()]


def last_sync(db: Session) -> dict | None:
    row = db.scalar(select(SyncLog).order_by(SyncLog.id.desc()))
    if row is None:
        return None
    return {"source": row.source, "status": row.status,
            "finished_at": row.finished_at.isoformat(timespec="seconds") if row.finished_at else None,
            "message": row.message}
