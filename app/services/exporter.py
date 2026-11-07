"""文档导出与 JSON 数据快照（FR-22 / FR-23 / FR-24 / TC-11 / NFR-05）。

安全边界：problem_snapshots（单题题面原文）**永不进入任何导出产物**，
包括 Markdown / DOCX / PDF 复盘文档与 JSON 数据快照。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.types import Date, DateTime, JSON

from ..config import EXPORT_DIR, SOURCES
from ..db import _utcnow
from ..errors import NotFound, ValidationError
from ..models import (
    AiCall, AiMessage, DailyPlan, DailyPlanItem, ExportRecord, KnowledgeNode,
    Note, PlanConfig, Problem, ProblemKnowledge, ProblemList, ProblemListItem,
    ReviewState, Setting, StudyRecord, Syllabus, SyllabusItem, SyncLog, TagMapping,
)
from ..utils import model_dict, parse_date

# 快照包含的表（按外键依赖顺序排列）；problem_snapshots 明确排除
SNAPSHOT_MODELS = [
    Setting, KnowledgeNode, TagMapping, Syllabus, SyllabusItem, Problem,
    ProblemKnowledge, ProblemList, ProblemListItem, PlanConfig, DailyPlan,
    DailyPlanItem, ReviewState, StudyRecord, Note, AiCall, AiMessage,
    ExportRecord, SyncLog,
]

DIFFICULTY_LABELS = {1: "入门", 2: "基础", 3: "进阶", 4: "提高", 5: "竞赛"}


def _serialize(obj) -> dict:
    data = model_dict(obj)
    for k, v in list(data.items()):
        if isinstance(v, (date, datetime)):
            data[k] = v.isoformat()
    return data


def collect(db: Session, date_from: date, date_to: date,
            include_ai: bool = True, include_code: bool = True) -> dict:
    plans = db.scalars(
        select(DailyPlan).where(DailyPlan.plan_date >= date_from, DailyPlan.plan_date <= date_to)
        .order_by(DailyPlan.plan_date)
    ).all()
    sections: list[dict] = []
    links: list[dict] = []
    total_items = 0
    done_items = 0

    for plan in plans:
        items = db.scalars(
            select(DailyPlanItem).where(DailyPlanItem.plan_id == plan.id)
            .order_by(DailyPlanItem.position, DailyPlanItem.id)
        ).all()
        day: dict[str, Any] = {"date": plan.plan_date.isoformat(), "note": plan.note, "items": []}
        for item in items:
            total_items += 1
            if item.status in ("learned", "ac", "reviewed"):
                done_items += 1
            problem = db.get(Problem, item.problem_id)
            if problem is None:
                continue
            records = db.scalars(
                select(StudyRecord).where(StudyRecord.plan_item_id == item.id)
                .order_by(StudyRecord.id.desc())
            ).all()
            notes = db.scalars(
                select(Note).where(Note.problem_id == problem.id)
                .order_by(Note.updated_at.desc())
            ).all()
            # 只导出范围内更新过的笔记，避免把很久以前的笔记反复塞进每份报告
            notes = [n for n in notes if date_from <= (n.updated_at.date() if isinstance(n.updated_at, datetime) else date_from) <= date_to]
            ai_texts: list[str] = []
            if include_ai:
                calls = db.scalars(
                    select(AiCall).where(AiCall.problem_id == problem.id, AiCall.status == "ok")
                    .order_by(AiCall.id.desc()).limit(3)
                ).all()
                for call in calls:
                    msgs = db.scalars(
                        select(AiMessage).where(AiMessage.call_id == call.id, AiMessage.role == "assistant")
                        .order_by(AiMessage.seq)
                    ).all()
                    if msgs:
                        ai_texts.append("\n".join(m.content for m in msgs).strip())

            day["items"].append({
                "item_type": item.item_type,
                "status": item.status,
                "problem": problem,
                "records": records,  # code_snippet 在渲染时按 include_code 开关裁剪
                "notes": notes,
                "ai_texts": ai_texts,
            })
            links.append({
                "source": SOURCES.get(problem.source, problem.source),
                "source_key": problem.source_key,
                "title": problem.title,
                "url": problem.url,
            })
        sections.append(day)

    return {
        "date_from": date_from, "date_to": date_to,
        "sections": sections, "links": links,
        "total_items": total_items, "done_items": done_items,
        "include_ai": include_ai, "include_code": include_code,
    }


def build_markdown(data: dict) -> str:
    lines: list[str] = [
        "# 刷题复盘报告",
        "",
        f"- 统计范围：{data['date_from'].isoformat()} ~ {data['date_to'].isoformat()}",
        f"- 生成时间：{_utcnow().isoformat(timespec='seconds')}",
        f"- 任务总数：{data['total_items']}，已完成：{data['done_items']}",
        f"- 完成率：{(data['done_items'] / data['total_items'] * 100):.1f}%" if data["total_items"] else "- 完成率：0.0%",
        "",
        "> 本报告仅包含本地题目元信息、个人笔记与 AI 分析摘要；不含任何第三方题面原文。",
        "",
    ]
    if not data["sections"]:
        lines += ["## 空报告", "", "所选日期范围内没有任何计划记录，请调整日期范围后重试。", ""]

    status_labels = {
        "pending": "待开始", "learning": "学习中", "learned": "已学题解",
        "ac": "独立通过", "reviewed": "已完成复习", "skipped": "已跳过", "overdue": "已逾期",
    }
    for day in data["sections"]:
        lines += [f"## {day['date']}", ""]
        if day["note"]:
            lines += [f"> 备注：{day['note']}", ""]
        if not day["items"]:
            lines += ["_当日无任务（休息日 / 暂停区间 / 题单不足）_", ""]
        for item in day["items"]:
            p = item["problem"]
            kind = "新题 · 学题解" if item["item_type"] == "new" else "复习 · 独立重做"
            lines += [f"### {kind}：{p.title}", ""]
            lines += [
                f"- 来源：{SOURCES.get(p.source, p.source)} · `{p.source_key}`",
                f"- 难度：{DIFFICULTY_LABELS.get(p.difficulty_level, '?')}（{p.difficulty_level}/5）",
                f"- 原题链接：[{p.url}]({p.url})" if p.url else "- 原题链接：无",
                f"- 状态：{status_labels.get(item['status'], item['status'])}",
            ]
            for rec in item["records"]:
                lines.append(
                    f"- 记录：耗时 {rec.time_spent_min} 分钟 · {rec.language} · 自评掌握度 {rec.mastery}/5"
                )
                if rec.memo:
                    lines.append(f"  - 备注：{rec.memo}")
                if data["include_code"] and rec.code_snippet.strip():
                    lang = rec.language.lower().replace("c++", "cpp").replace("c#", "csharp") or "text"
                    lines += ["", f"```{lang}", rec.code_snippet.rstrip(), "```"]
            if item["notes"]:
                lines += ["", "#### 思路总结", ""]
                for note in item["notes"]:
                    lines += [f"**{note.title}**", "", note.content_md.rstrip(), ""]
            if item["ai_texts"]:
                lines += ["", "#### AI 分析摘要", "", "> AI 生成，仅供参考，请以官方题解与独立验证为准。", ""]
                for text in item["ai_texts"]:
                    lines += [text, ""]
            lines.append("")

    # 题目外链清单（去重）
    seen = set()
    unique_links = []
    for link in data["links"]:
        key = (link["source"], link["source_key"])
        if key in seen:
            continue
        seen.add(key)
        unique_links.append(link)
    lines += ["---", "", "## 题目外链清单", ""]
    if unique_links:
        for link in unique_links:
            lines.append(f"- [{link['source']} · {link['source_key']} {link['title']}]({link['url']})")
    else:
        lines.append("_所选范围内没有题目。_")
    lines.append("")
    return "\n".join(lines)


def _markdown_blocks(md: str) -> list[tuple[str, str]]:
    """把 Markdown 拆成 (类型, 文本) 块，供 DOCX / PDF 渲染。"""
    blocks: list[tuple[str, str]] = []
    in_code = False
    buf: list[str] = []
    for line in md.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                blocks.append(("code", "\n".join(buf)))
                buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            buf.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("### "):
            blocks.append(("h3", stripped[4:]))
        elif stripped.startswith("## "):
            blocks.append(("h2", stripped[3:]))
        elif stripped.startswith("# "):
            blocks.append(("h1", stripped[2:]))
        elif stripped.startswith("- ") or stripped.startswith("  - "):
            blocks.append(("li", stripped.lstrip(" -")))
        elif stripped.startswith(">"):
            blocks.append(("quote", stripped.lstrip("> ")))
        elif stripped.startswith("**") and stripped.endswith("**"):
            blocks.append(("bold", stripped.strip("*")))
        elif stripped == "":
            blocks.append(("blank", ""))
        else:
            blocks.append(("p", line))
    if in_code and buf:
        blocks.append(("code", "\n".join(buf)))
    return blocks


def _clean_inline(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    return text


def render_docx(md: str, path: Path) -> None:
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.oxml.ns import qn
    except ImportError as exc:
        raise ValidationError("未安装 python-docx，无法导出 DOCX；请执行 pip install -r requirements.txt") from exc

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    for kind, text in _markdown_blocks(md):
        if kind == "blank":
            continue
        if kind == "h1":
            doc.add_heading(_clean_inline(text), level=1)
        elif kind == "h2":
            doc.add_heading(_clean_inline(text), level=2)
        elif kind == "h3":
            doc.add_heading(_clean_inline(text), level=3)
        elif kind == "li":
            doc.add_paragraph(_clean_inline(text), style="List Bullet")
        elif kind == "code":
            para = doc.add_paragraph()
            run = para.add_run(text)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif kind == "quote":
            para = doc.add_paragraph(_clean_inline(text))
            for run in para.runs:
                run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
                run.italic = True
        elif kind == "bold":
            para = doc.add_paragraph()
            para.add_run(_clean_inline(text)).bold = True
        else:
            doc.add_paragraph(_clean_inline(text))
    doc.save(str(path))


def render_pdf(md: str, path: Path) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise ValidationError("未安装 reportlab，无法导出 PDF；请执行 pip install -r requirements.txt") from exc

    # 内置 CJK CID 字体，无需外部字体文件即可正确显示中文
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:  # pragma: no cover
        pass
    font = "STSong-Light"
    base = getSampleStyleSheet()["Normal"]
    styles = {
        "h1": ParagraphStyle("h1", parent=base, fontName=font, fontSize=18, leading=24, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=base, fontName=font, fontSize=14, leading=20, spaceBefore=10, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=base, fontName=font, fontSize=12, leading=17, spaceBefore=6, spaceAfter=4),
        "p": ParagraphStyle("p", parent=base, fontName=font, fontSize=9.5, leading=14),
        "li": ParagraphStyle("li", parent=base, fontName=font, fontSize=9.5, leading=14, leftIndent=10),
        "quote": ParagraphStyle("quote", parent=base, fontName=font, fontSize=9, leading=13,
                                textColor=colors.HexColor("#666666"), leftIndent=10),
        "code": ParagraphStyle("code", parent=base, fontName=font, fontSize=8, leading=11,
                               backColor=colors.HexColor("#F4F4F4"), leftIndent=6, spaceBefore=3, spaceAfter=3),
        "bold": ParagraphStyle("bold", parent=base, fontName=font, fontSize=10, leading=15),
    }
    story = []
    for kind, text in _markdown_blocks(md):
        if kind == "blank":
            story.append(Spacer(1, 3))
            continue
        if kind == "code":
            safe = xml_escape(text).replace("\n", "<br/>")
            story.append(Paragraph(safe, styles["code"]))
        else:
            safe = xml_escape(_clean_inline(text)).replace("\n", "<br/>")
            story.append(Paragraph(safe, styles.get(kind, styles["p"])))

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title="刷题复盘报告", author="quiz-assistant",
    )
    doc.build(story)


def export_document(db: Session, payload: dict) -> dict:
    date_from = parse_date(payload.get("date_from"))
    date_to = parse_date(payload.get("date_to"))
    if date_from is None or date_to is None:
        raise ValidationError("date_from 与 date_to 必填（YYYY-MM-DD）")
    if date_from > date_to:
        raise ValidationError("date_from 不能晚于 date_to")
    fmt = str(payload.get("format") or "md").lower()
    if fmt not in ("md", "docx", "pdf"):
        raise ValidationError("format 仅支持 md / docx / pdf")
    include_ai = bool(payload.get("include_ai", True))
    include_code = bool(payload.get("include_code", True))

    data = collect(db, date_from, date_to, include_ai, include_code)
    md = build_markdown(data)
    stamp = _utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"复盘_{date_from.isoformat()}_{date_to.isoformat()}_{stamp}.{fmt}"
    path = EXPORT_DIR / filename
    if fmt == "md":
        path.write_text(md, encoding="utf-8")
    elif fmt == "docx":
        render_docx(md, path)
    else:
        render_pdf(md, path)

    record = ExportRecord(
        date_from=date_from, date_to=date_to, format=fmt,
        file_path=str(path), include_ai=include_ai, include_code=include_code,
        item_count=data["total_items"],
    )
    db.add(record)
    db.commit()
    return {
        "id": record.id, "format": fmt, "filename": filename,
        "file_path": str(path), "item_count": data["total_items"],
        "download_url": f"/api/exports/{record.id}/download",
        "markdown_preview": md[:600],
    }


def list_exports(db: Session) -> list[dict]:
    return [model_dict(r) for r in db.scalars(
        select(ExportRecord).order_by(ExportRecord.id.desc()).limit(50)
    ).all()]


def export_file(db: Session, export_id: int) -> tuple[Path, str]:
    record = db.get(ExportRecord, export_id)
    if record is None:
        raise NotFound("导出记录不存在")
    path = Path(record.file_path)
    if not path.exists():
        raise NotFound("导出文件已被移动或删除")
    media = {"md": "text/markdown", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "pdf": "application/pdf"}.get(record.format, "application/octet-stream")
    return path, media


# ---------------------------------------------------------------- JSON 快照

def snapshot_export(db: Session) -> dict:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "app_version": "1.0.0",
        "exported_at": _utcnow().isoformat(timespec="seconds"),
        "excluded_tables": ["alembic_version", "problem_snapshots"],
        "tables": {},
    }
    for model in SNAPSHOT_MODELS:
        rows = db.scalars(select(model)).all()
        payload["tables"][model.__tablename__] = [_serialize(r) for r in rows]
    payload["counts"] = {k: len(v) for k, v in payload["tables"].items()}
    return payload


def _coerce(model, data: dict) -> dict:
    out = {}
    for col in model.__table__.columns:
        key = col.key
        if key not in data:
            continue
        val = data[key]
        if val is None:
            out[key] = None
        elif isinstance(col.type, DateTime):
            out[key] = datetime.fromisoformat(val) if isinstance(val, str) else val
        elif isinstance(col.type, Date):
            out[key] = date.fromisoformat(val[:10]) if isinstance(val, str) else val
        elif isinstance(col.type, JSON):
            out[key] = val
        else:
            out[key] = val
    return out


def clear_business_data(db: Session) -> None:
    """清空业务表（按外键反序），供快照导入与演示数据重置使用。"""
    from sqlalchemy import update
    from ..models import ProblemSnapshot
    # 知识点树是自关联且外键为 RESTRICT，先解除父子关系再整表删除
    db.execute(update(KnowledgeNode).values(parent_id=None))
    for model in reversed(SNAPSHOT_MODELS):
        db.query(model).delete(synchronize_session=False)
    db.query(ProblemSnapshot).delete(synchronize_session=False)
    db.commit()


def snapshot_import(db: Session, payload: dict, clear: bool = True) -> dict:
    if not isinstance(payload, dict) or "tables" not in payload:
        raise ValidationError("快照格式错误：缺少 tables")
    if str(payload.get("schema_version")) != "1.0":
        raise ValidationError("不支持的快照 schema_version")
    try:
        if clear:
            clear_business_data(db)
        restored: dict[str, int] = {}
        for model in SNAPSHOT_MODELS:
            rows = payload["tables"].get(model.__tablename__, [])
            for row in rows:
                db.add(model(**_coerce(model, row)))
            restored[model.__tablename__] = len(rows)
            db.flush()
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise ValidationError(f"快照导入失败，已回滚：{exc}") from exc
    return {"restored": restored, "total": sum(restored.values()), "cleared": clear}
