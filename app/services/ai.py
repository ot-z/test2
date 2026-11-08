"""DeepSeek AI 智能体（FR-17 ～ FR-21 / M3）。

- OpenAI 兼容模式，base_url=https://api.deepseek.com；模型 ID 可自定义（预设仅作建议）。
- 流式输出走 SSE；中断时只在成功完成后落库，不残留半截脏数据（FR-18）。
- 402 余额不足、429 指数退避、超时 / 鉴权错误均转成可读提示。
- 密钥只从 .env.local / 环境变量读取，日志与页面一律不回显明文。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import DEPRECATED_MODELS
from ..db import SessionLocal, _utcnow
from ..errors import ServiceUnavailable, ValidationError
from ..models import AiCall, AiMessage, KnowledgeNode, Problem, ProblemKnowledge
from ..utils import model_dict
from . import ability
from .settings import ai_config, model_capabilities, read_api_key

PRICE_PER_MILLION = {
    "deepseek-flash": (0.15, 0.60),
    "deepseek-v4-pro": (0.66, 1.98),
}

SYSTEM_PROMPTS = {
    "analyze": (
        "你是一位算法竞赛辅导助手，服务对象是备战蓝桥杯 C/C++ 组的学生。"
        "请严格按以下结构输出，且以「分步提示」为主，不要一上来给完整答案：\n"
        "1. 考察知识点（列出知识点 / 数据结构 / 算法）；\n"
        "2. 解题思路分步提示（3-5 步，每步给方向而非结论）；\n"
        "3. 复杂度分析；\n"
        "4. 参考代码（用 <details><summary>展开参考代码</summary> 包裹，默认折叠，标注「AI 生成，仅供参考」）。\n"
        "所有结论都必须标注「AI 生成，仅供参考，请以官方题解与独立验证为准」。"
    ),
    "debug": (
        "你是一位 C/C++ 算法竞赛代码诊断助手。用户会给出代码与错误现象。"
        "请输出：① 错误定位（具体行号或代码片段）；② 原因分析；③ 修改建议（不要直接重写整段代码）。"
        "结尾标注「AI 生成，仅供参考」。"
    ),
    "suggest_tags": (
        "你是题目元信息标注助手。只输出一个 JSON 对象，不要任何解释文字，格式为："
        '{"knowledge_nodes": ["知识点名称"], "difficulty_level": 3, "reason": "简短理由"}。'
        "knowledge_nodes 最多 3 个，须使用常见中文算法知识点名称；difficulty_level 为 1 到 5 的整数。"
    ),
}


def _client(cfg: dict, api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        base_url=cfg.get("base_url") or "https://api.deepseek.com",
        timeout=float(cfg.get("timeout_seconds") or 60),
        max_retries=0,
    )


def _kwargs(cfg: dict, model: str, messages: list[dict], stream: bool) -> dict:
    caps = model_capabilities(model)
    kwargs: dict[str, Any] = {
        "model": model, "messages": messages, "stream": stream,
    }
    if "temperature" in caps:
        kwargs["temperature"] = float(cfg.get("temperature", 0.3))
    if "max_tokens" in caps:
        kwargs["max_tokens"] = int(cfg.get("max_tokens", 2048))
    extra: dict[str, Any] = {}
    # 明确不发送 frequency_penalty / presence_penalty（服务端会忽略）
    if cfg.get("thinking") and "thinking" in caps:
        extra["thinking"] = {"type": "enabled"}
    if cfg.get("thinking") and "reasoning_effort" in caps:
        extra["reasoning_effort"] = cfg.get("reasoning_effort", "medium")
    if extra:
        kwargs["extra_body"] = extra
    # 不发送 stream_options：不同模型 / 网关对此参数支持度不一致，
    # token 用量优先取响应中的 usage，缺失时按文本长度估算（FR-21 允许「估算费用」）。
    return kwargs


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = PRICE_PER_MILLION.get(model, (0.15, 0.60))
    return round((prompt_tokens * pin + completion_tokens * pout) / 1_000_000, 8)


def _readable_error(exc: Exception) -> tuple[str, str]:
    """返回 (用户可读提示, 错误码)。绝不把堆栈或密钥回显给页面。"""
    status = getattr(exc, "status_code", None)
    text = str(exc)
    if status == 401 or "Authentication" in type(exc).__name__ or "invalid api key" in text.lower():
        return "API Key 无效或已撤销，请在「设置 → AI」中更新密钥", "AUTH_FAILED"
    if status == 402 or "insufficient balance" in text.lower() or "余额不足" in text:
        return "DeepSeek 账户余额不足，请充值后再试（错误 402）", "INSUFFICIENT_BALANCE"
    if status == 429 or "RateLimit" in type(exc).__name__:
        return "请求过于频繁（429），已自动退避重试，请稍后再试", "RATE_LIMITED"
    if "Timeout" in type(exc).__name__:
        return "AI 请求超时，可在设置页调大超时时间", "TIMEOUT"
    if "Connection" in type(exc).__name__:
        return "无法连接 DeepSeek 服务，请检查网络（核心刷题功能不受影响）", "NETWORK"
    return f"AI 调用失败：{type(exc).__name__}，请检查设置或稍后重试", "AI_ERROR"


def _log_start(scene: str, model: str, problem_id: int | None, prompt: str) -> int:
    db = SessionLocal()
    try:
        call = AiCall(scene=scene, model=model, problem_id=problem_id, status="running")
        db.add(call)
        db.flush()
        db.add(AiMessage(call_id=call.id, role="user", content=prompt, seq=0))
        db.commit()
        return call.id
    finally:
        db.close()


def _log_finish(call_id: int, content: str, prompt_tokens: int,
                completion_tokens: int, status: str, error: str = "") -> dict:
    db = SessionLocal()
    try:
        call = db.get(AiCall, call_id)
        if call is None:
            return {}
        call.prompt_tokens = prompt_tokens
        call.completion_tokens = completion_tokens
        call.estimated_cost = _estimate_cost(call.model, prompt_tokens, completion_tokens)
        call.status = status
        call.error = error
        call.finished_at = _utcnow()
        # 只在成功时写入模型输出，避免中断留下半截内容
        if status == "ok" and content:
            db.add(AiMessage(call_id=call_id, role="assistant", content=content, seq=1))
        db.commit()
        return {"call_id": call_id, "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "estimated_cost": call.estimated_cost}
    finally:
        db.close()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def stream_events(scene: str, payload: dict) -> AsyncGenerator[str, None]:
    if scene not in ("analyze", "debug"):
        yield _sse({"type": "error", "message": f"不支持的场景：{scene}"})
        return

    db = SessionLocal()
    try:
        cfg = ai_config(db)
        api_key = read_api_key()
        model = cfg["effective_model"]
        problem_id = payload.get("problem_id")
        problem_id = int(problem_id) if problem_id else None
        if model in DEPRECATED_MODELS:
            yield _sse({"type": "error",
                        "message": f"模型 {model} 已于 2026-07-24 停用，请在设置页换成 deepseek-flash 或自定义模型 ID"})
            return
        if not api_key:
            yield _sse({"type": "error", "code": "NOT_CONFIGURED",
                        "message": "尚未配置 DeepSeek API Key，请在「设置 → AI」填写。核心刷题功能不受影响。"})
            return
        # FR-32：把个人能力画像写进 system prompt，讲解深度与新知识标记由它约束
        ability_hint = ability.prompt_suffix(db, problem_id)
    finally:
        db.close()

    user_text = str(payload.get("content") or "").strip()
    if not user_text:
        yield _sse({"type": "error", "message": "请粘贴题面 / 思路或代码内容"})
        return
    if scene == "debug":
        phenomenon = str(payload.get("phenomenon") or "未说明")
        user_text = f"【错误现象】{phenomenon}\n\n【代码】\n{user_text}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPTS[scene] + ability_hint},
        {"role": "user", "content": user_text},
    ]
    call_id = _log_start(scene, model, problem_id, user_text)
    client = _client(cfg, api_key)
    retries = max(0, int(cfg.get("max_retries") or 0))
    content_parts: list[str] = []
    prompt_tokens = completion_tokens = 0
    status = "ok"
    error_msg = ""

    try:
        for attempt in range(retries + 1):
            try:
                stream = await client.chat.completions.create(
                    **_kwargs(cfg, model, messages, stream=True)
                )
                async for chunk in stream:
                    usage = getattr(chunk, "usage", None)
                    if usage:
                        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or prompt_tokens)
                        completion_tokens = int(getattr(usage, "completion_tokens", 0) or completion_tokens)
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    piece = getattr(delta, "content", None) or ""
                    if piece:
                        content_parts.append(piece)
                        yield _sse({"type": "delta", "content": piece})
                break
            except Exception as exc:  # noqa: BLE001
                readable, code = _readable_error(exc)
                if code == "RATE_LIMITED" and attempt < retries:
                    wait = 2 ** attempt
                    yield _sse({"type": "retry", "message": f"触发限流，{wait} 秒后自动重试…", "attempt": attempt + 1})
                    content_parts.clear()
                    await asyncio.sleep(wait)
                    continue
                status, error_msg = "error", readable
                yield _sse({"type": "error", "code": code, "message": readable})
                break
        else:
            status, error_msg = "error", "重试次数已用尽，请稍后再试"
            yield _sse({"type": "error", "message": error_msg})
    except asyncio.CancelledError:
        # 客户端中断：不落库任何模型输出（FR-18）
        status, error_msg = "aborted", "用户中断生成"
        _log_finish(call_id, "", prompt_tokens, completion_tokens, status, error_msg)
        raise
    finally:
        await client.close()

    if status == "ok":
        content = "".join(content_parts)
        if not prompt_tokens:
            prompt_tokens = max(1, len(user_text) // 3)
            completion_tokens = max(1, len(content) // 3)
        usage_info = _log_finish(call_id, content, prompt_tokens, completion_tokens, status)
        yield _sse({"type": "done", "content": content, **usage_info})
    else:
        _log_finish(call_id, "", prompt_tokens, completion_tokens, status, error_msg)


async def debug_stream(payload: dict) -> AsyncGenerator[str, None]:
    async for event in stream_events("debug", payload):
        yield event


async def analyze_stream(payload: dict) -> AsyncGenerator[str, None]:
    async for event in stream_events("analyze", payload):
        yield event


def suggest_tags(db: Session, payload: dict) -> dict:
    """FR-20：JSON 模式标签建议；非法 JSON 时降级，不写入任何脏标签。"""
    import httpx

    cfg = ai_config(db)
    api_key = read_api_key()
    if not api_key:
        raise ServiceUnavailable("尚未配置 DeepSeek API Key，请在设置页填写", "NOT_CONFIGURED")
    model = cfg["effective_model"]
    if model in DEPRECATED_MODELS:
        raise ValidationError(f"模型 {model} 已停用，请改用 deepseek-flash 或自定义模型 ID")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise ValidationError("请提供题面或题目描述文本")

    call_id = _log_start("suggest_tags", model, payload.get("problem_id"), content)
    url = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPTS["suggest_tags"]},
            {"role": "user", "content": content},
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
        "temperature": float(cfg.get("temperature", 0.3)),
        "max_tokens": int(cfg.get("max_tokens", 2048)),
    }
    try:
        with httpx.Client(timeout=float(cfg.get("timeout_seconds") or 60)) as client:
            resp = client.post(url, json=body, headers={"Authorization": f"Bearer {api_key}"})
            if resp.status_code == 402:
                raise ServiceUnavailable("DeepSeek 账户余额不足（402），请充值后再试", "INSUFFICIENT_BALANCE")
            resp.raise_for_status()
            raw = resp.json()
        text = (raw.get("choices") or [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(text)
        nodes = [str(x) for x in (parsed.get("knowledge_nodes") or [])][:3]
        level = int(parsed.get("difficulty_level") or 3)
        if not 1 <= level <= 5:
            raise ValueError("difficulty_level 越界")
        if not isinstance(nodes, list):
            raise ValueError("knowledge_nodes 格式错误")
        usage = raw.get("usage") or {}
        p_tok = int(usage.get("prompt_tokens") or len(content) // 3)
        c_tok = int(usage.get("completion_tokens") or len(text) // 3)
        _log_finish(call_id, text, p_tok, c_tok, "ok")
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        _log_finish(call_id, "", 0, 0, "error", "解析失败")
        return {"ok": False, "error": f"解析失败，可重试（模型未返回合法 JSON）：{type(exc).__name__}",
                "written": 0, "suggestions": []}
    except Exception as exc:  # noqa: BLE001
        readable, code = _readable_error(exc)
        _log_finish(call_id, "", 0, 0, "error", readable)
        raise ServiceUnavailable(readable, code) from exc

    problem_id = payload.get("problem_id")
    written = 0
    if problem_id:
        problem = db.get(Problem, int(problem_id))
        if problem:
            names = {n.name: n.id for n in db.scalars(select(KnowledgeNode)).all()}
            for name in nodes:
                node_id = names.get(name)
                if not node_id:
                    continue
                dup = db.scalar(select(ProblemKnowledge).where(
                    ProblemKnowledge.problem_id == problem.id,
                    ProblemKnowledge.node_id == node_id,
                ))
                if dup:
                    continue
                db.add(ProblemKnowledge(
                    problem_id=problem.id, node_id=node_id,
                    confidence="ai", verified=False,  # 人工确认后才生效
                ))
                written += 1
            db.commit()
    notice = (
        f"已写入 {written} 条 AI 建议标签（verified=false），需人工确认后才参与组题筛选"
        if written else
        "仅返回建议，未写入标签（未提供题目 ID 或建议节点未匹配）；如需入库请在题目详情中指定题目"
    )
    return {"ok": True, "suggestions": nodes, "difficulty_level": level,
            "reason": str(parsed.get("reason") or ""),
            "written": written, "verified": False, "notice": notice}


def usage(db: Session) -> dict:
    total_calls = int(db.scalar(select(func.count()).select_from(AiCall)) or 0)
    ok_calls = int(db.scalar(select(func.count()).select_from(AiCall)
                             .where(AiCall.status == "ok")) or 0)
    tokens_in = int(db.scalar(select(func.sum(AiCall.prompt_tokens))) or 0)
    tokens_out = int(db.scalar(select(func.sum(AiCall.completion_tokens))) or 0)
    cost = float(db.scalar(select(func.sum(AiCall.estimated_cost))) or 0.0)
    recent = [model_dict(r) for r in db.scalars(
        select(AiCall).order_by(AiCall.id.desc()).limit(20)
    ).all()]
    return {"total_calls": total_calls, "ok_calls": ok_calls,
            "prompt_tokens": tokens_in, "completion_tokens": tokens_out,
            "estimated_cost_usd": round(cost, 6), "recent": recent}


def status(db: Session) -> str:
    if not read_api_key():
        return "not_configured"
    last = db.scalar(select(AiCall).order_by(AiCall.id.desc()))
    if last and last.status == "error" and "AUTH" in (last.error or "").upper():
        return "unavailable"
    return "configured"


def test_connection(db: Session) -> dict:
    import httpx
    cfg = ai_config(db)
    api_key = read_api_key()
    if not api_key:
        raise ServiceUnavailable("尚未配置 API Key", "NOT_CONFIGURED")
    url = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    body = {"model": cfg["effective_model"],
            "messages": [{"role": "user", "content": "回复 OK 两个字母即可"}],
            "max_tokens": 8, "stream": False}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=body, headers={"Authorization": f"Bearer {api_key}"})
            if resp.status_code == 402:
                raise ServiceUnavailable("账户余额不足（402）", "INSUFFICIENT_BALANCE")
            if resp.status_code == 401:
                raise ServiceUnavailable("API Key 无效（401）", "AUTH_FAILED")
            resp.raise_for_status()
            return {"ok": True, "model": cfg["effective_model"],
                    "reply": ((resp.json().get("choices") or [{}])[0]
                              .get("message", {}).get("content", ""))[:60]}
    except ServiceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        readable, code = _readable_error(exc)
        raise ServiceUnavailable(readable, code) from exc
