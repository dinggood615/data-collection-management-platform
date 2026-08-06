from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

DATE = re.compile(r"20\d{2}(?:[-./年])\d{1,2}(?:[-./月])\d{1,2}(?:日)?")
DYNAMIC_MARKERS = ("__NEXT_DATA__", "__NUXT__", "webpackJsonp", "vue", "react", "captcha", "验证码")

# Scrapling needs adaptive mode and a writable SQLite store before a response
# is created. This is only for our learned list CSS, never cookies or passwords.
Fetcher.configure(
    adaptive=True,
    storage_args={"storage_file": os.getenv("SCRAPLING_STORAGE_PATH", "data/scrapling-selectors.sqlite3")},
)


def validate_public_url(url: str) -> str:
    """Permit only public HTTP(S) destinations; prevents SSRF from the dashboard."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("仅支持完整的公开 HTTP/HTTPS 地址")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("域名无法解析") from exc
    for address in addresses:
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("不允许内网、回环或保留地址")
    return parsed.geturl()


def _text(element) -> str:
    return " ".join(element.css("::text").getall()).strip()


def _selector_candidates(page) -> list[str]:
    candidates = ["a"]
    for link in page.css("a")[:80]:
        classes = [item for item in link.attrib.get("class", "").split() if re.fullmatch(r"[A-Za-z_-][\w-]{1,48}", item)]
        if classes:
            candidates.append("a." + ".".join(classes[:2]))
    return list(dict.fromkeys(candidates))


def _score_selector(page, selector: str) -> tuple[int, int, int]:
    links = page.css(selector)
    usable = [(item, _text(item)) for item in links if item.attrib.get("href") and len(_text(item)) >= 8]
    dated = sum(1 for _, title in usable if DATE.search(title))
    score = min(len(usable), 40) + dated * 5 - (6 if selector == "a" and len(usable) > 30 else 0)
    return score, len(usable), dated


def profile_site(url: str) -> dict[str, str]:
    """Low-frequency, one-page profiling. It never retries or defeats a challenge."""
    safe_url = validate_public_url(url)
    page = Fetcher.get(safe_url, timeout=20, impersonate="chrome")
    best = max((_score_selector(page, item) + (item,) for item in _selector_candidates(page)), default=(0, 0, 0, "a"))
    score, count, dated, selector = best
    page_text = " ".join(page.css("script::text").getall()).lower()
    dynamic_hint = any(marker.lower() in page_text for marker in DYNAMIC_MARKERS)
    if count >= 3 and (dated >= 1 or score >= 10):
        try:
            page.css(selector, auto_save=True)
            learned = "已学习"
        except Exception:
            learned = "未保存"
        note = f"识别到 {count} 条候选公告、{dated} 条标题含日期；列表选择器 {selector!r}，结构记忆：{learned}。"
        return {"url": safe_url, "engine": "Fetcher + 自适应选择器", "status": "已适配（静态列表）", "selector": selector, "note": note}
    if dynamic_hint:
        note = "页面疑似依赖 JavaScript 或会话。请通过“可视 Chrome 人工验证”打开站点并完成允许的验证后，再重新自动适配。"
    else:
        note = "未能可靠识别公告列表和发布日期；为避免误采集，未启用自动采集。请检查是否为公开列表页。"
    return {"url": safe_url, "engine": "需要人工确认", "status": "待人工确认", "selector": "a", "note": note}


def _normalize_date(value: str) -> str:
    return value.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")


def collect_custom_site(site: dict, target_date: str, keywords: list[str]) -> tuple[list[dict], str]:
    if site["status"] != "已适配（静态列表）":
        return [], f"{site['name']}：等待人工确认适配规则"
    try:
        page = Fetcher.get(site["url"], timeout=20, impersonate="chrome")
        selector = site["list_selector"] or "a"
        try:
            links = page.css(selector, adaptive=True)
        except Exception:
            links = page.css(selector)
        found, visited = [], set()
        for link in links:
            title = _text(link)
            href = urljoin(site["url"], link.attrib.get("href", ""))
            if not title or not href or href in visited:
                continue
            visited.add(href)
            date_match = DATE.search(title)
            if not date_match or _normalize_date(date_match.group(0)) != target_date:
                continue
            terms = [word for word in keywords if word.casefold() in title.casefold()]
            if terms:
                found.append({"source": site["name"], "title": title, "url": href, "published_date": target_date, "notice_type": "自动适配公告", "matched_terms": terms})
        return found, ""
    except Exception as exc:
        return [], f"{site['name']}：{type(exc).__name__}；请重新自动适配或使用人工验证入口"
