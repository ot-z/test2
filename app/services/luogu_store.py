"""洛谷题面采集：本地落库、Markdown 导出、进度状态与批量编排（FR-30）。

与既有批量导入路径（catalog.import_batch / collector.collect）**完全隔离**：
- 那些路径继续只写元信息、绝不生成题面快照（FR-28 / TC-11 不变）；
- 本模块是用户显式发起的独立通道，才允许写入 ``problem_snapshots``；
- 题面快照不参与任何导出产物，仅供本地阅读。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import DATA_DIR, EXPORT_DIR
from ..models import KnowledgeNode, Problem, ProblemKnowledge, ProblemSnapshot
from . import link_rules
from .luogu import (
    LuoguClient, LuoguError, ProblemNotFound, RateLimited, TAGS_CACHE,
    load_config, load_tagbook, sync_difficulty_names,
)

STATE_PATH = DATA_DIR / "luogu_fetch_state.json"
CATALOG_PATH = DATA_DIR / "luogu_pids.json"
MD_DIR = EXPORT_DIR / "luogu"
INDEX_PATH = MD_DIR / "index.csv"

PID_RE = re.compile(r"^([A-Za-z]+)?(\d+)$", re.I)


# ---------------------------------------------------------------- 进度状态

def load_state(path: Path | None = None) -> dict:
    p = path or STATE_PATH
    if p.exists():
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("done", [])
                data.setdefault("missing", [])
                data.setdefault("failed", {})
                return data
        except (OSError, ValueError):
            pass
    return {"done": [], "missing": [], "failed": {}, "last_pid": "", "updated_at": ""}


def save_state(state: dict, path: Path | None = None) -> None:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def normalize_pid(value: Any) -> str:
    """1001 / p1001 / P1001 → P1001；其它前缀（B/CF/AT…）原样大写。"""
    text = str(value or "").strip()
    match = PID_RE.match(text)
    if not match:
        return text.upper()
    prefix, num = match.group(1), match.group(2)
    return f"{(prefix or 'P').upper()}{int(num)}"


def pid_number(pid: str) -> int:
    match = re.search(r"(\d+)", str(pid))
    return int(match.group(1)) if match else 0


# ---------------------------------------------------------------- 题号清单

def ensure_catalog(client: LuoguClient, refresh: bool = False,
                   progress: Callable[[str], None] | None = None) -> dict:
    """从公开题库列表页拉取全部 P 题题号（精确清单，不靠盲猜题号）。"""
    if CATALOG_PATH.exists() and not refresh:
        with open(CATALOG_PATH, encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("pids"):
            return cached
    first = client.fetch_catalog_page(1)
    count = first["count"]
    per_page = max(1, first["per_page"])
    pages = max(1, math.ceil(count / per_page))
    pids: list[str] = []
    for page in range(1, pages + 1):
        payload = first if page == 1 else client.fetch_catalog_page(page)
        for row in payload["items"]:
            pid = str(row.get("pid") or "").strip()
            if pid and pid not in pids:
                pids.append(pid)
        if progress and (page == 1 or page % 20 == 0 or page == pages):
            progress(f"题库清单 {page}/{pages} 页，已收集 {len(pids)} 个题号")
    result = {"count": count, "pages": pages, "pids": pids,
              "updated_at": datetime.now().isoformat(timespec="seconds")}
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _existing_pids(db: Session) -> list[str]:
    rows = db.scalars(select(Problem).where(Problem.source == "luogu")
                      .order_by(Problem.source_key)).all()
    return [r.source_key for r in rows if str(r.source_key or "").upper().startswith("P")]


def build_pid_list(db: Session, params: dict, client: LuoguClient | None,
                   progress: Callable[[str], None] | None = None) -> list[str]:
    mode = str(params.get("mode") or "range")
    if mode == "existing":
        pids = _existing_pids(db)
    elif mode == "catalog":
        if client is None:
            raise LuoguError("catalog 模式需要网络客户端")
        payload = ensure_catalog(client, refresh=bool(params.get("refresh_catalog")),
                                 progress=progress)
        pids = list(payload["pids"])
    else:
        start = int(params.get("start") or 1000)
        end = params.get("end")
        if end is None:
            count = int(params.get("count") or 20)
            end = start + max(1, count) - 1
        end = int(end)
        if end < start:
            raise LuoguError("end 不能小于 start")
        pids = [f"P{n}" for n in range(start, end + 1)]

    if params.get("pid_min") is not None:
        pids = [p for p in pids if pid_number(p) >= int(params["pid_min"])]
    if params.get("pid_max") is not None:
        pids = [p for p in pids if pid_number(p) <= int(params["pid_max"])]
    if params.get("skip_done", True) and params.get("state") is not None:
        done = set(params["state"].get("done") or [])
        pids = [p for p in pids if normalize_pid(p) not in done]
    limit = params.get("max_items")
    if limit:
        pids = pids[: int(limit)]
    return [normalize_pid(p) for p in pids]


# ---------------------------------------------------------------- 单题落库

def content_fingerprint(md: str) -> str:
    """忽略采集时间行，用于判断同一份题面是否已经存过快照。"""
    body = "\n".join(line for line in md.splitlines()
                     if not line.startswith("> **本地采集时间**"))
    return hashlib.md5(body.encode("utf-8")).hexdigest()


def store_problem(db: Session, item: dict, cfg: dict | None = None, *,
                  force: bool = False, save_files: bool = True,
                  respect_manual: bool = True) -> dict:
    """把一题的元信息 / 知识点 / 题面快照 / Markdown 文件写入本地。"""
    cfg = cfg or load_config()
    pid = item["pid"]
    p = item["problem"]
    diff = item["difficulty"]
    tags = item["tags"]["all"]
    nodes = item["knowledge_nodes"]
    url = link_rules.derive_url("luogu", pid) or item["url"]
    md = item["content_md"]

    problem = db.scalar(select(Problem).where(
        Problem.source == "luogu", Problem.source_key == pid))
    created = problem is None
    skipped_manual = False
    if created:
        problem = Problem(
            source="luogu", source_key=pid, title=str(p.get("name") or pid)[:300],
            url=url, difficulty_raw=diff["raw"][:80], difficulty_level=diff["level"],
            external_tags=list(tags), is_active=True, manual_edited=False,
            sync_status="crawled", solved_count=int(p.get("totalAccepted") or 0),
        )
        db.add(problem)
        db.flush()
    else:
        skipped_manual = bool(problem.manual_edited and respect_manual and not force)
        if not skipped_manual:
            problem.title = str(p.get("name") or pid)[:300]
            problem.url = url
            problem.difficulty_raw = diff["raw"][:80]
            problem.difficulty_level = diff["level"]
            problem.external_tags = list(tags)
            problem.solved_count = int(p.get("totalAccepted") or 0)
        problem.sync_status = "crawled"

    # 知识点归一：只补充缺失关联，不删除人工标注
    node_index = {n.name: n.id for n in db.scalars(select(KnowledgeNode)).all()}
    linked = set(db.scalars(select(ProblemKnowledge.node_id).where(
        ProblemKnowledge.problem_id == problem.id)).all())
    nodes_added = 0
    for name in nodes:
        node_id = node_index.get(name)
        if not node_id or node_id in linked:
            continue
        linked.add(node_id)
        db.add(ProblemKnowledge(problem_id=problem.id, node_id=node_id,
                                confidence="mapped", verified=True))
        nodes_added += 1

    # 题面快照：同一份内容只存一条
    last = db.scalar(select(ProblemSnapshot).where(
        ProblemSnapshot.problem_id == problem.id)
        .order_by(ProblemSnapshot.id.desc()).limit(1))
    fingerprint = content_fingerprint(md)
    snapshot_action = "duplicate"
    if last is None or content_fingerprint(last.content_md or "") != fingerprint:
        db.add(ProblemSnapshot(
            problem_id=problem.id, content_md=md,
            source_url=item["url"] or url, user_initiated=True,
        ))
        snapshot_action = "created"

    file_path = ""
    if save_files:
        MD_DIR.mkdir(parents=True, exist_ok=True)
        path = MD_DIR / f"{pid}.md"
        path.write_text(md, encoding="utf-8")
        file_path = str(path)

    db.commit()
    return {
        "pid": pid, "title": str(p.get("name") or pid),
        "action": "created" if created else ("skipped_manual" if skipped_manual else "updated"),
        "difficulty": diff["raw"], "difficulty_level": diff["level"],
        "tags": tags, "knowledge_nodes": nodes, "nodes_added": nodes_added,
        "snapshot": snapshot_action, "file": file_path,
        "length": len(md),
    }


def rebuild_index(db: Session) -> dict:
    """根据已抓快照重建 exports/luogu/index.csv，便于直接检索本地题面。

    索引只是检索便利，不是采集结果本身。用户常用 Excel 打开 index.csv 查看，
    Windows 会独占该文件，此时直接 ``open(..., "w")`` 会抛 PermissionError，
    让整批采集请求在最后一步变成 500。因此：先写临时文件再原子替换；若目标仍被
    占用，退回到带时间戳的备用文件，并返回 warning 由上层提示，绝不阻断已入库数据。
    """
    MD_DIR.mkdir(parents=True, exist_ok=True)
    rows = db.execute(
        select(Problem.source_key, Problem.title, Problem.difficulty_raw,
               Problem.difficulty_level, Problem.external_tags,
               func.count(ProblemSnapshot.id))
        .join(ProblemSnapshot, ProblemSnapshot.problem_id == Problem.id)
        .where(Problem.source == "luogu")
        .group_by(Problem.id)
        .order_by(Problem.source_key)
    ).all()
    tmp = INDEX_PATH.with_suffix(".csv.tmp")

    def _write(path: Path) -> None:
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["题号", "标题", "洛谷难度", "项目难度(1-5)", "标签", "快照数", "链接", "本地文件"])
            for key, title, raw, level, tags, snaps in rows:
                writer.writerow([key, title, raw, level,
                                 "、".join(tags or []), snaps,
                                 link_rules.derive_url("luogu", key),
                                 str(MD_DIR / f"{key}.md")])

    try:
        _write(tmp)
        tmp.replace(INDEX_PATH)
        return {"path": str(INDEX_PATH), "rows": len(rows)}
    except OSError:
        # 固定备用文件名覆盖写：不累积时间戳文件，也不删除任何文件
        # （用户常把索引打开查看，删除会触发系统回收站策略且可能失败）
        fallback = MD_DIR / "index-new.csv"
        try:
            if tmp.exists():
                tmp.replace(fallback)
            else:
                _write(fallback)
            path = str(fallback)
        except OSError:
            # 备用文件也恰好被占用时，再退到带时间戳的文件，保证索引始终有产出
            try:
                fallback = MD_DIR / f"index-{datetime.now():%Y%m%d-%H%M%S}.csv"
                if tmp.exists():
                    tmp.replace(fallback)
                else:
                    _write(fallback)
                path = str(fallback)
            except OSError:
                path = ""
                # 临时文件留待下次覆盖写，不主动删除以免触发系统回收站策略
        detail = (f"已改写到 {fallback.name}，关闭占用后重新执行即可刷新"
                  if path else "本次跳过索引写入")
        return {
            "path": path,
            "rows": len(rows),
            "warning": (
                f"索引文件 index.csv 正被其它程序占用（常见于用 Excel 打开查看），{detail}；"
                "题面与题库数据均已正常保存，不影响本次采集结果。"
            ),
        }


# ---------------------------------------------------------------- 批量编排

def fetch_batch(db: Session, params: dict | None = None,
                progress: Callable[[dict], None] | None = None) -> dict:
    """执行一批采集。返回统计；任何单题失败都记录后继续，不中断整批之外的数据。"""
    params = dict(params or {})
    cfg = load_config()
    state = params.get("state") if isinstance(params.get("state"), dict) else load_state()
    params["state"] = state
    crawl = cfg.get("crawl") or {}
    max_missing = int(params.get("max_missing") or crawl.get("max_consecutive_missing", 120))
    max_errors = int(params.get("max_errors") or crawl.get("max_consecutive_errors", 6))
    flush_every = int(crawl.get("state_flush_every", 10))
    refresh = bool(params.get("refresh"))
    if refresh:
        state["done"] = []
        state["missing"] = []
        state["failed"] = {}
    skip_missing = bool(params.get("skip_missing", True))
    done = set(state.get("done") or [])
    missing = set(state.get("missing") or [])
    failed: dict = dict(state.get("failed") or {})

    interval = params.get("interval")
    with LuoguClient(cfg, interval=float(interval) if interval else None) as client:
        log = (lambda msg: progress({"event": "log", "message": msg})) if progress else None
        if params.get("dry_run"):
            # 预览只列题号：不拉取标签字典、不访问题目页、不写库
            pids = build_pid_list(db, params, None if params.get("mode") != "catalog" else client,
                                  progress=log)
            return {
                "planned": len(pids), "pids": pids[:50], "dry_run": True,
                "added": 0, "updated": 0, "snapshots": 0, "files": 0,
                "missing": 0, "failed": 0, "skipped": 0,
                "message": "仅预览，未抓取题面、未写入数据",
            }

        # 标签字典 / 难度枚举每批只同步一次，后续每题复用（离线缓存优先）
        refresh_meta = bool(params.get("refresh_tags"))
        load_tagbook(client=client, cfg=cfg, refresh=refresh_meta)
        cfg["_difficulty_names"] = sync_difficulty_names(client, cfg, refresh=refresh_meta)
        pids = build_pid_list(db, params, client, progress=log)

        stats = {"planned": len(pids), "added": 0, "updated": 0, "skipped_manual": 0,
                 "skipped": 0, "snapshots": 0, "files": 0, "missing": 0,
                 "failed": 0, "items": [], "dry_run": False, "stopped_reason": ""}
        consecutive_missing = 0
        consecutive_errors = 0
        touched = 0

        for pid in pids:
            if pid in done and not refresh:
                stats["skipped"] += 1
                continue
            if pid in missing and skip_missing and not refresh:
                stats["skipped"] += 1
                continue
            try:
                item = client.fetch_problem(pid)
            except ProblemNotFound:
                consecutive_missing += 1
                consecutive_errors = 0
                stats["missing"] += 1
                missing.add(pid)
                failed.pop(pid, None)
                if progress:
                    progress({"event": "missing", "pid": pid,
                              "message": f"{pid} 不存在或未公开"})
                if consecutive_missing >= max_missing:
                    stats["stopped_reason"] = f"连续 {max_missing} 个题号不存在，判定已到题库末尾"
                    break
                continue
            except RateLimited as exc:
                consecutive_errors += 1
                stats["failed"] += 1
                failed[pid] = str(exc)
                if progress:
                    progress({"event": "error", "pid": pid, "message": str(exc)})
                time.sleep(float(crawl.get("retry_backoff_seconds", 4)) * 6)
                if consecutive_errors >= max_errors:
                    stats["stopped_reason"] = "站点持续限流，已停止以免继续加压；稍后重跑会自动续传"
                    break
                continue
            except LuoguError as exc:
                consecutive_errors += 1
                stats["failed"] += 1
                failed[pid] = str(exc)
                if progress:
                    progress({"event": "error", "pid": pid, "message": str(exc)})
                if consecutive_errors >= max_errors:
                    stats["stopped_reason"] = f"连续 {max_errors} 次请求失败，已停止；稍后重跑会自动续传"
                    break
                continue

            consecutive_missing = 0
            consecutive_errors = 0
            result = store_problem(
                db, item, cfg, force=bool(params.get("force")),
                save_files=bool(params.get("save_files", True)),
                respect_manual=bool(params.get("respect_manual", True)),
            )
            if result["action"] == "created":
                stats["added"] += 1
            elif result["action"] == "updated":
                stats["updated"] += 1
            else:
                stats["skipped_manual"] += 1
            stats["snapshots"] += 1 if result["snapshot"] == "created" else 0
            stats["files"] += 1 if result["file"] else 0
            stats["items"].append(result)
            done.add(pid)
            missing.discard(pid)
            failed.pop(pid, None)
            state["last_pid"] = pid
            touched += 1
            if progress:
                progress({"event": "item", **result})
            if touched % flush_every == 0:
                state.update(done=sorted(done), missing=sorted(missing), failed=failed)
                save_state(state)

        state.update(done=sorted(done), missing=sorted(missing), failed=failed)
        save_state(state)

    try:
        index_result = rebuild_index(db)
    except Exception as exc:  # noqa: BLE001 - 索引是附属产物，任何异常都不能让已入库的采集结果丢失
        index_result = {"path": "", "rows": 0,
                        "warning": f"索引重建跳过（{type(exc).__name__}），题面与题库数据已正常保存。"}
    stats["index"] = index_result
    stats["state_path"] = str(STATE_PATH)
    stats["message"] = (
        f"采集完成：新增 {stats['added']} 题、更新 {stats['updated']} 题、"
        f"题面快照 {stats['snapshots']} 份、不存在 {stats['missing']} 个、失败 {stats['failed']} 个"
    )
    if stats["stopped_reason"]:
        stats["message"] += f"；{stats['stopped_reason']}"
    if index_result.get("warning"):
        stats["message"] += f"；{index_result['warning']}"
    return stats


def status(db: Session) -> dict:
    """采集概览：配置、进度、题面数量、标签字典。"""
    state = load_state()
    cfg = load_config()
    snapshots = int(db.scalar(select(func.count()).select_from(ProblemSnapshot)) or 0)
    luogu = int(db.scalar(select(func.count()).select_from(Problem)
                          .where(Problem.source == "luogu")) or 0)
    linked = int(db.scalar(select(func.count()).select_from(ProblemKnowledge)
                           .where(ProblemKnowledge.confidence == "mapped")) or 0)
    catalog = {}
    if CATALOG_PATH.exists():
        try:
            with open(CATALOG_PATH, encoding="utf-8") as fh:
                catalog = json.load(fh)
        except (OSError, ValueError):
            catalog = {}
    return {
        "config_version": cfg.get("version"),
        "interval": (cfg.get("crawl") or {}).get("request_interval_seconds"),
        "max_missing": (cfg.get("crawl") or {}).get("max_consecutive_missing"),
        "tags_cached": TAGS_CACHE.exists(),
        "tags_cache_path": str(TAGS_CACHE),
        "tag_count": len(load_tagbook(cfg=cfg)) if TAGS_CACHE.exists() else 0,
        "problems_luogu": luogu,
        "snapshots": snapshots,
        "mapped_links": linked,
        "state": {"done": len(state.get("done") or []),
                  "missing": len(state.get("missing") or []),
                  "failed": len(state.get("failed") or {}),
                  "last_pid": state.get("last_pid") or "",
                  "updated_at": state.get("updated_at") or ""},
        "catalog": {"count": catalog.get("count") or 0,
                    "pids": len(catalog.get("pids") or []),
                    "updated_at": catalog.get("updated_at") or ""},
        "md_dir": str(MD_DIR),
        "index_path": str(INDEX_PATH) if INDEX_PATH.exists() else "",
        "state_path": str(STATE_PATH),
    }
