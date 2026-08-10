from __future__ import annotations

from dataclasses import dataclass


EXPANSION_GROUPS = {
    "软件开发": ("系统开发", "平台开发", "应用开发", "二次开发", "定制开发", "研发服务"),
    "人力外包": ("人员外包", "劳务外包", "技术外包", "研发外包", "驻场服务", "人力框架"),
    "信息化": ("信息系统", "系统建设", "系统改造", "系统升级", "技术服务", "运维服务"),
    "数字化": ("数字化转型", "数字平台", "数据治理", "数据中台", "智慧平台", "智能化"),
}


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
    score += min(40, len(expanded_title) * 20) + min(20, len(expanded_body) * 10)
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
    if excluded_title or excluded_body:
        reasons.append("排除词：" + "、".join(dict.fromkeys(excluded_title + excluded_body)))
    return MatchResult(score, level, terms, reasons)
