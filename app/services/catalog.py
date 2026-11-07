"""题目目录服务（FR-01 / FR-02 / FR-03 / FR-27 导入契约 / FR-28 单题快照）。

合规边界：批量路径只存元信息与 AI 摘要，**绝不写入题面原文**；
题面原文只能经 FR-28 单题、用户主动触发的独立通道进入 problem_snapshots。
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..errors import Conflict, NotFound, ValidationError
from . import link_rules
from ..models import (
    DailyPlanItem, KnowledgeNode, Note, Problem, ProblemKnowledge,
    ProblemSnapshot, StudyRecord, TagMapping,
)
from ..utils import clean_text, is_http_url, model_dict


def derive_url(source: str, key: str) -> str:
    """题号 → 原题链接（委托 config/link_rules.yaml，站点规则不再硬编码）。"""
    return link_rules.derive_url(source, key)


def _node_index(db: Session) -> dict[str, int]:
    return {n.name: n.id for n in db.scalars(select(KnowledgeNode)).all()}


def _map_tags(db: Session, source: str, tags: list[str]) -> list[int]:
    """按 tag_mappings 把外部标签归一到内部知识点节点（FR-06）。"""
    if not tags:
        return []
    rows = db.scalars(
        select(TagMapping).where(
            TagMapping.source.in_([source, "*"]),
            TagMapping.external_tag.in_([str(t).lower() for t in tags]),
        )
    ).all()
    result = []
    for row in rows:
        if str(row.external_tag).lower() in {str(t).lower() for t in tags} and row.node_id not in result:
            result.append(row.node_id)
    return result


def _tag_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in re.split(r"[,;|，；、]", str(value)) if x.strip()]


def problem_detail(db: Session, problem: Problem) -> dict:
    out = model_dict(problem)
    out["source_label"] = link_rules.known_sources().get(problem.source, problem.source)
    links = db.scalars(
        select(ProblemKnowledge).where(ProblemKnowledge.problem_id == problem.id)
    ).all()
    node_map = {n.id: n for n in db.scalars(select(KnowledgeNode)).all()}
    out["knowledge"] = [
        {
            "id": link.id, "node_id": link.node_id,
            "name": node_map.get(link.node_id).name if node_map.get(link.node_id) else "?",
            "category": node_map.get(link.node_id).category if node_map.get(link.node_id) else "",
            "confidence": link.confidence, "verified": link.verified,
        }
        for link in links
    ]
    out["notes_count"] = int(db.scalar(
        select(func.count()).select_from(Note).where(Note.problem_id == problem.id)
    ) or 0)
    out["records_count"] = int(db.scalar(
        select(func.count()).select_from(StudyRecord).where(StudyRecord.problem_id == problem.id)
    ) or 0)
    out["snapshots_count"] = int(db.scalar(
        select(func.count()).select_from(ProblemSnapshot).where(ProblemSnapshot.problem_id == problem.id)
    ) or 0)
    return out


def list_problems(db: Session, q: dict) -> dict:
    page = max(1, int(q.get("page", 1)))
    page_size = max(1, min(int(q.get("page_size", 20)), 200))
    stmt = select(Problem)
    count_stmt = select(func.count()).select_from(Problem)

    conditions = []
    if q.get("include_inactive") not in (True, "true", "1", 1):
        conditions.append(Problem.is_active.is_(True))
    if q.get("source"):
        conditions.append(Problem.source == q["source"])
    if q.get("difficulty_min"):
        conditions.append(Problem.difficulty_level >= int(q["difficulty_min"]))
    if q.get("difficulty_max"):
        conditions.append(Problem.difficulty_level <= int(q["difficulty_max"]))
    if q.get("node_id"):
        sub = select(ProblemKnowledge.problem_id).where(
            ProblemKnowledge.node_id == int(q["node_id"]),
            ProblemKnowledge.verified.is_(True),  # AI 未确认标签不参与筛选（FR-06）
        )
        conditions.append(Problem.id.in_(sub))
    if q.get("keyword"):
        kw = f"%{q['keyword']}%"
        conditions.append(or_(Problem.title.like(kw), Problem.source_key.like(kw)))
    if q.get("learned") in (True, "true", "1", 1, False, "false", "0", 0):
        learned_ids = select(DailyPlanItem.problem_id).where(
            DailyPlanItem.status.in_(["learned", "ac", "reviewed"])
        )
        if q.get("learned") in (True, "true", "1", 1):
            conditions.append(Problem.id.in_(learned_ids))
        else:
            conditions.append(Problem.id.not_in(learned_ids))
    if conditions:
        stmt = stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)

    total = int(db.scalar(count_stmt) or 0)
    sort = q.get("sort", "id")
    order = {
        "difficulty": Problem.difficulty_level,
        "title": Problem.title,
        "source_key": Problem.source_key,
    }.get(sort, Problem.id)
    rows = db.scalars(
        stmt.order_by(order.asc(), Problem.id.asc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [model_dict(p) | {"source_label": link_rules.known_sources().get(p.source, p.source)} for p in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


def get_problem(db: Session, problem_id: int) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise NotFound(f"题目 {problem_id} 不存在")
    return problem_detail(db, problem)


def create_problem(db: Session, payload: dict) -> dict:
    source = clean_text(payload.get("source"), 32).lower()
    key = clean_text(payload.get("source_key"), 120)
    if not source or not key:
        raise ValidationError("source 与 source_key 必填")
    sources = link_rules.known_sources()
    if source not in sources:
        raise ValidationError(f"source 必须是 {', '.join(sources)} 之一")
    exists = db.scalar(select(Problem).where(Problem.source == source, Problem.source_key == key))
    if exists:
        raise Conflict(f"已存在同源同题号：{source}/{key}（id={exists.id}）")
    url = clean_text(payload.get("url"), 1000) or derive_url(source, key)
    if url and not is_http_url(url):
        raise ValidationError("url 必须是 http(s) 链接")
    problem = Problem(
        source=source, source_key=key,
        title=clean_text(payload.get("title"), 300) or key,
        url=url,
        difficulty_raw=clean_text(payload.get("difficulty_raw"), 80),
        difficulty_level=max(1, min(5, int(payload.get("difficulty_level") or 3))),
        external_tags=_tag_list(payload.get("external_tags")),
        note=clean_text(payload.get("note") or "", 4000),
        summary=clean_text(payload.get("summary") or "", 4000),
        is_active=bool(payload.get("is_active", True)),
        manual_edited=True,
        sync_status="manual",
    )
    db.add(problem)
    db.flush()
    _attach_nodes(db, problem, payload.get("knowledge_nodes") or [], "manual", True)
    db.commit()
    return get_problem(db, problem.id)


def _attach_nodes(db: Session, problem: Problem, names: list[str], confidence: str, verified: bool) -> int:
    nodes = _node_index(db)
    count = 0
    for name in dict.fromkeys(str(n).strip() for n in names if str(n).strip()):
        node_id = nodes.get(name)
        if not node_id:
            continue
        dup = db.scalar(select(ProblemKnowledge).where(
            ProblemKnowledge.problem_id == problem.id,
            ProblemKnowledge.node_id == node_id,
        ))
        if dup:
            continue
        db.add(ProblemKnowledge(
            problem_id=problem.id, node_id=node_id,
            confidence=confidence, verified=verified,
        ))
        count += 1
    return count


def update_problem(db: Session, problem_id: int, payload: dict) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise NotFound(f"题目 {problem_id} 不存在")
    allowed = ("title", "url", "difficulty_raw", "difficulty_level", "note", "summary", "is_active")
    for key in allowed:
        if key in payload:
            val = payload[key]
            if key == "url":
                val = clean_text(val, 1000)
                if val and not is_http_url(val):
                    raise ValidationError("url 必须是 http(s) 链接")
            elif key == "difficulty_level":
                val = max(1, min(5, int(val or 3)))
            elif key == "is_active":
                val = bool(val)
            else:
                val = clean_text(val, 4000) if key != "title" else clean_text(val, 300)
            setattr(problem, key, val)
    if "external_tags" in payload:
        problem.external_tags = _tag_list(payload["external_tags"])
    if "knowledge_nodes" in payload:
        for link in db.scalars(select(ProblemKnowledge).where(ProblemKnowledge.problem_id == problem.id)).all():
            db.delete(link)
        _attach_nodes(db, problem, _tag_list(payload["knowledge_nodes"]), "manual", True)
    problem.manual_edited = True  # 同步时不得覆盖人工编辑过的字段（FR-04）
    db.commit()
    return get_problem(db, problem.id)


def deactivate_problem(db: Session, problem_id: int) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise NotFound(f"题目 {problem_id} 不存在")
    problem.is_active = False
    problem.manual_edited = True
    db.commit()
    return {"id": problem_id, "is_active": False}


def verify_link(db: Session, link_id: int) -> dict:
    link = db.get(ProblemKnowledge, link_id)
    if link is None:
        raise NotFound(f"标签关联 {link_id} 不存在")
    link.verified = True
    db.commit()
    return {"id": link_id, "verified": True}


# ---------------------------------------------------------------- 批量导入

def _parse_rows(payload: dict) -> list[dict]:
    """支持三种输入：附录 C 契约 JSON、CSV 文本、粘贴的题号行。"""
    default_source = clean_text(payload.get("source"), 32).lower() or "luogu"
    if default_source not in link_rules.known_sources():
        default_source = "other"

    if isinstance(payload.get("problems"), list):
        # 附录 C.1：顶层契约同样必须校验 schema_version，避免静默接受错误格式
        version = payload.get("schema_version", "1.0")
        if str(version) != "1.0":
            raise ValidationError(f"不支持的 schema_version：{version}（当前契约 1.0）")
        return [dict(r) for r in payload["problems"]]

    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("problems"), list):
        if data.get("schema_version") and str(data["schema_version"]) != "1.0":
            raise ValidationError(f"不支持的 schema_version：{data['schema_version']}（当前契约 1.0）")
        return [dict(r) for r in data["problems"]]
    if isinstance(data, list):
        return [dict(r) for r in data]

    text = payload.get("csv") or payload.get("text") or ""
    text = str(text).strip()
    if not text:
        raise ValidationError("导入内容为空")

    first = text.splitlines()[0]
    if "source_key" in first or "," in first and ("source" in first or "题号" in first):
        reader = csv.DictReader(io.StringIO(text))
        return [dict(r) for r in reader]

    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in re.split(r"[\s,，\t]+", line) if p]
        if not parts:
            continue
        source = default_source
        if parts[0].lower() in link_rules.known_sources():
            source = parts[0].lower()
            parts = parts[1:]
        if not parts:
            continue
        key = parts[0]
        url = next((p for p in parts[1:] if is_http_url(p)), "")
        title_parts = [p for p in parts[1:] if not is_http_url(p)]
        rows.append({
            "source": source, "source_key": key,
            "title": " ".join(title_parts) if title_parts else key,
            "url": url,
        })
    return rows


def import_batch(db: Session, payload: dict, dry_run: bool = False) -> dict:
    """预览 / 事务化批量导入。任一失败不产生半写入数据（FR-02 验收）。"""
    rows = _parse_rows(payload)
    existing = {(p.source, p.source_key) for p in db.scalars(select(Problem)).all()}
    nodes = _node_index(db)
    prepared: list[dict] = []
    duplicates: list[dict] = []
    failed: list[dict] = []

    for idx, raw in enumerate(rows, start=1):
        source = clean_text(raw.get("source"), 32).lower()
        key = clean_text(raw.get("source_key") or raw.get("题号") or raw.get("key"), 120)
        if not source or not key or source not in link_rules.known_sources():
            failed.append({"row": idx, "reason": "缺少合法的 source / source_key"})
            continue
        if (source, key) in existing:
            duplicates.append({"row": idx, "source": source, "source_key": key})
            continue
        url = clean_text(raw.get("url"), 1000) or derive_url(source, key)
        if url and not is_http_url(url):
            failed.append({"row": idx, "reason": "url 不是 http(s) 链接"})
            continue
        try:
            level = max(1, min(5, int(raw.get("difficulty_level") or 3)))
        except (TypeError, ValueError):
            failed.append({"row": idx, "reason": "difficulty_level 必须是 1–5"})
            continue
        confidence = str(raw.get("confidence") or "manual").lower()
        verified = bool(raw.get("verified", True)) and confidence != "ai"
        prepared.append({
            "source": source, "source_key": key,
            "title": clean_text(raw.get("title") or raw.get("标题"), 300) or key,
            "url": url,
            "difficulty_raw": clean_text(raw.get("difficulty_raw"), 80),
            "difficulty_level": level,
            "external_tags": _tag_list(raw.get("external_tags")),
            "summary": clean_text(raw.get("summary"), 4000),
            "knowledge_nodes": _tag_list(raw.get("knowledge_nodes")),
            "confidence": "ai" if confidence == "ai" else "manual",
            "verified": verified,
            "nodes_index": nodes,
        })
        existing.add((source, key))

    result = {
        "total": len(rows),
        "added": len(prepared),
        "added_preview": [
            {"source": r["source"], "source_key": r["source_key"], "title": r["title"],
             "url": r["url"], "difficulty_level": r["difficulty_level"]}
            for r in prepared
        ],
        "duplicates": duplicates,
        "failed": failed,
        "committed": False,
        "dry_run": dry_run,
        "snapshots_created": 0,  # TC-11：批量导入绝不自动生成题面快照
    }
    if dry_run:
        return result

    try:
        for row in prepared:
            problem = Problem(
                source=row["source"], source_key=row["source_key"], title=row["title"],
                url=row["url"], difficulty_raw=row["difficulty_raw"],
                difficulty_level=row["difficulty_level"],
                external_tags=row["external_tags"], summary=row["summary"],
                is_active=True, manual_edited=False, sync_status="manual",
            )
            db.add(problem)
            db.flush()
            # SessionLocal 使用 autoflush=False，pending 对象不会被后续 SELECT 看到；
            # 因此知识点关联必须用本地集合去重，不能只依赖数据库查重。
            attached: set[int] = set()
            for name in dict.fromkeys(row["knowledge_nodes"]):
                node_id = row["nodes_index"].get(name)
                if not node_id or node_id in attached:
                    continue
                db.add(ProblemKnowledge(
                    problem_id=problem.id, node_id=node_id,
                    confidence=row["confidence"], verified=row["verified"],
                ))
                attached.add(node_id)
            # 外部标签归一（FR-06）
            for node_id in _map_tags(db, row["source"], row["external_tags"]):
                if node_id in attached:
                    continue
                db.add(ProblemKnowledge(
                    problem_id=problem.id, node_id=node_id,
                    confidence="mapped", verified=True,
                ))
                attached.add(node_id)
            db.flush()
        db.commit()
        result["committed"] = True
    except Exception as exc:  # noqa: BLE001 —— 保证不产生半写入
        db.rollback()
        from ..errors import AppError
        raise AppError(f"导入失败，已回滚全部写入：{exc}") from exc
    return result


# ---------------------------------------------------------------- FR-28 单题快照

def save_snapshot(db: Session, problem_id: int, payload: dict) -> dict:
    if db.get(Problem, problem_id) is None:
        raise NotFound(f"题目 {problem_id} 不存在")
    content = str(payload.get("content_md") or "").strip()
    if not content:
        raise ValidationError("快照内容不能为空")
    problem = db.get(Problem, problem_id)
    snap = ProblemSnapshot(
        problem_id=problem_id, content_md=content,
        source_url=clean_text(payload.get("source_url") or problem.url, 1000),
        user_initiated=True,
    )
    db.add(snap)
    db.commit()
    return {
        "id": snap.id, "problem_id": problem_id,
        "source_url": snap.source_url, "captured_at": snap.captured_at.isoformat(timespec="seconds"),
        "user_initiated": True, "length": len(content),
    }


def list_snapshots(db: Session, problem_id: int) -> list[dict]:
    return [
        model_dict(s) | {"content_preview": s.content_md[:120]}
        for s in db.scalars(
            select(ProblemSnapshot).where(ProblemSnapshot.problem_id == problem_id)
            .order_by(ProblemSnapshot.captured_at.desc())
        ).all()
    ]


def delete_snapshot(db: Session, snapshot_id: int) -> dict:
    snap = db.get(ProblemSnapshot, snapshot_id)
    if snap is None:
        raise NotFound(f"快照 {snapshot_id} 不存在")
    db.delete(snap)
    db.commit()
    return {"id": snapshot_id, "deleted": True}
