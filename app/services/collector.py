"""自动题目录入 / 扫描采集（FR-29）。

只做「文本 / 题号 / 链接 → 按规则归一化 → 生成正确外链 → 元信息入库」，
**不访问、不抓取、不解析任何题目网页**，不产生题面快照（D-20 / NFR-05 / RK-09）。
题目元信息（标题、标签）来自用户粘贴的文本，或后续的官方公开 API 同步。
"""
from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ValidationError
from ..models import Problem
from ..seed_data import LEGACY_LANQIAO_FIX, STARTER_BY_KEY
from . import catalog, link_rules

EXAMPLES = {
    "lanqiao": {
        "title": "蓝桥云课题号清单",
        "source": "lanqiao",
        "text": """# 蓝桥云课官方题库（题号 = 列表页「编号」）
254 A+B 问题【OJ 示例题目】
605 年号字串
600 数列求值
192 等差数列
602 迷宫
498 回文日期
1447 砝码称重
1460 路径
""",
    },
    "luogu": {
        "title": "洛谷题号与标题",
        "source": "luogu",
        "text": """# 洛谷（纯数字会自动补 P）
1001 A+B Problem
1048 采药
P8649 k 倍区间
P8680 特别数的和
P1001-P1005
""",
    },
    "mixed": {
        "title": "混合来源 / 链接 / 范围",
        "source": "auto",
        "text": """# 可用行内前缀或完整链接指定来源；混合清单自动识别
LQ254 A+B 问题【OJ 示例题目】
https://www.luogu.com.cn/problem/P8649 k 倍区间
CF71A Way Too Long Words
P16232 青春常数
lanqiao 1593 二进制问题
""",
    },
}


def scan_preview(payload: dict) -> dict:
    """只扫描与预览，不写库。"""
    text = payload.get("text") or payload.get("csv") or ""
    if not str(text).strip():
        raise ValidationError("扫描内容为空")
    result = link_rules.scan(
        text,
        source=payload.get("source") or "auto",
        mode=payload.get("mode") or "lines",
        default_source=payload.get("default_source") or "luogu",
        max_items=int(payload.get("max_items") or 0) or None,
    )
    for item in result["items"]:
        check = link_rules.check(item["source"], item["source_key"], item["url"])
        item["rule_ok"] = check["ok"]
        item["expected_url"] = check["expected_url"]
    return result


def collect(db: Session, payload: dict, dry_run: bool = True) -> dict:
    """扫描 → 生成候选题目 → dry_run 预览 / 事务化批量入库。"""
    scanned = scan_preview(payload)
    if not scanned["items"]:
        return {
            "scanned": 0, "issues": scanned["issues"], "candidates": [],
            "total": 0, "added": 0, "duplicates": [], "failed": [],
            "committed": False, "dry_run": dry_run,
            "message": "没有识别到可录入的题目，请检查题号格式或指定来源",
        }
    level = int(payload.get("difficulty_level") or 3)
    level = max(1, min(5, level))
    extra_tags = payload.get("external_tags") or []
    if isinstance(extra_tags, str):
        extra_tags = [t.strip() for t in extra_tags.replace("，", ",").split(",") if t.strip()]
    nodes = payload.get("knowledge_nodes") or []
    if isinstance(nodes, str):
        nodes = [n.strip() for n in nodes.replace("，", ",").split(",") if n.strip()]

    problems = [
        {
            "source": item["source"],
            "source_key": item["source_key"],
            "title": item["title"],
            "url": item["url"],
            "difficulty_level": level,
            "external_tags": list(extra_tags),
            "knowledge_nodes": list(nodes),
            "confidence": "manual",
            "summary": "",
        }
        for item in scanned["items"]
    ]
    result = catalog.import_batch(
        db, {"schema_version": "1.0", "problems": problems}, dry_run=dry_run
    )
    result.update({
        "scanned": scanned["total"],
        "issues": scanned["issues"],
        "candidates": scanned["items"],
        "truncated": scanned["truncated"],
        "rules_version": link_rules.version(),
        "snapshots_created": 0,
    })
    return result


def _legacy_findings(db: Session) -> list[dict]:
    out = []
    for old_key, new_key in LEGACY_LANQIAO_FIX.items():
        row = db.scalar(select(Problem).where(
            Problem.source == "lanqiao", Problem.source_key == old_key
        ))
        if row is None or f"/problems/{old_key}/" not in (row.url or ""):
            continue
        item = STARTER_BY_KEY.get(("lanqiao", new_key)) or {}
        out.append({
            "problem_id": row.id, "source": "lanqiao",
            "source_key": old_key, "url": row.url,
            "expected_key": new_key,
            "expected_url": link_rules.derive_url("lanqiao", new_key),
            "expected_title": item.get("title", ""),
            "issue": "legacy_seed",
            "reason": "v1.0.0 起始题库中的虚构蓝桥杯编号，题号与链接均需修正",
        })
    return out


