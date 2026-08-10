from __future__ import annotations

from dataclasses import dataclass


EXPANSION_GROUPS = {
    "软件开发": ("系统开发", "平台开发", "应用开发", "二次开发", "定制开发", "研发服务"),
    "人力外包": ("人员外包", "劳务外包", "技术外包", "研发外包", "驻场服务", "人力框架"),
    "信息化": ("信息系统", "系统建设", "系统改造", "系统升级", "技术服务", "运维服务"),
    "数字化": ("数字化转型", "数字平台", "数据治理", "数据中台", "智慧平台", "智能化"),
}

BUSINESS_OBJECTS = ("软件", "系统", "平台", "应用", "数据", "信息化", "数字化", "网络安全", "流程管理")
SERVICE_ACTIONS = ("开发", "建设", "改造", "升级", "实施", "集成", "运维", "技术服务", "外包", "驻场", "框架")
PRODUCT_ONLY_PHRASES = ("正版软件采购", "软件许可采购", "办公软件采购", "杀毒软件采购", "硬件设备采购", "仪器仪表采购")


@dataclass(frozen=True)
class MatchResult:
    score: int
    level: str
    terms: list[str]
    reasons: list[str]


def parse_terms(value: str) -> list[str]:
    normalized = value.replace("，", ",").replace("；", ",").replace(";", ",").replace("\r", "\n")
    return list(dict.fromkeys(item.strip() for part in normalized.split("\n") for item in part.split(",") if item.strip()))


def expanded_terms(keywords: list[str]) -> list[str]:
    result: list[str] = []
    for keyword in keywords:
        folded = keyword.casefold()
        for anchor, candidates in EXPANSION_GROUPS.items():
            if anchor.casefold() in folded or folded in anchor.casefold():
                result.extend(candidates)
    return list(dict.fromkeys(result))


def evaluate_relevance(title: str, body: str, keywords: list[str], exclusions: list[str]) -> MatchResult:
    title_folded, body_folded = title.casefold(), body.casefold()
    core_title = [term for term in keywords if term.casefold() in title_folded]
    core_body = [term for term in keywords if term.casefold() in body_folded and term not in core_title]
    expansions = expanded_terms(keywords)
    expanded_title = [term for term in expansions if term.casefold() in title_folded]
    expanded_body = [term for term in expansions if term.casefold() in body_folded and term not in expanded_title]
    excluded_title = [term for term in exclusions if term.casefold() in title_folded]
    excluded_body = [term for term in exclusions if term.casefold() in body_folded and term not in excluded_title]

    score = min(80, len(core_title) * 40) + min(40, len(core_body) * 20)
    # Synonyms are recall helpers, not sufficient evidence by themselves.
    # Requiring either two signals or a business-object/action combination
    # prevents broad terms such as “技术服务” from flooding the report.
    score += min(30, len(expanded_title) * 15) + min(16, len(expanded_body) * 8)
    domain_enabled = any(anchor.casefold() in " ".join(keywords).casefold() for anchor in EXPANSION_GROUPS)
    title_objects = [term for term in BUSINESS_OBJECTS if term.casefold() in title_folded]
    title_actions = [term for term in SERVICE_ACTIONS if term.casefold() in title_folded]
    body_objects = [term for term in BUSINESS_OBJECTS if term.casefold() in body_folded]
    body_actions = [term for term in SERVICE_ACTIONS if term.casefold() in body_folded]
    if domain_enabled and title_objects and title_actions:
        score += 25
    elif domain_enabled and body_objects and body_actions:
        score += 12
    product_only = [phrase for phrase in PRODUCT_ONLY_PHRASES if phrase.casefold() in title_folded]
    score -= min(60, len(product_only) * 35)
    score -= min(80, len(excluded_title) * 45) + min(40, len(excluded_body) * 25)
    score = max(0, min(score, 100))
    level = "高相关" if score >= 60 else "可能相关" if score >= 20 else "低相关"

    terms = list(dict.fromkeys(core_title + core_body + expanded_title + expanded_body))
    reasons: list[str] = []
    if core_title:
        reasons.append("标题核心词：" + "、".join(core_title))
    if core_body:
        reasons.append("正文核心词：" + "、".join(core_body))
    if expanded_title:
        reasons.append("标题同义词：" + "、".join(expanded_title))
    if expanded_body:
        reasons.append("正文同义词：" + "、".join(expanded_body))
    if domain_enabled and title_objects and title_actions:
        reasons.append("标题业务组合：" + title_objects[0] + "+" + title_actions[0])
    elif domain_enabled and body_objects and body_actions:
        reasons.append("正文业务组合：" + body_objects[0] + "+" + body_actions[0])
    if product_only:
        reasons.append("产品型采购降权：" + "、".join(product_only))
    if excluded_title or excluded_body:
        reasons.append("排除词：" + "、".join(dict.fromkeys(excluded_title + excluded_body)))
    return MatchResult(score, level, terms, reasons)
