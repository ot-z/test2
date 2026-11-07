"""TC-06 断网闭环、TC-07 快照往返、TC-10 AI 降级；
额外用例：settings.ai_config 密钥只返回掩码、syncer 断网保留本地数据并标记 stale。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.models import (
    DailyPlan, DailyPlanItem, KnowledgeNode, Note, PlanConfig, Problem,
    ProblemList, ProblemListItem, Setting, StudyRecord, SyncLog,
)
from app.services import catalog, exporter, plan, records
from app.services import settings as settings_svc
from app.services import syncer
from app.utils import mask_secret

START = date(2026, 11, 9)


def _count(db, model):
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _configure(db, **overrides):
    payload = dict(start_date=START.isoformat(), new_per_day=1, review_per_day=1,
                   review_delay_days=7, review_repeat=1)
    payload.update(overrides)
    return plan.update_config(db, payload)


def _plan_on(db, d):
    return db.scalar(select(DailyPlan).where(DailyPlan.plan_date == d))


def _items_on(db, d):
    return list(db.scalars(
        select(DailyPlanItem).join(DailyPlan, DailyPlanItem.plan_id == DailyPlan.id)
        .where(DailyPlan.plan_date == d)
        .order_by(DailyPlanItem.position, DailyPlanItem.id)
    ).all())


# ---------------------------------------------------------------- TC-06
def test_tc06_offline_closed_loop(client, db, monkeypatch):
    """不发起真实网络请求：打卡/笔记/导出全部成功；AI/同步接口离线可读降级而非 500。"""
    from app.services import ai as ai_mod

    def _offline(*_a, **_k):
        raise OSError("simulated offline: network unreachable")

    # 断网模拟：数据源抓取必失败；AI 客户端创建兜底（未配置 Key 时本就不会走到）
    monkeypatch.setattr(syncer, "_fetch_cf", _offline)
    monkeypatch.setattr(syncer, "_fetch_at", _offline)
    monkeypatch.setattr(ai_mod, "AsyncOpenAI", _offline)
    monkeypatch.setattr(ai_mod, "_client", _offline)

    class _FakeTime:  # 只替换 syncer 用到的 time，避免动全局 time.sleep
        @staticmethod
        def sleep(_seconds):
            return None

    monkeypatch.setattr(syncer, "time", _FakeTime)

    _configure(db)
    plan.generate(db, days=3, from_date=START)
    item = _items_on(db, START)[0]
    db.commit()

    # 打卡（PATCH 状态）
    r = client.patch(f"/api/plan/items/{item.id}", json={"status": "ac"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ac"

    # 写笔记（POST /api/notes）
    r = client.post("/api/notes", json={"problem_id": item.problem_id,
                                        "title": "离线笔记", "content_md": "断网也能写"})
    assert r.status_code == 200, r.text
    assert r.json()["content_md"] == "断网也能写"

    # 导出 Markdown（POST /api/exports）
    r = client.post("/api/exports", json={"date_from": START.isoformat(),
                                          "date_to": (START + timedelta(days=2)).isoformat(),
                                          "format": "md"})
    assert r.status_code == 200, r.text
    md = Path(r.json()["file_path"]).read_text(encoding="utf-8")
    assert "刷题复盘报告" in md

    # AI 离线降级：SSE 中带 NOT_CONFIGURED 错误事件，无异常堆栈
    r = client.post("/api/ai/analyze", json={"content": "离线题面"})
    assert r.status_code == 200, r.text
    assert "NOT_CONFIGURED" in r.text
    assert "Traceback" not in r.text

    # 同步离线降级：ok=false，保留本地目录并标记 stale
    r = client.post("/api/sync/codeforces", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "stale" in body["message"]

    # 其它 AI 接口：可读降级提示，不是 500
    for path, payload in (("/api/ai/test", {}),
                          ("/api/ai/suggest-tags", {"content": "x"})):
        rr = client.post(path, json=payload)
        assert rr.status_code != 500, f"{path} 不应返回 500: {rr.text}"
        assert ("NOT_CONFIGURED" in rr.text) or rr.status_code == 503


# ---------------------------------------------------------------- TC-07
def test_tc07_snapshot_roundtrip(db):
    """snapshot_export -> clear_business_data -> snapshot_import：
    题目、知识点、计划、记录、笔记条数与关键字段一致。"""
    _configure(db)
    plan.generate(db, days=3, from_date=START)
    item = _items_on(db, START)[0]
    records.upsert_record(db, {
        "problem_id": item.problem_id, "plan_item_id": item.id,
        "memo": "关键记忆点", "time_spent_min": 25, "mastery": 4,
        "code_snippet": "int main(){return 0;}",
    })
    records.create_note(db, {"problem_id": item.problem_id, "title": "思路",
                             "content_md": "## 关键字段 ABC"})
    db.commit()

    models = (Problem, KnowledgeNode, PlanConfig, DailyPlan, DailyPlanItem,
              StudyRecord, Note, ProblemList, ProblemListItem, Setting)
    before = {m.__tablename__: _count(db, m) for m in models}
    titles = sorted(p.title for p in db.scalars(select(Problem)).all())
    urls = sorted(p.url for p in db.scalars(select(Problem)).all())
    notes = [n.content_md for n in db.scalars(select(Note)).all()]
    memos = [r.memo for r in db.scalars(select(StudyRecord)).all()]
    snapshots = [_plan_on(db, START + timedelta(days=i)).config_snapshot for i in range(3)]

    snap = exporter.snapshot_export(db)
    assert snap["schema_version"] == "1.0"

    exporter.clear_business_data(db)
    db.expunge_all()
    assert all(_count(db, m) == 0 for m in models), "清空后业务表应为空"

    res = exporter.snapshot_import(db, snap)
    assert res["cleared"] is True
    db.expunge_all()

    after = {m.__tablename__: _count(db, m) for m in models}
    assert after == before
    assert sorted(p.title for p in db.scalars(select(Problem)).all()) == titles
    assert sorted(p.url for p in db.scalars(select(Problem)).all()) == urls
    assert [n.content_md for n in db.scalars(select(Note)).all()] == notes
    assert [r.memo for r in db.scalars(select(StudyRecord)).all()] == memos
    assert [_plan_on(db, START + timedelta(days=i)).config_snapshot
            for i in range(3)] == snapshots


# ---------------------------------------------------------------- TC-10
def test_tc10_ai_degradation(client, db, monkeypatch):
    """DEEPSEEK_API_KEY 为空：/api/health 的 ai.status == not_configured，
    /api/plan/today 正常，/api/ai/analyze 的 SSE 带 code=NOT_CONFIGURED 错误事件。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")

    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["ai"]["status"] == "not_configured"

    r = client.get("/api/plan/today")
    assert r.status_code == 200, r.text
    assert "items" in r.json() and "config" in r.json()

    r = client.post("/api/ai/analyze", json={"content": "题目内容", "problem_id": 1})
    assert r.status_code == 200, r.text
    assert "NOT_CONFIGURED" in r.text
    assert "Traceback" not in r.text
    assert "sk-" not in r.text  # 不回显任何密钥片段


