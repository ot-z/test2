"""「重建未来计划」验收测试（v1.1.1 修复）。

复现的缺陷：/api/plan/generate 窗口固定从 config.start_date 起算且整日幂等跳过，
用户改过起始日期与每日题量后点击「重新生成 30 天计划」永远返回 0 项，页面无变化。

修复后的契约：
- regenerate 窗口从今天起算（可显式 from_date）；
- 已有日期只补齐缺口，不删除/不修改任何已有任务（TC-04 不回溯仍成立）；
- 题单耗尽只标记「题单不足」，不从题库随机补题（FR-10）；
- 连续两次调用第二次不产生重复任务（幂等）。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.models import DailyPlan, DailyPlanItem, Problem, ProblemListItem, StudyRecord
from app.services import plan


def _items_on(db, day: date) -> list[DailyPlanItem]:
    p = db.scalar(select(DailyPlan).where(DailyPlan.plan_date == day))
    if p is None:
        return []
    return list(db.scalars(
        select(DailyPlanItem).where(DailyPlanItem.plan_id == p.id)
        .order_by(DailyPlanItem.position, DailyPlanItem.id)
    ).all())


def _count(db, model) -> int:
    return len(db.scalars(select(model)).all())


def _configure(db, **kw) -> None:
    today = date.today()
    payload = {
        "start_date": (today - timedelta(days=14)).isoformat(),
        "new_per_day": 1, "review_per_day": 1,
        "review_delay_days": 7, "review_repeat": 1,
        "selection_strategy": "earliest_due", "review_mode": "fixed",
    }
    payload.update(kw)
    plan.update_config(db, payload)


def test_regenerate_uses_today_window_and_fills_gap(db):
    """改过 start_date 后旧 generate 返回 0；regenerate 从今天起补齐并按新配置排题。"""
    today = date.today()
    _configure(db)
    first = plan.generate(db, days=30)
    assert first["from"] == (today - timedelta(days=14)).isoformat()
    assert first["generated_plans"] > 0

    # 复现缺陷：再点「重新生成 30 天计划」什么也没发生
    assert plan.generate(db, days=30)["generated_items"] == 0

    before_yesterday = len(_items_on(db, today - timedelta(days=1)))
    # start_date 在 14 天前，今日已进入「新题 + 复习」阶段
    assert sum(1 for i in _items_on(db, today) if i.item_type == "new") == 1

    # 用户把每日新题改成 3，再点「重建未来 30 天计划」
    plan.update_config(db, {"new_per_day": 3, "review_per_day": 3})
    r = plan.regenerate(db, days=30, from_date=today)

    assert r["mode"] == "regenerate"
    assert r["from"] == today.isoformat()
    assert r["to"] == (today + timedelta(days=29)).isoformat()
    assert r["generated_items"] > 0
    assert len(_items_on(db, today)) >= 3, "今日应补齐到 3 项"
    assert sum(1 for i in _items_on(db, today) if i.item_type == "new") == 3
    # 历史日期不被回溯修改（TC-04）
    assert len(_items_on(db, today - timedelta(days=1))) == before_yesterday


def test_regenerate_creates_missing_future_plans(db):
    """窗口超出既有计划覆盖范围时，缺失日期要新建并使用当前配置快照。"""
    today = date.today()
    _configure(db)
    plan.generate(db, days=30)  # 覆盖 start_date 起 30 天 = today+15
    far = today + timedelta(days=20)
    assert db.scalar(select(DailyPlan).where(DailyPlan.plan_date == far)) is None

    r = plan.regenerate(db, days=25, from_date=today)
    assert r["generated_plans"] > 0
    created = db.scalar(select(DailyPlan).where(DailyPlan.plan_date == far))
    assert created is not None
    assert created.config_snapshot["new_per_day"] == 1


def test_regenerate_preserves_existing_tasks_and_states(db):
    """补齐只新增，不删除、不改状态；学习记录与笔记不受影响。"""
    today = date.today()
    _configure(db)
    plan.generate(db, days=30)
    item = _items_on(db, today)[0]
    plan.update_status(db, item.id, "learned")
    db.add(StudyRecord(problem_id=item.problem_id, plan_item_id=item.id,
                       result="done", mastery=4, time_spent_min=12,
                       language="C++", code_snippet="", memo=""))
    db.commit()
    old_ids = {i.id for i in _items_on(db, today)}
    old_states = {i.id: i.status for i in _items_on(db, today)}

    plan.update_config(db, {"new_per_day": 3})
    plan.regenerate(db, days=1, from_date=today)

    current = _items_on(db, today)
    assert old_ids.issubset({i.id for i in current})
    assert all(old_states[i.id] == i.status for i in current if i.id in old_states)
    assert _count(db, StudyRecord) == 1


def test_regenerate_is_idempotent(db):
    """连续重建两次，第二次不产生新任务。"""
    today = date.today()
    _configure(db)
    plan.generate(db, days=30)
    plan.update_config(db, {"new_per_day": 3, "review_per_day": 3})
    first = plan.regenerate(db, days=10, from_date=today)
    assert first["generated_items"] > 0
    before = _count(db, DailyPlanItem)
    second = plan.regenerate(db, days=10, from_date=today)
    assert second["generated_items"] == 0
    assert _count(db, DailyPlanItem) == before


def test_regenerate_marks_shortage_without_random_fill(db):
    """FR-10：题单队列耗尽只标记「题单不足」，不从题库随机补题。"""
    today = date.today()
    _configure(db, new_per_day=3, review_per_day=0)
    for row in db.scalars(select(ProblemListItem)).all():
        row.consumed = True
    db.commit()

    new_before = _count(db, DailyPlanItem)
    r = plan.regenerate(db, days=5, from_date=today)
    assert r["shortage_dates"], "题单耗尽应标记 shortage"
    assert "题单不足" in r["hint"]
    news = [i for i in db.scalars(select(DailyPlanItem)).all() if i.item_type == "new"]
    assert news == [], "不得从题库随机补题"
    assert _count(db, DailyPlanItem) >= new_before  # 只可能新增 review，不删除
    p = db.scalar(select(DailyPlan).where(DailyPlan.plan_date == today))
    assert "题单不足" in (p.note or "")


def test_refill_queue_adds_only_unplanned_problems(db):
    """补充队列只取题库中未进激活题单、且未出现在任何计划任务里的题；可重复调用。"""
    today = date.today()
    _configure(db, new_per_day=2, review_per_day=0)
    # 模拟「题号采集」新录入、尚未进入题单的题目
    extra_ids = []
    for n in range(3):
        p = Problem(source="other", source_key=f"EXTRA-{n}", title=f"补充题 {n}",
                    url="", difficulty_raw="基础", difficulty_level=2,
                    external_tags=[], note="", summary="", is_active=True,
                    manual_edited=False, sync_status="manual")
        db.add(p)
        db.flush()
        extra_ids.append(p.id)
    db.commit()
    plan.generate(db, days=3, from_date=today)
    plist = plan.active_list(db)
    before = {r.problem_id for r in db.scalars(
        select(ProblemListItem).where(ProblemListItem.list_id == plist.id)).all()}
    planned = {i.problem_id for i in db.scalars(select(DailyPlanItem)).all()}
    tasks_before = len(db.scalars(select(DailyPlanItem)).all())
    cfg_before = plan.active_config(db).new_per_day

    r = plan.refill_queue(db)
    after = {x.problem_id for x in db.scalars(
        select(ProblemListItem).where(ProblemListItem.list_id == plist.id)).all()}
    assert r["added"] == 3
    assert r["queue_remaining"] >= r["added"]
    assert set(extra_ids).issubset(after - before)
    assert before.issubset(after)
    assert not (after - before) & planned, "不得把已排进计划的题再次加入队列"
    assert not (after - before) & before
    assert len(db.scalars(select(DailyPlanItem)).all()) == tasks_before
    assert plan.active_config(db).new_per_day == cfg_before, "不得擅自修改计划参数"

    again = plan.refill_queue(db)
    assert again["added"] == 0, "题库已无未排过的题，重复调用应幂等"


def test_api_regenerate_endpoint(db, client):
    """REST 端点可用；旧 generate 仍保持幂等语义。"""
    today = date.today()
    _configure(db)
    r = client.post("/api/plan/generate?days=30")
    assert r.status_code == 200
    assert r.json()["generated_plans"] > 0
    assert client.post("/api/plan/generate?days=30").json()["generated_items"] == 0

    plan.update_config(db, {"new_per_day": 2})
    r = client.post("/api/plan/regenerate?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "regenerate"
    assert body["from"] == today.isoformat()
    assert body["generated_items"] > 0
    assert client.get("/api/plan/today").json()["items"]
    assert "hint" in body

    r = client.post("/api/plan/refill-queue")
    assert r.status_code == 200
    assert {"added", "queue_remaining", "hint"}.issubset(r.json().keys())
