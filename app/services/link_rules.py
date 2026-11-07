"""题号 → 链接规则引擎（配置驱动，零硬编码站点逻辑）。

规则来自 ``config/link_rules.yaml`` + ``config/link_rules.custom.yaml`` 覆盖层，
新增站点或修正链接格式只需改 YAML，不需要改 Python 代码。

合规边界：本模块只做本地字符串推导与文本扫描，**不发起任何网络请求**，
不抓取、不解析、不存储任何第三方题目页面内容（D-20 / NFR-05 / RK-09）。
"""
from __future__ import annotations

import copy
import re
import threading
from typing import Any

import yaml

from ..config import CONFIG_DIR, SOURCES
from ..errors import ValidationError

BUILTIN_PATH = CONFIG_DIR / "link_rules.yaml"
CUSTOM_PATH = CONFIG_DIR / "link_rules.custom.yaml"

_TOKEN = re.compile(r"\{(\d+)([UL])?\}")
_URL_TOKEN = re.compile(r"https?://[^\s,;，；|)\]】]+", re.IGNORECASE)
_RANGE = re.compile(r"^([A-Za-z]*?)(\d+)\s*[-~—]\s*([A-Za-z]*?)(\d+)$")
_LOCK = threading.Lock()

_cache: dict[str, Any] | None = None
_cache_stamp: tuple[int, int, int, int] | None = None


# ---------------------------------------------------------------- 配置加载

def _stamp() -> tuple[int, int, int, int]:
    def info(path):
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return (0, 0)
    return (*info(BUILTIN_PATH), *info(CUSTOM_PATH))


def _read_yaml(path) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"链接规则 {path.name} 不是合法 YAML：{exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"链接规则 {path.name} 顶层必须是字典")
    return data


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _validate(data: dict) -> None:
    rules = data.get("rules") or {}
    if not isinstance(rules, dict):
        raise ValidationError("链接规则 rules 必须是字典")
    for source, rule in rules.items():
        if not isinstance(rule, dict):
            raise ValidationError(f"规则 {source} 必须是字典")
        for variant in _variants_of(rule):
            for field in ("key_pattern", "url_template"):
                value = variant.get(field) or ""
                if value and field == "key_pattern":
                    try:
                        re.compile(value)
                    except re.error as exc:
                        raise ValidationError(f"规则 {source} 的 key_pattern 非法：{exc}") from exc
            for pattern in variant.get("url_regexes") or []:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValidationError(f"规则 {source} 的 url_regexes 非法：{exc}") from exc
            for pattern in variant.get("scan_regexes") or []:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValidationError(f"规则 {source} 的 scan_regexes 非法：{exc}") from exc
        for item in rule.get("normalize") or []:
            try:
                re.compile(item["regex"])
            except (KeyError, re.error) as exc:
                raise ValidationError(f"规则 {source} 的 normalize.regex 非法：{exc}") from exc


def _load() -> dict:
    global _cache, _cache_stamp
    stamp = _stamp()
    with _LOCK:
        if _cache is not None and _cache_stamp == stamp:
            return _cache
        builtin = _read_yaml(BUILTIN_PATH)
        custom = _read_yaml(CUSTOM_PATH)
        merged = _deep_merge({k: v for k, v in builtin.items() if k != "rules"},
                             {k: v for k, v in custom.items() if k != "rules"})
        merged["rules"] = _deep_merge(builtin.get("rules") or {}, custom.get("rules") or {})
        _validate(merged)
        _cache = merged
        _cache_stamp = stamp
        return merged


def reload_rules() -> dict:
    """清缓存并重新读取配置（编辑 YAML 后立即生效）。"""
    global _cache, _cache_stamp
    with _LOCK:
        _cache = None
        _cache_stamp = None
    return _load()


def get_rules() -> dict:
    return _load().get("rules") or {}


def get_settings() -> dict:
    return _load().get("settings") or {}