# ---------------------------------------------------------------- 额外：密钥掩码
def test_extra_ai_config_masks_secret(client, db, monkeypatch):
    """settings.ai_config 只返回密钥掩码，API 响应也不回显明文。"""
    # 占位串刻意不带连续 8 位字母数字，避免被 TC-08 的全仓密钥扫描误判
    secret = "sk-TEST-KEY-ONLY"
    monkeypatch.setattr(settings_svc, "read_api_key", lambda: secret)

    cfg = settings_svc.ai_config(db)
    assert cfg["api_key_configured"] is True
    assert cfg["api_key_masked"] == mask_secret(secret)
    assert secret not in json.dumps(cfg, ensure_ascii=False)
    assert "********" in cfg["api_key_masked"]

    r = client.get("/api/settings/ai")
    assert r.status_code == 200, r.text
    assert secret not in r.text
    assert cfg["api_key_masked"] in r.text


# ---------------------------------------------------------------- 额外：同步断网
def test_extra_sync_offline_keeps_local_and_marks_stale(db, monkeypatch):
    """monkeypatch 让 _fetch_cf 抛异常：sync 返回 ok=false，保留本地题目并标记 stale。"""
    created = catalog.create_problem(db, {
        "source": "codeforces", "source_key": "999Z",
        "title": "本地 CF 题", "difficulty_level": 2,
    })
    before = _count(db, Problem)

    def _offline(*_a, **_k):
        raise OSError("simulated offline")

    monkeypatch.setattr(syncer, "_fetch_cf", _offline)

    class _FakeTime:
        @staticmethod
        def sleep(_seconds):
            return None

    monkeypatch.setattr(syncer, "time", _FakeTime)

    res = syncer.sync(db, "codeforces")
    assert res["ok"] is False
    assert _count(db, Problem) == before  # 不清空本地目录
    db.expire_all()
    local = db.get(Problem, created["id"])
    assert local is not None
    assert local.sync_status == "stale"
    assert "stale" in res["message"]

    log = db.scalar(select(SyncLog).order_by(SyncLog.id.desc()))
    assert log is not None and log.status == "stale"
