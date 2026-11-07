"""每日计划与复习调度（FR-09 / FR-10 / FR-11 / FR-12 / FR-13）。

规则严格对应原始方案与 SRS 4.3：
- 第 1..review_delay_days 天：每天仅 new_per_day 道新题；
- 第 review_delay_days+1 天起：每天再加 review_per_day 道复习题；
- 复习题来自「计划日期 ≤ 当日 - 复习延迟」的新题任务，且每道原题复习次数未达 review_repeat；
- review 任务用 source_plan_item_id 指回原任务，保证「第 8 天复习第 1 天的题」可追溯（TC-01）；
- 漏打卡顺延不堆积：历史任务过期标记 overdue，绝不补发任务（TC-03）；
- 参数变更写新的 config 版本，历史 daily_plans 用 config_snapshot 固化，不回溯（TC-04）。
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import DEFAULT_PLAN_CONFIG, TASK_STATUS_LABELS
from ..db import _utcnow
from ..errors import ValidationError
from ..models import (
    DailyPlan, DailyPlanItem, Note, PlanConfig, Problem, ProblemList,
    ProblemListItem, ReviewState, StudyRecord,
)
from ..utils import model_dict, parse_date

STATUS_RANK = {"pending": 0, "overdue": 0, "learning": 1, "learned": 2, "ac": 3, "reviewed": 4}
LEARNED_RANK = 2
CONFIG_FIELDS = (
    "name", "new_per_day", "review_per_day", "review_delay_days", "review_repeat",
    "selection_strategy", "review_mode", "start_date", "rest_weekdays", "pause_ranges",
)
STRATEGIES = {"earliest_due", "random", "weak_first"}
REVIEW_MODES = {"fixed", "dynamic"}


def active_config(db: Session) -> PlanConfig:
    cfg = db.scalar(
        select(PlanConfig).where(PlanConfig.is_active.is_(True)).order_by(PlanConfig.id.desc())
    )
    if cfg is None:
        cfg = PlanConfig(
            start_date=date.today(),
            **{k: v for k, v in DEFAULT_PLAN_CONFIG.items() if k in CONFIG_FIELDS and k != "start_date"},
            is_active=True,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def config_dict(cfg: PlanConfig) -> dict:
    data = model_dict(cfg)
    data["strategy_label"] = {
        "earliest_due": "最早到期", "random": "随机", "weak_first": "弱项优先",
    }.get(cfg.selection_strategy, cfg.selection_strategy)
    data["review_mode_label"] = "固定节奏" if cfg.review_mode == "fixed" else "动态间隔(SM-2)"
    return data


def _validate(data: dict) -> dict:
    def _int(key, low, high):
        val = int(data.get(key, DEFAULT_PLAN_CONFIG.get(key, 1)))
        if not low <= val <= high:
            raise ValueError(f"{key} 需在 {low}–{high} 之间")
        return val

    out = {
        "name": str(data.get("name") or "默认计划")[:120],
        "new_per_day": _int("new_per_day", 0, 20),
        "review_per_day": _int("review_per_day", 0, 20),
        "review_delay_days": _int("review_delay_days", 1, 90),
        "review_repeat": _int("review_repeat", 1, 10),
        "selection_strategy": data.get("selection_strategy", "earliest_due"),
        "review_mode": data.get("review_mode", "fixed"),
        "start_date": parse_date(data.get("start_date")) or date.today(),
        "rest_weekdays": [int(x) for x in (data.get("rest_weekdays") or []) if 0 <= int(x) <= 6],
        "pause_ranges": list(data.get("pause_ranges") or []),
    }
    if out["selection_strategy"] not in STRATEGIES:
        raise ValueError("selection_strategy 不合法")
    if out["review_mode"] not in REVIEW_MODES:
        raise ValueError("review_mode 不合法")
    for rng in out["pause_ranges"]:
        if "from" not in rng or "to" not in rng:
            raise ValueError("pause_ranges 元素需含 from / to")
    return out


def update_config(db: Session, payload: dict) -> dict:
    """参数变更生成新的配置版本；已生成的 daily_plans 保持原 config_id 与快照。"""
    current = active_config(db)
    merged = {k: getattr(current, k) for k in CONFIG_FIELDS}
    merged.update({k: v for k, v in payload.items() if k in CONFIG_FIELDS and v is not None})
    clean = _validate(merged)
    current.is_active = False
    new_cfg = PlanConfig(**clean, is_active=True)
    db.add(new_cfg)
    db.commit()
    db.refresh(new_cfg)
    return config_dict(new_cfg)


def _is_off_day(cfg: PlanConfig, day: date) -> bool:
    if day.weekday() in (cfg.rest_weekdays or []):
        return True
    for rng in cfg.pause_ranges or []:
        try:
            lo = parse_date(rng.get("from"))
            hi = parse_date(rng.get("to"))
            if lo and hi and lo <= day <= hi:
                return True
        except (TypeError, ValueError):
            continue
    return False


def active_list(db: Session) -> ProblemList | None:
    return db.scalar(
        select(ProblemList).where(
            ProblemList.is_active.is_(True), ProblemList.status == "confirmed"
        ).order_by(ProblemList.id.desc())
    )


def queue_remaining(db: Session) -> int:
    plist = active_list(db)
    if plist is None:
        return 0
    return int(db.scalar(
        select(func.count()).select_from(ProblemListItem).where(
            ProblemListItem.list_id == plist.id, ProblemListItem.consumed.is_(False)
        )
    ) or 0)


def _review_counts(db: Session, source_ids: list[int]) -> dict[int, int]:
    if not source_ids:
        return {}
    rows = db.execute(
        select(DailyPlanItem.source_plan_item_id, func.count())
        .where(
            DailyPlanItem.item_type == "review",
            DailyPlanItem.source_plan_item_id.in_(source_ids),
            DailyPlanItem.status != "skipped",
        )
        .group_by(DailyPlanItem.source_plan_item_id)
    ).all()
    return {int(k): int(v) for k, v in rows}


def _mastery_map(db: Session, problem_ids: list[int]) -> dict[int, float]:
    if not problem_ids:
        return {}
    rows = db.execute(
        select(StudyRecord.problem_id, func.avg(StudyRecord.mastery))
        .where(StudyRecord.problem_id.in_(problem_ids))
        .group_by(StudyRecord.problem_id)
    ).all()
    return {int(k): float(v) for k, v in rows}


def _pick_review(
    db: Session, cfg: PlanConfig, source_pool: list[DailyPlanItem],
    day: date, used_problems: set[int],
) -> list[DailyPlanItem]:
    """挑选当日复习题（固定节奏 / 动态间隔两条分支）。"""
    if cfg.review_per_day <= 0:
        return []

    if cfg.review_mode == "dynamic":
        states = db.scalars(
            select(ReviewState).where(
                ReviewState.next_due_date.is_not(None),
                ReviewState.next_due_date <= day,
            ).order_by(ReviewState.next_due_date, ReviewState.id)
        ).all()
        problems = {p.id: p for p in db.scalars(
            select(Problem).where(Problem.id.in_([s.problem_id for s in states]))
        ).all()} if states else {}
        out = []
        for st in states:
            if st.problem_id in used_problems or st.problem_id not in problems:
                continue
            # 为动态模式构造一个「虚拟来源」，实际 source_plan_item_id 取该问题最近的新题任务
            src = db.scalar(
                select(DailyPlanItem).where(
                    DailyPlanItem.problem_id == st.problem_id,
                    DailyPlanItem.item_type == "new",
                ).order_by(DailyPlanItem.id.desc())
            )
            if src is None:
                continue
            out.append(src)
            used_problems.add(st.problem_id)
            if len(out) >= cfg.review_per_day:
                break
        return out

    cutoff = day - timedelta(days=cfg.review_delay_days)
    candidates = [
        s for s in source_pool
        if s.due_date is not None
        and s.due_date <= cutoff
        and s.status not in ("skipped",)
        and s.problem_id not in used_problems
    ]
    counts = _review_counts(db, [s.id for s in candidates])
    candidates = [s for s in candidates if counts.get(s.id, 0) < cfg.review_repeat]

    # 同一原题在同一天不重复出现
    seen: set[int] = set()
    unique: list[DailyPlanItem] = []
    for s in candidates:
        if s.problem_id in seen:
            continue
        seen.add(s.problem_id)
        unique.append(s)

    if cfg.selection_strategy == "random":
        random.shuffle(unique)
    elif cfg.selection_strategy == "weak_first":
        mastery = _mastery_map(db, [s.problem_id for s in unique])
        unique.sort(key=lambda s: (mastery.get(s.problem_id, 3), s.due_date or day, s.id))
    else:
        unique.sort(key=lambda s: (s.due_date or day, s.id))

    chosen = unique[: cfg.review_per_day]
    for s in chosen:
        used_problems.add(s.problem_id)
    return chosen


def generate(db: Session, days: int = 30, from_date: date | None = None) -> dict:
    """预生成 N 天计划（幂等：已存在 plan_date 的日期直接跳过）。"""
    days = max(1, min(int(days), 365))
    cfg = active_config(db)
    start = from_date or cfg.start_date
    end = start + timedelta(days=days - 1)

    existing = set(db.scalars(
        select(DailyPlan.plan_date).where(DailyPlan.plan_date >= start, DailyPlan.plan_date <= end)
    ).all())

    plist = active_list(db)
    queue: list[ProblemListItem] = []
    if plist:
        queue = list(db.scalars(
            select(ProblemListItem).where(
                ProblemListItem.list_id == plist.id,
                ProblemListItem.consumed.is_(False),
            ).order_by(ProblemListItem.position, ProblemListItem.id)
        ).all())
    queue_idx = 0

    # 历史已生成的新题任务也要参与复习候选
    source_pool = list(db.scalars(
        select(DailyPlanItem).join(DailyPlan, DailyPlanItem.plan_id == DailyPlan.id)
        .where(DailyPlanItem.item_type == "new", DailyPlan.plan_date <= end)
        .order_by(DailyPlan.plan_date, DailyPlanItem.id)
    ).all())

    snapshot = {k: (getattr(cfg, k).isoformat() if k == "start_date" else getattr(cfg, k))
                for k in CONFIG_FIELDS}
    plans_created = 0
    items_created = 0
    shortage: list[str] = []

    for offset in range(days):
        day = start + timedelta(days=offset)
        if day in existing:
            continue
        day_index = (day - cfg.start_date).days + 1
        if day_index < 1:
            continue

        plan = DailyPlan(
            plan_date=day, config_id=cfg.id, generated_at=_utcnow(),
            config_snapshot=snapshot, note="",
        )
        db.add(plan)
        db.flush()
        plans_created += 1

        if _is_off_day(cfg, day):
            plan.note = "休息日 / 暂停区间"
            continue

        notes: list[str] = []
        for pos in range(cfg.new_per_day):
            if queue_idx >= len(queue):
                notes.append("题单不足：未从确认题单补题")
                break
            queue_item = queue[queue_idx]
            queue_idx += 1
            queue_item.consumed = True
            item = DailyPlanItem(
                plan_id=plan.id, item_type="new", problem_id=queue_item.problem_id,
                status="pending", due_date=day, position=pos,
            )
            db.add(item)
            db.flush()
            items_created += 1
            source_pool.append(item)

        if day_index > cfg.review_delay_days:
            used = set()
            for pos, src in enumerate(_pick_review(db, cfg, source_pool, day, used)):
                item = DailyPlanItem(
                    plan_id=plan.id, item_type="review", problem_id=src.problem_id,
                    status="pending", due_date=day, source_plan_item_id=src.id,
                    position=cfg.new_per_day + pos,
                )
                db.add(item)
                items_created += 1
                # 供后续日期判断「已排过几次复习」：刷新 source_pool 中的计数
                db.flush()

        if notes:
            plan.note = "；".join(dict.fromkeys(notes))
            shortage.append(day.isoformat())

    db.commit()
    return {
        "generated_plans": plans_created,
        "generated_items": items_created,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "shortage_dates": shortage,
        "queue_remaining": queue_remaining(db),
        "config": config_dict(cfg),
    }


def regenerate(db: Session, days: int = 30, from_date: date | None = None) -> dict:
    """重建未来 N 天计划（用户主动点击「重建未来计划」时调用）。

    与 :func:`generate` 的区别：
    - 窗口**从今天起算**，而不是配置的 ``start_date``；改过起始日期也不会只覆盖到过去；
    - 已存在的日期**只补齐缺口**（按当前配置补足 new / review 任务），
      不删除、不改任何已有任务，学习记录、笔记、已完成状态全部保留；
    - 仍遵守 FR-10：题单队列耗尽只标记「题单不足」，**不从题库随机补题**；
    - 补齐后再调用一次不会产生重复任务（幂等），因此不影响 TC-02 / TC-04 的语义。

    TC-04 的「参数变更不回溯」依然成立：单纯改配置不会自动改动历史计划，
    只有用户显式调用本函数才按新配置补齐未来计划。
    """
    days = max(1, min(int(days), 365))
    cfg = active_config(db)
    start = from_date or date.today()
    end = start + timedelta(days=days - 1)

    plist = active_list(db)
    queue: list[ProblemListItem] = []
    if plist:
        queue = list(db.scalars(
            select(ProblemListItem).where(
                ProblemListItem.list_id == plist.id,
                ProblemListItem.consumed.is_(False),
            ).order_by(ProblemListItem.position, ProblemListItem.id)
        ).all())
    queue_idx = 0

    source_pool = list(db.scalars(
        select(DailyPlanItem).join(DailyPlan, DailyPlanItem.plan_id == DailyPlan.id)
        .where(DailyPlanItem.item_type == "new", DailyPlan.plan_date <= end)
        .order_by(DailyPlan.plan_date, DailyPlanItem.id)
    ).all())

    snapshot = {k: (getattr(cfg, k).isoformat() if k == "start_date" else getattr(cfg, k))
                for k in CONFIG_FIELDS}
    plans_created = 0
    items_created = 0
    shortage: list[str] = []
    refilled: list[str] = []

    for offset in range(days):
        day = start + timedelta(days=offset)
        day_index = (day - cfg.start_date).days + 1
        if day_index < 1:
            continue

        plan = db.scalar(select(DailyPlan).where(DailyPlan.plan_date == day))
        created = False
        if plan is None:
            plan = DailyPlan(
                plan_date=day, config_id=cfg.id, generated_at=_utcnow(),
                config_snapshot=snapshot, note="",
            )
            db.add(plan)
            db.flush()
            plans_created += 1
            created = True
        elif plan.config_snapshot is None:
            plan.config_snapshot = snapshot

        if _is_off_day(cfg, day):
            if created:
                plan.note = "休息日 / 暂停区间"
            continue

        existing = list(db.scalars(
            select(DailyPlanItem).where(DailyPlanItem.plan_id == plan.id)
            .order_by(DailyPlanItem.position, DailyPlanItem.id)
        ).all())
        have_new = sum(1 for i in existing if i.item_type == "new")
        have_review = sum(1 for i in existing if i.item_type == "review")
        need_new = max(0, cfg.new_per_day - have_new)
        need_review = max(0, cfg.review_per_day - have_review) if day_index > cfg.review_delay_days else 0
        if created or need_new or need_review:
            refilled.append(day.isoformat())

        used = {i.problem_id for i in existing}
        position = max((i.position for i in existing), default=-1) + 1
        notes: list[str] = []

        for _ in range(need_new):
            if queue_idx >= len(queue):
                notes.append("题单不足：未从确认题单补题")
                break
            queue_item = queue[queue_idx]
            queue_idx += 1
            queue_item.consumed = True
            item = DailyPlanItem(
                plan_id=plan.id, item_type="new", problem_id=queue_item.problem_id,
                status="pending", due_date=day, position=position,
            )
            position += 1
            db.add(item)
            db.flush()
            items_created += 1
            source_pool.append(item)
            used.add(queue_item.problem_id)

        if need_review:
            picked = _pick_review(db, cfg, source_pool, day, used)[:need_review]
            for src in picked:
                db.add(DailyPlanItem(
                    plan_id=plan.id, item_type="review", problem_id=src.problem_id,
                    status="pending", due_date=day, source_plan_item_id=src.id,
                    position=position,
                ))
                position += 1
                db.flush()
                items_created += 1

        if notes:
            merged = [n for n in (plan.note or "").split("；") if n]
            merged.extend(notes)
            plan.note = "；".join(dict.fromkeys(merged))
            if day.isoformat() not in shortage:
                shortage.append(day.isoformat())

    db.commit()

    remaining = queue_remaining(db)
    needed = days * max(0, cfg.new_per_day)
    per_day = max(1, cfg.new_per_day)
    hint = (
        f"窗口 {start.isoformat()} ~ {end.isoformat()}；按每日 {cfg.new_per_day} 道新题，"
        f"{days} 天约需 {needed} 道，题单剩余 {remaining} 道，可完整覆盖约 {remaining // per_day} 天。"
    )
    if shortage:
        hint += (
            " 已按 FR-10 把题单耗尽的日期标记为「题单不足」，未从题库随机补题。"
            "建议：到「大纲题单」生成并确认新题单、到「题号采集」扩充题库，或在「设置」调低每日新题数。"
        )
    return {
        "mode": "regenerate",
        "from": start.isoformat(),
        "to": end.isoformat(),
        "generated_plans": plans_created,
        "generated_items": items_created,
        "refilled_dates": refilled,
        "shortage_dates": shortage,
        "queue_remaining": remaining,
        "hint": hint,
        "config": config_dict(cfg),
    }


def refill_queue(db: Session, limit: int = 500) -> dict:
    """从题库补充新题队列：把「未进激活题单、也未出现在任何计划任务」的题目追加进题单。

    用于题单耗尽时由用户显式触发（补充哪些题、补多少都在返回里可见），
    不改变任何已生成的任务，也不绕过 FR-08 的「确认后才进队列」——
    按钮点击本身就是一次确认。不会自动修改计划参数。
    """
    plist = active_list(db)
    if plist is None:
        raise ValidationError("还没有已确认并激活的题单，请先到「大纲题单」生成、确认并激活一份题单")

    in_list = set(db.scalars(
        select(ProblemListItem.problem_id).where(ProblemListItem.list_id == plist.id)
    ).all())
    in_plans = set(db.scalars(select(DailyPlanItem.problem_id)).all())
    used = in_list | in_plans

    candidates = list(db.scalars(
        select(Problem).where(Problem.is_active.is_(True))
        .order_by(Problem.difficulty_level, Problem.source, Problem.source_key, Problem.id)
    ).all())
    available = [p for p in candidates if p.id not in used]
    picked = available[: max(1, min(int(limit), 2000))]

    pos = int(db.scalar(
        select(func.max(ProblemListItem.position)).where(ProblemListItem.list_id == plist.id)
    ) or -1) + 1
    for p in picked:
        db.add(ProblemListItem(list_id=plist.id, problem_id=p.id, position=pos, consumed=False))
        pos += 1
    db.commit()

    remaining = queue_remaining(db)
    cfg = active_config(db)
    cover = remaining // max(1, cfg.new_per_day)
    hint = (
        f"从题库补入 {len(picked)} 道未排过的题（题库共 {len(candidates)} 道，"
        f"此前已在题单或计划中 {len(used)} 道），题单现有 {remaining} 道；"
        f"按每日 {cfg.new_per_day} 道新题，约可再覆盖 {cover} 天。"
    )
    if not picked:
        hint += " 题库已无可补充的新题，请到「题号采集」录入更多题目，或在「设置」调低每日新题数，仅靠复习任务推进。"
    elif cover < 30:
        hint += f" 若要连续覆盖 30 天，建议在「设置」把每日新题调为 {max(1, remaining // 30)} 道左右。"
    return {
        "added": len(picked),
        "available": len(available),
        "queue_remaining": remaining,
        "estimated_days": cover,
        "hint": hint,
    }


def mark_overdue(db: Session) -> int:
    """历史未完成项标记 overdue；不补发任何任务（FR-12 / D-07）。"""
    today = date.today()
    rows = db.scalars(
        select(DailyPlanItem).where(
            DailyPlanItem.status == "pending",
            DailyPlanItem.due_date.is_not(None),
            DailyPlanItem.due_date < today,
        )
    ).all()
    for row in rows:
        row.status = "overdue"
    if rows:
        db.commit()
    return len(rows)


def ensure_today(db: Session) -> dict:
    """打开首页时保证今日有计划；必要时补齐从 start_date 到今日 + 14 天。"""
    today = date.today()
    cfg = active_config(db)
    exists = db.scalar(select(DailyPlan).where(DailyPlan.plan_date == today))
    if exists is None:
        span = max(30, (today - cfg.start_date).days + 15)
        generate(db, days=min(span, 365))
    mark_overdue(db)
    return today_detail(db, today)


def _item_dict(db: Session, item: DailyPlanItem) -> dict:
    problem = db.get(Problem, item.problem_id)
    out = model_dict(item)
    out["status_label"] = TASK_STATUS_LABELS.get(item.status, item.status)
    out["item_type_label"] = "新题 · 学题解" if item.item_type == "new" else "复习 · 独立重做"
    out["problem"] = model_dict(problem) if problem else None
    out["notes_count"] = int(db.scalar(
        select(func.count()).select_from(Note).where(Note.problem_id == item.problem_id)
    ) or 0)
    if item.source_plan_item_id:
        src = db.get(DailyPlanItem, item.source_plan_item_id)
        if src:
            out["source_due_date"] = src.due_date.isoformat() if src.due_date else None
            out["source_status"] = src.status
    return out


def today_detail(db: Session, day: date | None = None) -> dict:
    day = day or date.today()
    plan = db.scalar(select(DailyPlan).where(DailyPlan.plan_date == day))
    items = []
    if plan:
        items = db.scalars(
            select(DailyPlanItem).where(DailyPlanItem.plan_id == plan.id)
            .order_by(DailyPlanItem.position, DailyPlanItem.id)
        ).all()
    return {
        "date": day.isoformat(),
        "plan": model_dict(plan) if plan else None,
        "items": [_item_dict(db, i) for i in items],
        "config": config_dict(active_config(db)),
        "queue_remaining": queue_remaining(db),
    }


def update_status(db: Session, item_id: int, target: str, score: int | None = None) -> dict:
    """任务状态机：pending → learning → learned → ac → reviewed；任意状态可 skipped。"""
    if target not in STATUS_RANK and target != "skipped":
        raise ValueError(f"非法状态：{target}")
    item = db.get(DailyPlanItem, item_id)
    if item is None:
        raise LookupError(f"任务 {item_id} 不存在")
    current = item.status
    cur_rank = STATUS_RANK.get(current, 0)
    if target == "skipped":
        pass
    elif target == "pending" and current in ("skipped", "overdue"):
        pass  # 允许恢复被打回的任务
    elif STATUS_RANK[target] <= cur_rank and current != "overdue":
        raise ValueError(f"不允许从 {current} 回退到 {target}")
    elif STATUS_RANK[target] > cur_rank + 1 and current != "overdue":
        # 允许跳级（例如直接标 AC），但只允许前进
        pass

    item.status = target
    if target in ("learned", "ac", "reviewed"):
        item.completed_at = _utcnow()

    cfg = active_config(db)
    if target == "skipped":
        # 被跳过的新题不进入复习池：级联把引用它的待办复习任务也置为 skipped（TC-03）
        linked = db.scalars(
            select(DailyPlanItem).where(
                DailyPlanItem.source_plan_item_id == item.id,
                DailyPlanItem.status.in_(["pending", "overdue", "learning"]),
            )
        ).all()
        for r in linked:
            r.status = "skipped"
        state = db.scalar(select(ReviewState).where(ReviewState.problem_id == item.problem_id))
        if state:
            db.delete(state)
    elif STATUS_RANK.get(target, 0) >= LEARNED_RANK:
        state = db.scalar(select(ReviewState).where(ReviewState.problem_id == item.problem_id))
        if state is None:
            base = item.due_date or date.today()
            state = ReviewState(
                problem_id=item.problem_id,
                learned_at=base,
                interval_days=cfg.review_delay_days,
                next_due_date=base + timedelta(days=cfg.review_delay_days),
            )
            db.add(state)
        state.last_result = target

    if target == "reviewed" and cfg.review_mode == "dynamic" and score:
        state = db.scalar(select(ReviewState).where(ReviewState.problem_id == item.problem_id))
        if state:
            score = max(1, min(5, int(score)))
            state.last_score = score
            state.ease_factor = max(1.3, state.ease_factor + (0.1 - (5 - score) * (0.08 + (5 - score) * 0.02)))
            state.repetition += 1
            if score < 3:
                state.repetition = 0
                state.interval_days = 1
            elif state.repetition == 1:
                state.interval_days = 1 if score == 3 else cfg.review_delay_days
            else:
                state.interval_days = max(1, round(state.interval_days * state.ease_factor))
            state.next_due_date = date.today() + timedelta(days=state.interval_days)

    db.commit()
    db.refresh(item)
    return _item_dict(db, item)


def calendar(db: Session, date_from: date, date_to: date) -> list[dict]:
    plans = db.scalars(
        select(DailyPlan).where(DailyPlan.plan_date >= date_from, DailyPlan.plan_date <= date_to)
        .order_by(DailyPlan.plan_date)
    ).all()
    out = []
    for plan in plans:
        items = db.scalars(
            select(DailyPlanItem).where(DailyPlanItem.plan_id == plan.id)
            .order_by(DailyPlanItem.position)
        ).all()
        out.append({
            "date": plan.plan_date.isoformat(),
            "note": plan.note,
            "items": [_item_dict(db, i) for i in items],
        })
    return out
