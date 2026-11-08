"""个人知识点掌握度 / 能力画像（FR-32）。

三处消费方：
1. 计划生成：候选题按「能力适配分」稳定重排，偏难或含新知识的题往后排；
2. AI 分析：把画像写进 system prompt，要求讲解深度匹配，并强制标记【新知识】；
3. 题目工作台：单题展示匹配结论与新知识清单。

兼容设计：**尚未建立任何掌握度记录时，所有函数保持既有行为**（不重排、不改变任务数量），
因此不会破坏 TC-01～TC-04 的幂等与语义。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound, ValidationError
from ..models import KnowledgeMastery, KnowledgeNode, Problem, ProblemKnowledge
from . import settings as settings_svc

LEVELS = {
    0: "未接触（新知识）",
    1: "听说过，看过但不会用",
    2: "能看懂题解",
    3: "能独立做基础题",
    4: "熟练，能变形",
    5: "能讲解与变通",
}
DEFAULT_LEVEL = 2          # 未评估知识点按「能看懂题解」处理
NEW_THRESHOLD = 1          # level <= 1 视为新知识，AI 必须标记并详细说明
DEFAULT_CONFIG = {
    "enabled": True,       # 是否启用能力匹配排序
    "challenge_offset": 1,  # 允许比当前水平高出的难度档数
    "max_new_per_day": 1,   # 每天建议最多接触几道含新知识的题
}


def config(db: Session) -> dict:
    stored = settings_svc.get(db, "ability")
    out = dict(DEFAULT_CONFIG)
    if isinstance(stored, dict):
        out.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
    return out


def save_config(db: Session, payload: dict) -> dict:
    cur = config(db)
    if "enabled" in payload:
        cur["enabled"] = bool(payload["enabled"])
    for key in ("challenge_offset", "max_new_per_day"):
        if key in payload:
            try:
                cur[key] = max(0, min(5, int(payload[key])))
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"{key} 必须是 0–5 的整数") from exc
    return settings_svc.put(db, "ability", cur)


def _mastery_rows(db: Session) -> dict[int, KnowledgeMastery]:
    return {m.node_id: m for m in db.scalars(select(KnowledgeMastery)).all()}


def _level_of(rows: dict[int, KnowledgeMastery], node_id: int) -> tuple[int, bool]:
    row = rows.get(node_id)
    return (row.level, True) if row is not None else (DEFAULT_LEVEL, False)


def _nodes(db: Session) -> list[KnowledgeNode]:
    return list(db.scalars(
        select(KnowledgeNode).order_by(KnowledgeNode.sort_order, KnowledgeNode.id)
    ).all())


def profile(db: Session) -> dict:
    rows = _mastery_rows(db)
    items = []
    for n in _nodes(db):
        row = rows.get(n.id)
        items.append({
            "node_id": n.id, "name": n.name, "category": n.category,
            "parent_id": n.parent_id,
            "level": row.level if row else DEFAULT_LEVEL,
            "note": row.note if row else "",
            "evaluated": row is not None,
            "updated_at": row.updated_at.isoformat(timespec="seconds") if row else None,
        })
    evaluated = [i for i in items if i["evaluated"]]
    by_level = {str(k): sum(1 for i in items if i["level"] == k) for k in LEVELS}
    return {
        "config": config(db),
        "levels": [{"value": k, "label": v} for k, v in LEVELS.items()],
        "default_level": DEFAULT_LEVEL,
        "new_threshold": NEW_THRESHOLD,
        "items": items,
        "stats": {
            "total": len(items),
            "evaluated": len(evaluated),
            "average": round(sum(i["level"] for i in evaluated) / len(evaluated), 2) if evaluated else None,
            "new_count": sum(1 for i in items if i["level"] <= NEW_THRESHOLD),
            "weak_count": sum(1 for i in items if i["level"] == 2),
            "strong_count": sum(1 for i in items if i["level"] >= 4),
            "by_level": by_level,
        },
    }


def save_levels(db: Session, items: list) -> dict:
    if not isinstance(items, list):
        raise ValidationError("items 必须是数组")
    rows = _mastery_rows(db)
    for item in items:
        try:
            node_id = int(item.get("node_id") or 0)
            level = int(item.get("level"))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("node_id / level 必须是整数") from exc
        if db.get(KnowledgeNode, node_id) is None:
            raise ValidationError(f"知识点 {node_id} 不存在")
        if level not in LEVELS:
            raise ValidationError("掌握度必须是 0–5")
        note = str(item.get("note") or "")[:500]
        row = rows.get(node_id)
        if row is None:
            db.add(KnowledgeMastery(node_id=node_id, level=level, note=note))
        else:
            row.level = level
            row.note = note
    db.commit()
    return profile(db)


def _problem_links(db: Session, problem_id: int, rows: dict) -> tuple[list, list, list]:
    """返回 (已知, 新知识, 薄弱) 三组的知识点条目。只看已确认的关联。"""
    node_ids = list(db.scalars(
        select(ProblemKnowledge.node_id).where(
            ProblemKnowledge.problem_id == problem_id,
            ProblemKnowledge.verified.is_(True),
        )
    ))
    nodes = {n.id: n for n in db.scalars(select(KnowledgeNode)).all()}
    known, new, weak = [], [], []
    for nid in dict.fromkeys(node_ids):
        node = nodes.get(nid)
        if node is None:
            continue
        level, evaluated = _level_of(rows, nid)
        entry = {"node_id": nid, "name": node.name, "category": node.category,
                 "level": level, "label": LEVELS[level], "evaluated": evaluated}
        if level <= NEW_THRESHOLD:
            new.append(entry)
        elif level <= 2:
            weak.append(entry)
        else:
            known.append(entry)
    return known, new, weak


def _overall_level(db: Session) -> float:
    rows = _mastery_rows(db)
    if not rows:
        return float(DEFAULT_LEVEL)
    return sum(m.level for m in rows.values()) / len(rows)


def match(db: Session, problem_id: int) -> dict:
    """单题能力匹配结论：供工作台与 AI 提示词使用。"""
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise NotFound(f"题目 {problem_id} 不存在")
    cfg = config(db)
    rows = _mastery_rows(db)
    known, new, weak = _problem_links(db, problem_id, rows)
    overall = round(_overall_level(db))
    gap = problem.difficulty_level - overall
    if new:
        verdict = "含新知识"
        advice = ("这道题会用到你尚未掌握的知识点，建议先读 AI 分析里的【新知识】说明，"
                  "再尝试独立解题；不要直接跳到完整代码。")
    elif gap > cfg["challenge_offset"]:
        verdict = "偏难"
        advice = f"题目难度比当前自评水平高 {gap} 档，建议先做同知识点的低难度题过渡。"
    elif gap < -1:
        verdict = "偏易 · 适合巩固"
        advice = "难度低于当前水平，适合用来巩固与提速，可尝试一题多解。"
    else:
        verdict = "能力匹配"
        advice = "难度与当前水平接近，适合作为今日主攻题。"
    return {
        "problem_id": problem_id,
        "problem_title": problem.title,
        "difficulty": problem.difficulty_level,
        "overall_level": overall,
        "gap": gap,
        "verdict": verdict,
        "advice": advice,
        "known": known,
        "new_knowledge": new,
        "weak": weak,
        "config": cfg,
        "profile_ready": bool(rows),
    }


def prompt_suffix(db: Session, problem_id: int | None = None) -> str:
    """拼进 AI system prompt 的能力约束；无画像时也给出保守的讲解要求。"""
    prof = profile(db)
    evaluated = [i for i in prof["items"] if i["evaluated"]]
    lines = ["\n\n【学习者能力画像 —— 必须遵守，它决定你的讲解深度】"]
    if evaluated:
        strong = [f"{i['name']}({i['level']}/5)" for i in evaluated if i["level"] >= 4][:12]
        weak = [f"{i['name']}({i['level']}/5)" for i in evaluated if i["level"] <= 2][:12]
        lines.append(f"整体自评 {prof['stats']['average']}/5（0 未接触，2 能看懂题解，3 能独立做基础题，5 能讲解变通）。")
        lines.append("已熟练掌握：" + ("、".join(strong) or "暂无"))
        lines.append("薄弱或未接触：" + ("、".join(weak) or "暂无"))
    else:
        lines.append("用户尚未填写掌握度。请默认按「能看懂题解、但独立解题经验不多」讲解，"
                     "不要跳步，不要默认用户已懂高级术语。")
    if problem_id:
        m = match(db, problem_id)
        all_nodes = m["known"] + m["weak"] + m["new_knowledge"]
        lines.append(f"本题：{m['problem_title']}，项目难度 {m['difficulty']}/5，能力匹配结论「{m['verdict']}」。")
        lines.append("本题关联知识点：" +
                     ("、".join(f"{n['name']}({n['level']}/5)" for n in all_nodes) or "暂无标注"))
        if m["new_knowledge"]:
            lines.append("其中属于新知识、必须标记：" +
                         "、".join(n["name"] for n in m["new_knowledge"]))
        lines.append(f"建议：{m['advice']}")
    lines += [
        "【输出硬性要求】",
        ("1. 讲解深度必须匹配上面的水平：对薄弱知识点从直觉与定义讲起，每一步都说明「为什么」，"
        "不要跳步、不要只给结论、不要使用未经解释的高级术语。"),
        ("2. 凡是涉及掌握度 ≤ 1 的知识点，首次出现时必须用「【新知识】知识点名」单独标记，"
        "并在紧随其后的小节里详细说明：① 大白话定义 ② 一个最小直观例子 ③ 为什么本题需要它 "
        "④ 需要的前置知识与最小可用模板 / 代码片段 ⑤ 与已掌握知识的联系。"
        "没有被标记的内容才可以假定用户已经掌握。"),
        "3. 若解题需要明显超出当前水平的前置知识，先列「建议先补」清单（每项一句话说明为什么），再进入正题。",
        "4. 参考代码里的每个新语法 / 新结构都要有行内注释，且默认折叠并标注「AI 生成，仅供参考」。",
        "5. 结尾固定标注「AI 生成，仅供参考，请以官方题解与独立验证为准」。",
    ]
    return "\n".join(lines)


def rank_queue(db: Session, queue: list) -> list:
    """按能力适配度稳定重排候选题（仅改变顺序，不改变数量、consumed 语义）。

    没有建立任何掌握度记录、或用户关闭能力匹配时，原样返回，保证既有计划行为不变。
    """
    if not queue or not _mastery_rows(db) or not config(db).get("enabled", True):
        return queue
    cfg = config(db)
    ids = [q.problem_id for q in queue]
    problems = {p.id: p for p in db.scalars(
        select(Problem).where(Problem.id.in_(ids))
    ).all()}
    link_rows = db.execute(
        select(ProblemKnowledge.problem_id, ProblemKnowledge.node_id).where(
            ProblemKnowledge.problem_id.in_(ids),
            ProblemKnowledge.verified.is_(True),
        )
    ).all()
    by_problem: dict[int, list[int]] = {}
    for pid, nid in link_rows:
        by_problem.setdefault(pid, []).append(nid)
    rows = _mastery_rows(db)
    overall = round(_overall_level(db))

    def key(q):
        problem = problems.get(q.problem_id)
        if problem is None:
            return (1, q.position, q.id)
        levels = [_level_of(rows, n)[0] for n in by_problem.get(q.problem_id, [])]
        new_count = sum(1 for lv in levels if lv <= NEW_THRESHOLD)
        too_hard = max(0, problem.difficulty_level - overall - cfg["challenge_offset"])
        too_easy = max(0, overall - problem.difficulty_level - 1)
        penalty = too_hard * 3 + new_count * 2 + too_easy
        return (penalty, q.position, q.id)

    return sorted(queue, key=key)
