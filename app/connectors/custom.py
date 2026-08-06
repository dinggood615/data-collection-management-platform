from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

DATE = re.compile(r"20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}")


def validate_public_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("仅支持完整的公开 HTTP/HTTPS 地址")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("域名无法解析") from exc
    for address in addresses:
        value = ipaddress.ip_address(address[4][0])
        if not value.is_global:
            raise ValueError("不允许内网、回环或保留地址")
    return parsed.geturl()


def profile_site(url: str) -> dict[str, str]:
    safe_url = validate_public_url(url)
    page = Fetcher.get(safe_url, timeout=20, impersonate="chrome")
    links = []
    for link in page.css("a"):
        title = " ".join(link.css("::text").getall()).strip()
        href = link.attrib.get("href", "")
        if len(title) >= 8 and href:
            links.append((title, href))
    if not links:
        return {"url": safe_url, "engine": "待人工确认", "status": "需要人工确认", "selector": "a", "note": "未发现可用公告链接；页面可能依赖 JavaScript 或需要验证。"}
    dated = sum(1 for title, _ in links if DATE.search(title))
    status = "已适配（静态列表）" if dated or len(links) >= 8 else "需要人工确认"
    note = f"检测到 {len(links)} 个文本链接；{dated} 个链接标题含日期。仅使用普通请求，不会绕过验证。"
    return {"url": safe_url, "engine": "Fetcher", "status": status, "selector": "a", "note": note}


def collect_custom_site(site: dict, target_date: str, keywords: list[str]) -> tuple[list[dict], str]:
    if site["status"] != "已适配（静态列表）":
        return [], f"{site['name']}：等待人工确认适配规则"
    try:
        page = Fetcher.get(site["url"], timeout=20, impersonate="chrome")
        found, visited = [], set()
        for link in page.css(site["list_selector"] or "a"):
            title = " ".join(link.css("::text").getall()).strip()
            href = urljoin(site["url"], link.attrib.get("href", ""))
            if not title or not href or href in visited:
                continue
            visited.add(href)
            date_match = DATE.search(title)
            if not date_match:
                continue
            published = date_match.group(0).replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
            if published != target_date:
                continue
            terms = [word for word in keywords if word.casefold() in title.casefold()]
            if terms:
                found.append({"source": site["name"], "title": title, "url": href, "published_date": published, "notice_type": "自动适配公告", "matched_terms": terms})
        return found, ""
    except Exception as exc:
        return [], f"{site['name']}：{type(exc).__name__}"