def audit(db: Session, source: str | None = None, include_inactive: bool = False,
          limit: int = 500) -> dict:
    """题库体检：找出题号不合规、链接不匹配、旧种子编号等问题。"""
    stmt = select(Problem)
    if source:
        stmt = stmt.where(Problem.source == source)
    if not include_inactive:
        stmt = stmt.where(Problem.is_active.is_(True))
    rows = db.scalars(stmt.order_by(Problem.id)).all()
    issues: list[dict] = []
    by_issue: dict[str, int] = {}
    for row in rows:
        check = link_rules.check(row.source, row.source_key, row.url)
        legacy = (row.source == "lanqiao" and row.source_key in LEGACY_LANQIAO_FIX
                  and f"/problems/{row.source_key}/" in (row.url or ""))
        if check["ok"] and not legacy:
            continue
        issue = "legacy_seed" if legacy else check["issue"]
        by_issue[issue] = by_issue.get(issue, 0) + 1
        if len(issues) >= limit:
            continue
        issues.append({
            "problem_id": row.id, "source": row.source,
            "source_key": row.source_key, "title": row.title, "url": row.url,
            "issue": issue,
            "reason": check["reason"] or "v1.0.0 起始题库中的虚构蓝桥杯编号",
            "expected_url": (link_rules.derive_url("lanqiao", LEGACY_LANQIAO_FIX[row.source_key])
                             if legacy else check["expected_url"]),
            "expected_key": LEGACY_LANQIAO_FIX.get(row.source_key, "") if legacy else "",
            "expected_title": (STARTER_BY_KEY.get(("lanqiao", LEGACY_LANQIAO_FIX[row.source_key]), {}) or {}).get("title", "") if legacy else "",
            "manual_edited": row.manual_edited,
        })
    return {
        "total": len(rows), "issue_count": sum(by_issue.values()),
        "by_issue": by_issue, "issues": issues,
        "truncated": sum(by_issue.values()) > limit,
        "rules_version": link_rules.version(),
    }


def repair(db: Session, payload: dict | None = None, dry_run: bool = True) -> dict:
    """按规则修正链接 + 修正旧种子蓝桥杯编号；dry_run=true 只预览。"""
    payload = payload or {}
    source = payload.get("source") or None
    include_manual = bool(payload.get("include_manual", False))
    do_legacy = bool(payload.get("fix_legacy", True))

    legacy_preview = _legacy_findings(db) if do_legacy else []
    changes: list[dict] = []
    skipped: list[dict] = []

    stmt = select(Problem)
    if source:
        stmt = stmt.where(Problem.source == source)
    rows = db.scalars(stmt.order_by(Problem.id)).all()
    legacy_ids = {item["problem_id"] for item in legacy_preview}

    for row in rows:
        if row.id in legacy_ids:
            continue  # 旧种子记录由下面的统一流程处理
        check = link_rules.check(row.source, row.source_key, row.url)
        if check["ok"]:
            continue
        if row.manual_edited and not include_manual:
            skipped.append({"problem_id": row.id, "reason": "人工编辑过，未勾选强制修复"})
            continue
        if check["issue"] not in ("url_mismatch", "missing_url") or not check["expected_url"]:
            skipped.append({"problem_id": row.id, "source_key": row.source_key,
                            "reason": f"{check['issue']}：无法自动修复，需人工处理"})
            continue
        changes.append({
            "problem_id": row.id, "source": row.source, "source_key": row.source_key,
            "before": row.url, "after": check["expected_url"], "kind": "url",
        })
        if not dry_run:
            row.url = check["expected_url"]

    legacy_changes: list[dict] = []
    if do_legacy and not dry_run:
        from ..seed import fix_legacy_lanqiao
        for item in fix_legacy_lanqiao(db):
            legacy_changes.append(item)
    elif do_legacy:
        legacy_changes = [
            {"problem_id": item["problem_id"], "old_key": item["source_key"],
             "new_key": item["expected_key"], "title": item["expected_title"],
             "after": item["expected_url"], "kind": "legacy_seed"}
            for item in legacy_preview
        ]

    if not dry_run and (changes or legacy_changes):
        db.commit()
    return {
        "dry_run": dry_run, "committed": not dry_run,
        "url_changes": changes, "legacy_changes": legacy_changes,
        "skipped": skipped,
        "changed": len(changes) + len(legacy_changes),
        "message": ("预览完成，确认后再执行修复" if dry_run
                    else f"已修复 {len(changes) + len(legacy_changes)} 条，链接按 link_rules.yaml 重新生成"),
    }
