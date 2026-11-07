"""TC-08 密钥安全、TC-09 回退演练；以及测试环境隔离断言。"""
from __future__ import annotations

import re
import subprocess
from datetime import date, timedelta

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import func, select

from app import seed
from app.config import ROOT
from app.db import SessionLocal, engine
from app.models import (
    DailyPlan, DailyPlanItem, KnowledgeNode, Note, PlanConfig, Problem,
)
from app.services import exporter, plan, records

START = date(2026, 11, 9)


def _count(db, model):
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _configure(db, **overrides):
    payload = dict(start_date=START.isoformat(), new_per_day=1, review_per_day=1,
                   review_delay_days=7, review_repeat=1)
    payload.update(overrides)
    return plan.update_config(db, payload)


def _items_on(db, d):
    return list(db.scalars(
        select(DailyPlanItem).join(DailyPlan, DailyPlanItem.plan_id == DailyPlan.id)
        .where(DailyPlan.plan_date == d)
        .order_by(DailyPlanItem.position, DailyPlanItem.id)
    ).all())


def test_environment_isolation_uses_temp_db():
    """证明 app.config / app.db 连的是 pytest 临时库，没有污染真实 data/quiz.db。"""
    from app.config import DATABASE_PATH, DATABASE_URL
    assert "quiz_acceptance_" in str(DATABASE_PATH)
    assert "quiz_acceptance_" in DATABASE_URL
    assert "test.db" in DATABASE_URL
    assert "data/quiz.db" not in DATABASE_URL
    assert "quiz_acceptance_" in str(engine.url)


def test_tc08_secret_safety():
    """扫描被 git 跟踪的文本文件无 sk- 明文密钥；.gitignore 含 .env 且排除原始方案 txt。"""
    raw = subprocess.check_output(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z"], cwd=str(ROOT))
    files = [b.decode("utf-8") for b in raw.split(b"\0") if b]
    assert any(f.endswith(".gitignore") for f in files)
    assert any(f.startswith("docs/") for f in files)

    pattern = re.compile(r"sk-[A-Za-z0-9]{8,}")
    scanned = 0
    for rel in files:
        p = ROOT / rel
        if not p.is_file():
            continue
        scanned += 1
        text = p.read_text(encoding="utf-8", errors="ignore")
        m = pattern.search(text)
        assert m is None, f"被 git 跟踪的文件 {rel} 含明文密钥: {m.group()}"
    assert scanned >= 4

    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gi
    assert "刷题助手第一次规划.txt" in gi  # 含明文密钥的原始方案 txt 被排除

    tracked = set(files)
    assert ".env" not in tracked
    assert ".env.local" not in tracked
    assert "刷题助手第一次规划.txt" not in tracked  # 原始方案 txt 未被 git 跟踪


def test_tc09_rollback_drill(client, db, wipe_db):
    """先导出 JSON 快照，再 alembic downgrade(base)/upgrade(head) 删表重建，
    重新导入快照后：/api/health 返回 ok 且数据条数一致。"""
    cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))

    # 重置为由 alembic 单独管理的干净库（含删除 alembic_version）
    wipe_db()
    command.upgrade(cfg, "head")
    db.expunge_all()

    # 重新装配业务数据（wipe 之后夹具已 seed 的内容被清空）
    seed.seed_all(db)
    _configure(db)
    plan.generate(db, days=3, from_date=START)
    first_item = _items_on(db, START)[0]
    records.create_note(db, {"problem_id": first_item.problem_id,
                             "content_md": "TC09 往返笔记"})
    db.commit()

    snap = exporter.snapshot_export(db)
    models = (Problem, KnowledgeNode, PlanConfig, DailyPlan, DailyPlanItem, Note)
    before = {m.__tablename__: _count(db, m) for m in models}
    titles = sorted(p.title for p in db.scalars(select(Problem)).all())
    db.close()

    # 回退演练：单一初始迁移的 downgrade 会删表，必须配合快照恢复
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    session = SessionLocal()
    try:
        result = exporter.snapshot_import(session, snap)
        assert result["total"] > 0
        session.expunge_all()
        after = {m.__tablename__: _count(session, m) for m in models}
        assert after == before
        assert sorted(p.title for p in session.scalars(select(Problem)).all()) == titles
    finally:
        session.close()

    # 应用可启动，且 health 中的条数与快照前一致
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["problems"] == before["problems"]
    assert r.json()["plan_items"] == before["daily_plan_items"]
    assert r.json()["notes"] == before["notes"]
