"""TC-01 ~ TC-04：计划节奏、幂等生成、顺延不堆积/跳过不入复习池、参数不回溯。

另含额外用例：plan.update_status 状态机非法回退报错。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models import DailyPlan, DailyPlanItem, PlanConfig
from app.services import plan

START = date(2026, 11, 9)


def _configure(db, **overrides):
    payload = dict(
        start_date=START.isoformat(),
        new_per_day=1,
        review_per_day=1,
        review_delay_days=7,
        review_repeat=1,
    )
    payload.update(overrides)
    return plan.update_config(db, payload)


def _count(db, model):
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _plan_on(db, d):
    return db.scalar(select(DailyPlan).where(DailyPlan.plan_date == d))


def _items_on(db, d):
    return list(db.scalars(
        select(DailyPlanItem).join(DailyPlan, DailyPlanItem.plan_id == DailyPlan.id)
        .where(DailyPlan.plan_date == d)
        .order_by(DailyPlanItem.position, DailyPlanItem.id)
    ).all())


def test_tc01_plan_rhythm(db):
    """第 1-7 天各 1 个 new；第 8-14 天各 2 个（1 new + 1 review）；
    第 8 天 review 指向第 1 天 new 且 problem_id 相同。"""
    _configure(db)
    plan.generate(db, days=14)

    for i in range(14):
        d = START + timedelta(days=i)
        items = _items_on(db, d)
        if i < 7:
            assert len(items) == 1, f"第 {i+1} 天应为 1 个 new，实际 {len(items)}"
            assert items[0].item_type == "new"
        else:
            assert len(items) == 2, f"第 {i+1} 天应为 2 个任务，实际 {len(items)}"
            kinds = [x.item_type for x in items]
            assert kinds.count("new") == 1 and kinds.count("review") == 1

    day1_new = _items_on(db, START)[0]
    day8_review = next(x for x in _items_on(db, START + timedelta(days=7))
                       if x.item_type == "review")
    assert day8_review.source_plan_item_id == day1_new.id
    assert day8_review.problem_id == day1_new.problem_id


def test_tc02_idempotent_generate(db):
    """连续两次 generate 同一区间，daily_plan_items 数量不变。"""
    _configure(db)
    plan.generate(db, days=14)
    before = _count(db, DailyPlanItem)
    assert before == 21  # 14 个 new + 7 个 review（第 8~14 天）
    plan.generate(db, days=14)
    after = _count(db, DailyPlanItem)
    assert after == before


def test_tc03_skip_no_pileup(db):
    """review_delay_days=1 构造「每天 1 新 + 1 复习」阶段；连续 3 天标记
    skipped 后，后续每天仍为 2 项、不出现 4+ 积压，且被跳过的原题不再
    被后续新生成的 review 引用。"""
    _configure(db, review_delay_days=1)
    plan.generate(db, days=14, from_date=START)

    # 连续把第 5、6、7 天的任务全部标 skipped
    for i in (4, 5, 6):
        for item in _items_on(db, START + timedelta(days=i)):
            plan.update_status(db, item.id, "skipped")

    skipped_new_ids = set()
    skipped_problem_ids = set()
    for i in (4, 5, 6):
        for item in _items_on(db, START + timedelta(days=i)):
            if item.item_type == "new":
                skipped_new_ids.add(item.id)
                skipped_problem_ids.add(item.problem_id)
    assert len(skipped_new_ids) == 3

    # 已生成的后续日期：任务数仍为 2，无堆积
    for i in range(7, 14):
        d = START + timedelta(days=i)
        assert len(_items_on(db, d)) == 2, f"{d} 应仍为 2 项"

    # 顺延不堆积：mark_overdue 只改状态，不补发任务
    total_before = _count(db, DailyPlanItem)
    plan.mark_overdue(db)
    assert _count(db, DailyPlanItem) == total_before

    # 再生成第 15~20 天（幂等跳过已存在日期）
    plan.generate(db, days=20, from_date=START)
    for i in range(14, 20):
        d = START + timedelta(days=i)
        items = _items_on(db, d)
        assert len(items) == 2, f"新生成 {d} 应为 2 项"
        for r in (x for x in items if x.item_type == "review"):
            assert r.source_plan_item_id not in skipped_new_ids, "review 引用了被跳过的原题任务"
            assert r.problem_id not in skipped_problem_ids, "review 引用了被跳过的原题"

    # 全程没有任何一天出现 4+ 项积压
    for i in range(20):
        assert len(_items_on(db, START + timedelta(days=i))) <= 2


def test_tc04_config_not_retroactive(db):
    """生成后把 new_per_day 改成 2（新 config 版本）：已生成日期任务数不变、
    config_snapshot 保留旧参数；新日期按 2 道 new 生成。"""
    _configure(db)
    plan.generate(db, days=7, from_date=START)
    old_dates = [START + timedelta(days=i) for i in range(7)]
    old_counts = {d: len(_items_on(db, d)) for d in old_dates}
    assert all(c == 1 for c in old_counts.values())
    cfg_count_before = _count(db, PlanConfig)

    plan.update_config(db, {"new_per_day": 2})
    assert _count(db, PlanConfig) == cfg_count_before + 1  # 创建了新 config 版本

    for d in old_dates:
        assert len(_items_on(db, d)) == old_counts[d] == 1
        assert _plan_on(db, d).config_snapshot["new_per_day"] == 1

    plan.generate(db, days=14, from_date=START)
    for i in range(7, 14):
        d = START + timedelta(days=i)
        items = _items_on(db, d)
        assert sum(1 for x in items if x.item_type == "new") == 2, f"{d} 应按 2 道 new 生成"
    assert _plan_on(db, START + timedelta(days=13)).config_snapshot["new_per_day"] == 2


def test_extra_status_machine_rejects_illegal_backward(db):
    """状态机：跳级前进允许；已完成状态回退到 pending 报错；非法状态报错。"""
    _configure(db)
    plan.generate(db, days=1, from_date=START)
    item = _items_on(db, START)[0]
    assert item.status == "pending"

    plan.update_status(db, item.id, "ac")  # pending -> ac：允许跳级前进
    with pytest.raises(ValueError):
        plan.update_status(db, item.id, "pending")  # ac -> pending：非法回退
    with pytest.raises(ValueError):
        plan.update_status(db, item.id, "not_a_status")  # 非法状态
