"""ORM 数据模型 —— 对应 SRS 第 6 节 + 附录 C.2（problem_snapshots）。

设计要点：
- 题目表只存元信息，不含题面/样例/题解原文（NFR-05）。
- 每日计划是快照：daily_plans.config_snapshot 固化生成时的参数（FR-09）。
- review 任务通过 source_plan_item_id 指回当初的新题任务（FR-10 / TC-01）。
- review_state 预留 SM-2 动态复习字段，M4 接入时无需改表（FR-13）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .db import Base, TimestampMixin, _utcnow


class Setting(Base, TimestampMixin):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, default=dict, nullable=False)


class KnowledgeNode(Base, TimestampMixin):
    __tablename__ = "knowledge_nodes"
    __table_args__ = (Index("ix_kn_parent", "parent_id"), Index("ix_kn_category", "category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="RESTRICT"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="knowledge", nullable=False)  # knowledge/data_structure/algorithm
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    children: Mapped[list["KnowledgeNode"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", passive_deletes=False
    )
    parent: Mapped["KnowledgeNode | None"] = relationship(back_populates="children", remote_side=[id])


class Problem(Base, TimestampMixin):
    __tablename__ = "problems"
    __table_args__ = (
        UniqueConstraint("source", "source_key", name="uq_problem_source_key"),
        Index("ix_problem_filter", "is_active", "difficulty_level", "source"),
        Index("ix_problem_title", "title"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_key: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    difficulty_raw: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    difficulty_level: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    external_tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 载体 B 的 AI 摘要（非题面原文）
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    manual_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_status: Mapped[str] = mapped_column(String(24), default="manual", nullable=False)  # manual/synced/stale
    solved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ProblemKnowledge(Base, TimestampMixin):
    __tablename__ = "problem_knowledge"
    __table_args__ = (UniqueConstraint("problem_id", "node_id", name="uq_pk"), Index("ix_pk_verified", "verified", "confidence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), nullable=False)
    node_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False)
    confidence: Mapped[str] = mapped_column(String(24), default="manual", nullable=False)  # manual/mapped/ai
    verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TagMapping(Base, TimestampMixin):
    __tablename__ = "tag_mappings"
    __table_args__ = (UniqueConstraint("source", "external_tag", "node_id", name="uq_tag_map"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="*", nullable=False)
    external_tag: Mapped[str] = mapped_column(String(120), nullable=False)
    node_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False)


class Syllabus(Base, TimestampMixin):
    __tablename__ = "syllabi"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_syllabus_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    track: Mapped[str] = mapped_column(String(120), default="软件赛", nullable=False)
    group_name: Mapped[str] = mapped_column(String(120), default="大学 B 组", nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    items: Mapped[list["SyllabusItem"]] = relationship(back_populates="syllabus", cascade="all, delete-orphan")


class SyllabusItem(Base, TimestampMixin):
    __tablename__ = "syllabus_items"
    __table_args__ = (Index("ix_si_syllabus", "syllabus_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    syllabus_id: Mapped[int] = mapped_column(ForeignKey("syllabi.id", ondelete="CASCADE"), nullable=False)
    node_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False)
    difficulty_min: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    difficulty_max: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    coefficient: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # 大纲难度系数 1-10

    syllabus: Mapped["Syllabus"] = relationship(back_populates="items")


class ProblemList(Base, TimestampMixin):
    __tablename__ = "problem_lists"
    __table_args__ = (Index("ix_pl_status", "status", "is_active"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    syllabus_id: Mapped[int | None] = mapped_column(ForeignKey("syllabi.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)  # draft/confirmed
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["ProblemListItem"]] = relationship(
        back_populates="problem_list", cascade="all, delete-orphan", order_by="ProblemListItem.position"
    )


class ProblemListItem(Base, TimestampMixin):
    __tablename__ = "problem_list_items"
    __table_args__ = (UniqueConstraint("list_id", "problem_id", name="uq_pli"), Index("ix_pli_consumed", "list_id", "consumed"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("problem_lists.id", ondelete="CASCADE"), nullable=False)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    problem_list: Mapped["ProblemList"] = relationship(back_populates="items")


class PlanConfig(Base, TimestampMixin):
    __tablename__ = "study_plan_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="默认计划", nullable=False)
    new_per_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    review_per_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    review_delay_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    review_repeat: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    selection_strategy: Mapped[str] = mapped_column(String(32), default="earliest_due", nullable=False)
    review_mode: Mapped[str] = mapped_column(String(32), default="fixed", nullable=False)  # fixed/dynamic
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    rest_weekdays: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    pause_ranges: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class DailyPlan(Base, TimestampMixin):
    __tablename__ = "daily_plans"
    __table_args__ = (UniqueConstraint("plan_date", name="uq_daily_plan_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    config_id: Mapped[int | None] = mapped_column(ForeignKey("study_plan_configs.id", ondelete="SET NULL"), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 如「题单不足」

    items: Mapped[list["DailyPlanItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="DailyPlanItem.position"
    )


class DailyPlanItem(Base, TimestampMixin):
    __tablename__ = "daily_plan_items"
    __table_args__ = (
        Index("ix_dpi_plan", "plan_id"),
        Index("ix_dpi_status", "status", "due_date"),
        Index("ix_dpi_source", "source_plan_item_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("daily_plans.id", ondelete="CASCADE"), nullable=False)
    item_type: Mapped[str] = mapped_column(String(16), default="new", nullable=False)  # new/review
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_plan_item_id: Mapped[int | None] = mapped_column(ForeignKey("daily_plan_items.id", ondelete="SET NULL"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    plan: Mapped["DailyPlan"] = relationship(back_populates="items")


class ReviewState(Base, TimestampMixin):
    __tablename__ = "review_state"
    __table_args__ = (UniqueConstraint("problem_id", name="uq_review_state_problem"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), nullable=False)
    repetition: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ease_factor: Mapped[float] = mapped_column(Float, default=2.5, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    next_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    learned_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_result: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    last_score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class StudyRecord(Base, TimestampMixin):
    __tablename__ = "study_records"
    __table_args__ = (Index("ix_sr_problem", "problem_id"), Index("ix_sr_item", "plan_item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_item_id: Mapped[int | None] = mapped_column(ForeignKey("daily_plan_items.id", ondelete="SET NULL"), nullable=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), nullable=False)
    result: Mapped[str] = mapped_column(String(40), default="done", nullable=False)
    time_spent_min: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    language: Mapped[str] = mapped_column(String(40), default="C++", nullable=False)
    code_snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mastery: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    memo: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Note(Base, TimestampMixin):
    __tablename__ = "notes"
    __table_args__ = (Index("ix_note_problem", "problem_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="思路总结", nullable=False)
    content_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_auto_saved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AiCall(Base, TimestampMixin):
    __tablename__ = "ai_calls"
    __table_args__ = (Index("ix_aicall_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scene: Mapped[str] = mapped_column(String(40), nullable=False)  # analyze/debug/suggest_tags
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    problem_id: Mapped[int | None] = mapped_column(ForeignKey("problems.id", ondelete="SET NULL"), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ok", nullable=False)  # ok/error/aborted
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    messages: Mapped[list["AiMessage"]] = relationship(
        back_populates="call", cascade="all, delete-orphan", order_by="AiMessage.seq"
    )


class AiMessage(Base, TimestampMixin):
    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("ai_calls.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    call: Mapped["AiCall"] = relationship(back_populates="messages")


class ExportRecord(Base, TimestampMixin):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    format: Mapped[str] = mapped_column(String(16), default="md", nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    include_ai: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    include_code: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SyncLog(Base, TimestampMixin):
    __tablename__ = "sync_logs"
    __table_args__ = (Index("ix_sync_source", "source", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)  # running/ok/failed/stale
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)


class ProblemSnapshot(Base, TimestampMixin):
    """FR-28：用户主动触发的单题题面本地快照。

    边界：仅本地 SQLite；不参与 FR-22 文档导出；不由批量导入自动生成。
    """
    __tablename__ = "problem_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    user_initiated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


ALL_TABLES = [
    Setting, KnowledgeNode, Problem, ProblemKnowledge, TagMapping,
    Syllabus, SyllabusItem, ProblemList, ProblemListItem,
    PlanConfig, DailyPlan, DailyPlanItem, ReviewState,
    StudyRecord, Note, AiCall, AiMessage, ExportRecord, SyncLog, ProblemSnapshot,
]