def version() -> int:
    return int(_load().get("version", 1))


def get_rule(source: str) -> dict | None:
    return get_rules().get(source)


def known_sources() -> dict[str, str]:
    """供题目校验使用的来源表：内置 SOURCES + 自定义规则里新增的 source。"""
    out = dict(SOURCES)
    for source, rule in get_rules().items():
        out[source] = rule.get("label") or out.get(source, source)
    return out


def _variants_of(rule: dict) -> list[dict]:
    variants = rule.get("variants")
    if isinstance(variants, list) and variants:
        return [v for v in variants if isinstance(v, dict)]
    return [rule]  # 隐式单变体


# ---------------------------------------------------------------- 模板与归一化

def _render(template: str, match: re.Match, key: str = "") -> str:
    """展开 {key} / {0} / {n} / {nU} / {nL} 占位符。"""
    def repl(m: re.Match) -> str:
        idx = int(m.group(1))
        try:
            value = match.group(0) if idx == 0 else match.group(idx)
        except IndexError:  # 模板引用了不存在的捕获组
            return ""
        value = value or ""
        if m.group(2) == "U":
            return value.upper()
        if m.group(2) == "L":
            return value.lower()
        return value

    return _TOKEN.sub(repl, str(template or "")).replace("{key}", key or "")


def normalize_key(source: str, raw_key: Any) -> str:
    """按规则反复应用 normalize，直到题号不再变化（最多 3 轮）。"""
    key = str(raw_key or "").strip()
    rule = get_rule(source)
    if not rule or not key:
        return key
    for _ in range(3):
        changed = False
        for item in rule.get("normalize") or []:
            match = re.fullmatch(item["regex"], key, re.IGNORECASE)
            if not match:
                continue
            new_key = _render(item.get("template", "{0}"), match, key)
            if new_key and new_key != key:
                key = new_key
                changed = True
                break
        if not changed:
            break
    return key


def _resolve_full(source: str, raw_key: Any, variant_id: str | None = None) -> dict | None:
    rule = get_rule(source)
    if not rule:
        return None
    key = normalize_key(source, raw_key)
    if not key:
        return None
    for variant in _variants_of(rule):
        if variant_id and variant.get("id", "default") != variant_id:
            continue
        pattern = variant.get("key_pattern")
        if not pattern:
            continue
        match = re.fullmatch(pattern, key)
        if not match:
            continue
        url = _render(variant.get("url_template") or "", match, key) if variant.get("url_template") else ""
        return {
            "source": source,
            "key": key,
            "url": url,
            "variant": variant.get("id", "default"),
            "variant_label": variant.get("label", ""),
            "label": rule.get("label", source),
            "match": match,
            "spec": variant,
        }
    return None


def resolve(source: str, raw_key: Any, variant_id: str | None = None) -> dict | None:
    """返回可序列化的解析结果；题号不合法时返回 None。"""
    result = _resolve_full(source, raw_key, variant_id)
    if result is None:
        return None
    return {k: v for k, v in result.items() if k not in ("match", "spec")}


def derive_url(source: str, raw_key: Any, variant_id: str | None = None) -> str:
    """题号 → 原题链接；无法推导时返回空串（保持与旧 catalog.derive_url 兼容）。"""
    result = _resolve_full(source, raw_key, variant_id)
    return result["url"] if result else ""


def is_valid_key(source: str, raw_key: Any) -> bool:
    return _resolve_full(source, raw_key) is not None


