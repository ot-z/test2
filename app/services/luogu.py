"""洛谷公开题面采集（FR-30）：网络层 + 页面解析 + 标签/难度归一。

边界（详见 config/luogu.yaml 顶部说明）：
- 只读取公开题目页自带的 ``<script id="lentille-context">`` JSON，也就是浏览器
  打开该页面时服务端已经下发的题面数据；不调用任何需要登录的私有接口；
- 不复用用户登录 Cookie、不处理验证码 / JS 挑战、不抓题解与讨论内容；
- CDN 自动下发的匿名访问 cookie 由 httpx 按浏览器常规行为自动保存；
- 所有外部请求限速、失败退避、可中断，题面仅本地个人学习使用。

本模块只负责「拿数据 / 解析数据」，写库与文件导出在 ``luogu_store.py``。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from ..config import CONFIG_DIR, DATA_DIR

CONFIG_PATH = CONFIG_DIR / "luogu.yaml"
TAGS_CACHE = DATA_DIR / "luogu_tags.json"
DIFFICULTY_CACHE = DATA_DIR / "luogu_difficulty.json"
CONTEXT_RE = re.compile(
    r'<script id="lentille-context"[^>]*>(.*?)</script>', re.S | re.I
)


class LuoguError(RuntimeError):
    """采集过程中的可预期错误。"""


class ProblemNotFound(LuoguError):
    """题号不存在 / 未公开（页面 status=404）。"""


class RateLimited(LuoguError):
    """403 / 429：需要退避，不应继续加压。"""


def load_config(path: Path | str | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg.setdefault("site", {})
    cfg.setdefault("crawl", {})
    cfg.setdefault("difficulty", {})
    cfg.setdefault("tag_nodes", {})
    cfg.setdefault("keyword_rules", [])
    cfg.setdefault("knowledge_tag_types", [2, 6])
    cfg.setdefault("ignore_tags", [])
    return cfg


def difficulty_info(value: Any, cfg: dict, names: dict | None = None) -> dict:
    """洛谷 difficulty 数值 → (官方中文名, 项目 1–5 档, 原始值)。

    ``names`` 来自站点 /_lfe/config 的 ProblemDifficulty；站点改名时自动跟随，
    yaml 里的 name 只作为离线兜底。level 始终由本地配置决定，便于按个人尺度校准。
    """
    try:
        num = int(value)
    except (TypeError, ValueError):
        num = 0
    table = cfg.get("difficulty") or {}
    entry = table.get(num) or table.get(str(num)) or {}
    name = str((names or {}).get(str(num)) or entry.get("name") or "暂无评定")
    level = int(entry.get("level") or 3)
    return {"value": num, "name": name, "level": max(1, min(5, level)),
            "raw": f"洛谷{num}·{name}"}


def sync_difficulty_names(client: "LuoguClient", cfg: dict, refresh: bool = False) -> dict:
    """从公开前端配置同步难度名称（id → 名称），失败时静默回退到本地配置。"""
    if DIFFICULTY_CACHE.exists() and not refresh:
        try:
            with open(DIFFICULTY_CACHE, encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached:
                return cached
        except (OSError, ValueError):
            pass
    url = cfg["site"].get("config_url") or "https://www.luogu.com.cn/_lfe/config"
    try:
        resp = client.get(url, expect_context=False)
        rows = (resp.json() or {}).get("ProblemDifficulty") or []
        names = {str(row["id"]): str(row["name"]) for row in rows
                 if row.get("id") is not None and row.get("name")}
        if names:
            DIFFICULTY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            DIFFICULTY_CACHE.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
            return names
    except (LuoguError, ValueError, httpx.HTTPError):
        pass
    return {}


class TagBook:
    """洛谷标签字典：id → {name, type, parent}，本地缓存避免重复请求。"""

    def __init__(self, data: dict):
        self.tags: dict[int, dict] = {}
        for row in (data.get("tags") or []):
            try:
                self.tags[int(row["id"])] = {
                    "name": str(row.get("name") or "").lstrip("\ufeff").strip(),
                    "type": int(row.get("type") or 0),
                    "parent": row.get("parent"),
                }
            except (KeyError, TypeError, ValueError):
                continue

    def __len__(self) -> int:
        return len(self.tags)

    def names(self, ids: list[int], cfg: dict) -> dict:
        """返回 {all: [...], knowledge: [...]}；knowledge 只含知识点类标签。"""
        all_names: list[str] = []
        knowledge: list[str] = []
        types = set(cfg.get("knowledge_tag_types") or [2, 6])
        ignore = set(cfg.get("ignore_tags") or [])
        exact = cfg.get("tag_nodes") or {}
        seen: set[str] = set()
        for tid in ids or []:
            tag = self.tags.get(int(tid)) if isinstance(tid, (int, str)) and str(tid).lstrip("-").isdigit() else None
            if not tag or not tag["name"]:
                continue
            name = tag["name"]
            if name not in seen:
                seen.add(name)
                all_names.append(name)
            if tag["type"] in types and name not in ignore and name not in knowledge:
                knowledge.append(name)
            # 仅当子标签没有精确映射时，才借父级标签兜底（避免「记忆化搜索」被泛化成 DFS）
            parent = tag.get("parent")
            if parent is not None and name not in exact:
                ptag = self.tags.get(int(parent)) if str(parent).lstrip("-").isdigit() else None
                if ptag and ptag["type"] in types and ptag["name"] not in ignore \
                        and ptag["name"] not in knowledge:
                    knowledge.append(ptag["name"])
        return {"all": all_names, "knowledge": knowledge}


def load_tagbook(client: "LuoguClient | None" = None, cfg: dict | None = None,
                 refresh: bool = False, cache_path: Path | None = None) -> TagBook:
    """优先读本地缓存；缓存不存在或 refresh=True 时从站点公开配置接口获取。"""
    cfg = cfg or load_config()
    path = cache_path or TAGS_CACHE
    if path.exists() and not refresh:
        with open(path, encoding="utf-8") as fh:
            return TagBook(json.load(fh))
    if client is None:
        raise LuoguError("标签字典缓存缺失且未提供网络客户端")
    resp = client.get(cfg["site"].get("tags_url") or "https://www.luogu.com.cn/_lfe/tags",
                      expect_context=False)
    if resp.status_code != 200:
        raise LuoguError(f"标签字典获取失败：HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise LuoguError("标签字典返回内容不是 JSON") from exc
    if not (data.get("tags") or []):
        raise LuoguError("标签字典为空，站点结构可能已改版")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return TagBook(data)


def map_knowledge(tag_names: list[str], cfg: dict) -> list[str]:
    """洛谷标签 → 项目知识点树节点名（精确匹配 + 关键词兜底，去重保序）。"""
    exact: dict[str, list[str]] = cfg.get("tag_nodes") or {}
    rules: list[dict] = cfg.get("keyword_rules") or []
    out: list[str] = []
    matched: set[str] = set()

    def add(nodes: list[str]) -> None:
        for node in nodes:
            if node and node not in out:
                out.append(node)

    for name in tag_names:
        key = str(name).lstrip("\ufeff").strip()
        if key in exact:
            add(list(exact[key]))
            matched.add(key)
    # 关键词只处理未被精确命中的标签，避免「树状数组」被泛化成「树形结构」
    for name in tag_names:
        key = str(name).lstrip("\ufeff").strip()
        if key in matched:
            continue
        for rule in rules:
            if str(rule.get("contains") or "") in key:
                add(list(rule.get("nodes") or []))
                break
    return out


class LuoguClient:
    """限速、自动处理匿名 cookie 挑战、失败退避的公开页面客户端。"""

    def __init__(self, cfg: dict | None = None, interval: float | None = None):
        self.cfg = cfg or load_config()
        self.crawl = self.cfg.get("crawl") or {}
        self.interval = max(0.8, float(interval if interval is not None
                                       else self.crawl.get("request_interval_seconds", 1.2)))
        self._last_request = 0.0
        self._tagbook: TagBook | None = None
        self.client = httpx.Client(
            headers={
                "User-Agent": self.cfg["site"].get("user_agent") or "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            follow_redirects=True,
            timeout=float(self.crawl.get("timeout_seconds", 20)),
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "LuoguClient":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _throttle(self) -> None:
        wait = self.interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, url: str, expect_context: bool = True) -> httpx.Response:
        """GET 一个公开 URL。

        站点 CDN 对首次访问会返回 302 并下发匿名 cookie；httpx 自动保存后
        重复请求同一 URL 即可拿到正常页面（与浏览器行为一致）。
        """
        retries = int(self.crawl.get("max_retries", 3))
        challenges = int(self.crawl.get("challenge_retries", 4))
        backoff = float(self.crawl.get("retry_backoff_seconds", 4))
        last_error = ""
        for attempt in range(retries + 1):
            for _ in range(challenges):
                self._throttle()
                try:
                    resp = self.client.get(url)
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    break
                if resp.status_code in (403, 429):
                    raise RateLimited(f"HTTP {resp.status_code}，站点要求放慢访问：{url}")
                if resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}"
                    break
                has_context = bool(CONTEXT_RE.search(resp.text))
                if resp.status_code == 200 and (has_context or not expect_context):
                    return resp
                if resp.status_code in (200, 404) and has_context:
                    return resp  # 404 页面也带 context，交给解析层判定
                last_error = f"HTTP {resp.status_code}（未拿到页面数据）"
                # 302 挑战：cookie 已写入 jar，直接重试同一 URL
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
        raise LuoguError(f"请求失败，已重试 {retries} 次：{url} · {last_error}")

    @property
    def tagbook(self) -> TagBook:
        """标签字典懒加载：整批采集只请求一次 /_lfe/tags。"""
        if self._tagbook is None:
            self._tagbook = load_tagbook(client=self, cfg=self.cfg)
        return self._tagbook

    def fetch_problem(self, pid: str, tagbook: TagBook | None = None) -> dict:
        """抓取并解析单题，返回结构化数据（不写库）。"""
        template = self.cfg["site"].get("problem_url") or "https://www.luogu.com.cn/problem/{pid}"
        url = template.format(pid=pid)
        resp = self.get(url)
        return parse_problem_page(resp.text, pid, self.cfg,
                                  tagbook=tagbook or self.tagbook, url=url)

    def fetch_catalog_page(self, page: int) -> dict:
        """题库列表页（公开）：返回该页题目元信息与总数。"""
        template = self.cfg["site"].get("list_url") or \
            "https://www.luogu.com.cn/problem/list?type=P&page={page}"
        resp = self.get(template.format(page=page))
        ctx = parse_context(resp.text)
        if ctx.get("status") != 200 or ctx.get("template") != "problem.list":
            raise LuoguError(f"题库列表页结构异常：page={page}, status={ctx.get('status')}")
        payload = (ctx.get("data") or {}).get("problems") or {}
        return {
            "count": int(payload.get("count") or 0),
            "per_page": int(payload.get("perPage") or 50),
            "items": list(payload.get("result") or []),
        }


def parse_context(html: str) -> dict:
    match = CONTEXT_RE.search(html or "")
    if not match:
        raise LuoguError("页面中没有找到 lentille-context 数据（可能需要人工在浏览器确认）")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise LuoguError(f"lentille-context 不是合法 JSON：{exc}") from exc


def _section(title: str, text: Any) -> str:
    body = str(text or "").strip()
    return f"## {title}\n\n{body}\n" if body else ""


def build_markdown(item: dict, cfg: dict | None = None) -> str:
    """把结构化题面拼成一份可读的 Markdown（同时写入快照表与本地文件）。"""
    cfg = cfg or load_config()
    p = item["problem"]
    diff = item["difficulty"]
    tags = item["tags"]["all"]
    nodes = item["knowledge_nodes"]
    url = item["url"]
    pid = item["pid"]

    lines = [
        f"# {pid} {p.get('name') or pid}",
        "",
        f"> **来源**：[{pid} · 洛谷]({url})　|　**难度**：{diff['name']}（洛谷难度 {diff['value']}）"
        f"　|　**项目 5 档**：{diff['level']}",
        f"> **洛谷标签**：{'、'.join(tags) if tags else '（无）'}",
        f"> **归一知识点**：{'、'.join(nodes) if nodes else '（未匹配到知识点）'}",
        f"> **本地采集时间**：{item['captured_at']}",
        "> 题面版权归洛谷及出题人所有，仅供个人本地学习使用，请勿二次分发。",
        "",
        "---",
        "",
    ]

    content = p.get("content") or p.get("contenu") or {}
    if not content:
        trans = (item.get("raw_context", {}).get("data") or {}).get("translations") or {}
        content = trans.get("zh-CN") or {}
    for key, title in (("background", "题目背景"), ("description", "题目描述"),
                       ("formatI", "输入格式"), ("formatO", "输出格式")):
        section = _section(title, content.get(key))
        if section:
            lines.append(section)

    samples = p.get("samples") or []
    if samples:
        lines.append("## 样例\n")
        for idx, pair in enumerate(samples, start=1):
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            lines.append(f"### 样例 {idx}\n")
            lines.append("**输入**\n")
            lines.append("```text")
            lines.append(str(pair[0] or "").rstrip())
            lines.append("```\n")
            lines.append("**输出**\n")
            lines.append("```text")
            lines.append(str(pair[1] or "").rstrip())
            lines.append("```\n")

    hint = _section("说明 / 数据范围 / 提示", content.get("hint"))
    if hint:
        lines.append(hint)

    limits = p.get("limits") or {}
    times = [int(x) for x in (limits.get("time") or []) if str(x).isdigit()]
    mems = [int(x) for x in (limits.get("memory") or []) if str(x).isdigit()]
    stats = ["## 限制与统计\n"]
    if times:
        stats.append(f"- 时间限制：{max(times) / 1000:g} s")
    if mems:
        stats.append(f"- 内存限制：{max(mems) / 1024:g} MiB")
    if p.get("fullScore") is not None:
        stats.append(f"- 满分：{p.get('fullScore')}")
    stats.append(f"- 提交 / 通过：{p.get('totalSubmit', 0)} / {p.get('totalAccepted', 0)}")
    provider = (p.get("provider") or {}).get("name")
    if provider:
        stats.append(f"- 提供者：{provider}")
    stats.append("")
    lines.append("\n".join(stats))
    return "\n".join(lines).rstrip() + "\n"


def parse_problem_page(html: str, pid: str, cfg: dict, tagbook: TagBook | None = None,
                       url: str = "") -> dict:
    """解析题目页 HTML → 结构化的题面、标签、难度、知识点。"""
    ctx = parse_context(html)
    if ctx.get("status") == 404 or ctx.get("template") == "error":
        raise ProblemNotFound(f"{pid} 不存在或未公开")
    if ctx.get("status") != 200 or ctx.get("template") != "problem.show":
        raise LuoguError(f"{pid} 页面不是题目页：status={ctx.get('status')} template={ctx.get('template')}")
    data = ctx.get("data") or {}
    p = data.get("problem") or {}
    if not p or str(p.get("pid") or "").upper() != str(pid).upper():
        raise LuoguError(f"{pid} 题面数据不完整或题号不匹配")
    if not (p.get("content") or p.get("contenu") or data.get("translations")):
        raise LuoguError(f"{pid} 没有公开题面正文，已跳过")

    book = tagbook or load_tagbook(cfg=cfg)
    tags = book.names(list(p.get("tags") or []), cfg)
    diff = difficulty_info(p.get("difficulty"), cfg,
                           names=cfg.get("_difficulty_names") or {})
    item = {
        "pid": str(pid).upper(),
        "url": url or (cfg["site"].get("problem_url") or "").format(pid=pid),
        "problem": p,
        "raw_context": ctx,
        "tags": tags,
        "difficulty": diff,
        "knowledge_nodes": map_knowledge(tags["knowledge"], cfg),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    item["content_md"] = build_markdown(item, cfg)
    return item