def parse_url(url: str) -> dict | None:
    """从任意已配置站点的链接反解出 (source, source_key)。"""
    url = str(url or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return None
    for source, rule in get_rules().items():
        for variant in _variants_of(rule):
            for pattern in variant.get("url_regexes") or []:
                match = re.search(pattern, url, re.IGNORECASE)
                if not match:
                    continue
                key = _render(variant.get("url_key_template") or "{1}", match, "")
                key = normalize_key(source, key)
                if _resolve_full(source, key, variant.get("id")) is None:
                    continue
                return {
                    "source": source, "source_key": key,
                    "url": derive_url(source, key, variant.get("id")) or url,
                    "variant": variant.get("id", "default"),
                    "label": rule.get("label", source),
                }
    return None


# ---------------------------------------------------------------- 校验

def check(source: str, raw_key: Any, url: str = "") -> dict:
    """检查一条题目的题号与链接是否符合规则，并给出修复建议。"""
    result = {"source": source, "source_key": str(raw_key or ""), "url": url or "",
              "ok": True, "issue": "", "expected_url": "", "reason": ""}
    rule = get_rule(source)
    if rule is None:
        result.update(ok=False, issue="unknown_source", reason=f"未配置来源 {source} 的链接规则")
        return result
    resolved = _resolve_full(source, raw_key)
    if resolved is None:
        result.update(ok=False, issue="invalid_key",
                      reason="题号不符合该来源的 key_pattern")
        return result
    expected = resolved["url"]
    result["expected_url"] = expected
    current = (url or "").strip()
    if not expected:
        # other 等无模板来源：只要求显式 http(s) 链接
        if not re.match(r"^https?://", current, re.IGNORECASE):
            result.update(ok=False, issue="missing_url", reason="该来源需显式填写 http(s) 链接")
        return result
    if current == expected:
        return result
    if current:
        parsed = parse_url(current)
        if parsed and parsed["source"] == source and parsed["source_key"] == resolved["key"]:
            result["reason"] = "链接为同一题的合法变体"
            return result
    result.update(ok=False, issue="url_mismatch" if current else "missing_url",
                  reason="链接与规则推导结果不一致" if current else "缺少原题链接")
    return result


# ---------------------------------------------------------------- 扫描

def _guess_source(token: str) -> str | None:
    for source in get_rules():
        if _resolve_full(source, token) is not None:
            return source
    return None


def _expand_range(token: str, source: str) -> list[str] | None:
    """展开 P1001-P1010 / 254-260 / LQ254-LQ260 之类的连续题号。"""
    rule = get_rule(source) or {}
    if not rule.get("range_enabled", True):
        return None
    match = _RANGE.fullmatch(token.strip())
    if not match:
        return None
    left_prefix, left_num, right_prefix, right_num = match.groups()
    if right_prefix and right_prefix.upper() != left_prefix.upper():
        return None
    start, end = int(left_num), int(right_num)
    if end < start or end - start + 1 > int(get_settings().get("max_range_items", 200)):
        return None
    return [f"{left_prefix}{n}" for n in range(start, end + 1)]


def _strip_alias(line: str) -> tuple[str, str | None]:
    """识别 'luogu P1001' / '蓝桥 254' / 'LQ254' 这类行内来源前缀。"""
    for source, rule in get_rules().items():
        for alias in [source] + list(rule.get("aliases") or []):
            alias = str(alias)
            if len(line) <= len(alias) or line[:len(alias)].lower() != alias.lower():
                continue
            separator = line[len(alias)]
            if separator in " \t:：-_":
                rest = line[len(alias) + 1:].strip()
                if rest:
                    return rest, source
            # 无分隔符前缀，如 LQ254 / CF71A / luogu1001
            if separator.isdigit() and alias.isalpha():
                rest = line[len(alias):].strip()
                if rest:
                    return rest, source
    return line, None


def _candidate(source: str, token: str, title: str = "", url: str = "", origin: str = "") -> dict | None:
    resolved = _resolve_full(source, token)
    if resolved is None:
        # 混合来源清单（如同时粘贴洛谷 P 题号与蓝桥数字编号）自动识别归属
        guessed = _guess_source(token)
        if not guessed:
            return None
        source = guessed
        resolved = _resolve_full(source, token)
    if resolved is None:
        return None
    return {
        "source": source, "source_key": resolved["key"],
        "title": title.strip() or resolved["key"],
        "url": url or resolved["url"],
        "variant": resolved["variant"],
        "source_label": resolved["label"],
        "origin": origin or token,
    }


def _append(out: list, seen: set, item: dict | None, limit: int) -> bool:
    """返回 False 表示已达上限。"""
    if item is None:
        return True
    key = (item["source"], item["source_key"])
    if key in seen or len(out) >= limit:
        return len(out) < limit
    seen.add(key)
    out.append(item)
    return len(out) < limit


def scan(text: str, source: str = "auto", mode: str = "lines",
         default_source: str = "luogu", max_items: int | None = None) -> dict:
    """从文本中扫描题号 / 链接，产出候选题目（不访问网络、不写库）。

    - mode=lines：按行解析，可带标题，如 ``254 A+B 问题``；支持连续题号展开。
    - mode=text ：全文正则扫描（只识别带前缀的题号与完整链接，避免误抓普通数字）。
    - source=auto：自动识别带前缀题号；纯数字按 default_source 处理，避免静默录错。
    """
    limit = int(max_items or get_settings().get("max_scan_items", 300))
    rules = get_rules()
    items: list[dict] = []
    issues: list[dict] = []
    seen: set[tuple[str, str]] = set()
    text = str(text or "")
    continue_scan = True

    def add(item, line_no=0, token=""):
        nonlocal continue_scan
        if not _append(items, seen, item, limit):
            continue_scan = False

    if mode == "text":
        hits: list[tuple[int, dict]] = []
        for match in _URL_TOKEN.finditer(text):
            parsed = parse_url(match.group(0))
            if parsed:
                item = _candidate(parsed["source"], parsed["source_key"], url=parsed["url"],
                                  origin=match.group(0))
                if item:
                    hits.append((match.start(), item))
        for src, rule in rules.items():
            for variant in _variants_of(rule):
                for pattern in variant.get("scan_regexes") or []:
                    for match in re.finditer(pattern, text, re.IGNORECASE):
                        token = match.group(1) if match.re.groups >= 1 else match.group(0)
                        item = _candidate(src, token, origin=match.group(0))
                        if item:
                            hits.append((match.start(), item))
        for _, item in sorted(hits, key=lambda pair: pair[0]):
            if not continue_scan:
                break
            add(item)
        return {"items": items, "issues": issues, "mode": mode, "total": len(items),
                "truncated": len(items) >= limit, "max_items": limit}

    # ---- 按行解析 ----
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if not continue_scan:
            break
        line = re.sub(r"^[-*•>]\s*", "", raw_line.strip())
        if not line or line.startswith("#"):
            continue
        if "|" in line:  # Markdown 表格：取前两列
            cells = [c.strip() for c in line.strip("|").split("|") if c.strip()]
            if cells:
                line = " ".join(cells[:2])

        line_source = None if source in ("", "auto", None) else str(source)
        line, alias_source = _strip_alias(line)
        if alias_source:
            line_source = alias_source

        urls = _URL_TOKEN.findall(line)
        plain = _URL_TOKEN.sub(" ", line).strip()
        parsed_urls = [parse_url(u) for u in urls]

        if urls:
            if not any(parsed_urls):
                issues.append({"line": line_no, "text": raw_line.strip(),
                               "reason": "链接不属于任何已配置来源，可在规则配置中新增"})
                continue
            for parsed in parsed_urls:
                if not parsed or not continue_scan:
                    continue
                title = re.sub(r"\s+", " ", plain).strip()
                add(_candidate(parsed["source"], parsed["source_key"], title=title,
                               url=parsed["url"], origin=raw_line.strip()), line_no)
            continue

        tokens = [t for t in re.split(r"[\s,，\t]+", plain) if t]
        if not tokens:
            issues.append({"line": line_no, "text": raw_line.strip(),
                           "reason": "未能识别题号或链接"})
            continue
        token, rest = tokens[0], tokens[1:]

        effective_source = line_source
        range_match = _RANGE.fullmatch(token.strip())
        if range_match and not effective_source:
            # 先按区间左端判断题号体系，避免 "P1001-P1005" 被整串误当成 slug
            left_token = f"{range_match.group(1)}{range_match.group(2)}"
            effective_source = (default_source if re.fullmatch(r"\d+", left_token)
                                else (_guess_source(left_token) or default_source))
        elif not effective_source and re.fullmatch(r"\d+", token):
            effective_source = default_source  # 裸数字必须有一个明确归属
        if not effective_source:
            effective_source = _guess_source(token)

        expanded = _expand_range(token, effective_source) if effective_source else None
        if expanded:
            for piece in expanded:
                if not continue_scan:
                    break
                item = _candidate(effective_source, piece,
                                  title=" ".join(rest), origin=raw_line.strip())
                if item:
                    add(item, line_no)
                else:
                    issues.append({"line": line_no, "text": piece,
                                   "reason": f"题号不符合 {effective_source} 的规则"})
            continue

        item = _candidate(effective_source or "", token, title=" ".join(rest),
                          origin=raw_line.strip()) if effective_source else None
        if item:
            add(item, line_no)
        else:
            issues.append({"line": line_no, "text": raw_line.strip(),
                           "reason": f"题号不符合 {effective_source or '任何'} 来源的规则，或需指定来源"})

    return {"items": items, "issues": issues, "mode": mode, "total": len(items),
            "truncated": len(items) >= limit, "max_items": limit}


# ---------------------------------------------------------------- 配置读写

def rules_summary() -> dict:
    """给界面展示的规则清单（含自定义覆盖层原文）。"""
    out = []
    for source, rule in get_rules().items():
        variants = []
        for variant in _variants_of(rule):
            variants.append({
                "id": variant.get("id", "default"),
                "label": variant.get("label", rule.get("label", source)),
                "key_pattern": variant.get("key_pattern", ""),
                "url_template": variant.get("url_template", ""),
                "url_regexes": list(variant.get("url_regexes") or []),
                "scan_regexes": list(variant.get("scan_regexes") or []),
                "examples": list(variant.get("examples") or rule.get("examples") or []),
            })
        out.append({
            "source": source,
            "label": rule.get("label", source),
            "aliases": list(rule.get("aliases") or []),
            "note": str(rule.get("note") or ""),
            "range_enabled": bool(rule.get("range_enabled", True)),
            "normalize": list(rule.get("normalize") or []),
            "examples": list(rule.get("examples") or []),
            "variants": variants,
            "custom": source in (_read_yaml(CUSTOM_PATH).get("rules") or {}),
        })
    custom_text = CUSTOM_PATH.read_text(encoding="utf-8") if CUSTOM_PATH.exists() else "version: 1\nrules: {}\n"
    return {"version": _load().get("version", 1), "settings": get_settings(),
            "items": out, "custom_yaml": custom_text,
            "builtin_path": str(BUILTIN_PATH), "custom_path": str(CUSTOM_PATH)}


def save_custom_yaml(content: str) -> dict:
    """保存用户覆盖层并热加载；保存前完整校验，非法配置不落盘。"""
    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"YAML 解析失败：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("rules", {}), dict):
        raise ValidationError("顶层必须是字典，且 rules 必须是字典")
    # 合并到内置规则后整体校验，确保不会写出破坏内置规则的覆盖层
    builtin = _read_yaml(BUILTIN_PATH)
    merged = _deep_merge(builtin.get("rules") or {}, data.get("rules") or {})
    _validate({"rules": merged})
    CUSTOM_PATH.write_text(content, encoding="utf-8")
    reload_rules()
    return {"ok": True, "sources": list(get_rules()), "message": "规则已保存并立即生效"}
