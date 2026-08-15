from __future__ import annotations

import ipaddress
import asyncio
import json
import os
import re
import socket
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from scrapling.fetchers import Fetcher

from ..matching import evaluate_relevance
from .cnpc import collect as collect_cnpc, is_cnpc_url, profile_page as profile_cnpc_page

DATE = re.compile(r"20\d{2}(?:[-./年])\d{1,2}(?:[-./月])\d{1,2}(?:日)?")
DYNAMIC_MARKERS = ("__NEXT_DATA__", "__NUXT__", "webpackJsonp", "vue", "react", "captcha", "验证码")
DETAIL_FETCH_LIMIT = max(1, min(int(os.getenv("DETAIL_FETCH_LIMIT", "20")), 50))
DETAIL_FETCH_DELAY = max(0.2, min(float(os.getenv("DETAIL_FETCH_DELAY", "0.6")), 5.0))
API_PAGE_LIMIT = max(1, min(int(os.getenv("API_PAGE_LIMIT", "30")), 100))
TITLE_KEYS = ("datatitle", "title", "subject", "noticetitle", "projectname", "name")
DATE_KEYS = ("releasetimestr", "releasedate", "publishtime", "publishdate", "createdate", "date", "time")
TYPE_KEYS = ("codemodename", "noticetype", "businessname", "categoryname", "type")

# Scrapling needs adaptive mode and a writable SQLite store before a response
# is created. This is only for our learned list CSS, never cookies or passwords.
Fetcher.configure(
    adaptive=True,
    storage_args={"storage_file": os.getenv("SCRAPLING_STORAGE_PATH", "data/scrapling-selectors.sqlite3")},
)


def validate_public_url(url: str) -> str:
    """Permit only public HTTP(S) destinations; prevents SSRF from the dashboard."""
    parsed = urlparse(url.strip())
    raw = url.strip()
    if len(raw) > 2048 or any(char.isspace() for char in raw) or raw.lower().count("http") != 1:
        raise ValueError("网址格式不正确：请只粘贴一条完整的公告列表网址")
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


def validate_site_name(name: str) -> str:
    value = " ".join(name.split())
    if not 2 <= len(value) <= 80:
        raise ValueError("网站名称需为 2 至 80 个字符")
    if "://" in value or "/" in value:
        raise ValueError("网站名称不能包含网址或路径，请只填写名称")
    return value


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
    if is_cnpc_url(safe_url):
        return {"url": safe_url, "engine": "中石油专用动态浏览器", "status": "待自动恢复",
                "selector": ".el-table__body-wrapper tbody tr", "profile_json": "",
                "note": "已识别为中石油招标网；系统将在采集时自动复用可用浏览器会话并重新适配。"}
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
        note = "页面疑似依赖 JavaScript 或会话；系统将在采集时自动尝试浏览器会话和规则重建。"
    else:
        note = "未能可靠识别公告列表和发布日期；为避免误采集，未启用自动采集。请检查是否为公开列表页。"
    return {"url": safe_url, "engine": "自动恢复", "status": "待自动恢复", "selector": "a", "note": note}


def auto_reprofile_site(url: str) -> dict[str, str]:
    """Rebuild a site profile using static discovery and any reusable browser session."""
    profile = profile_site(url)
    if profile["status"].startswith("已适配（"):
        return profile
    try:
        browser_profile = asyncio.run(profile_site_from_manual_browser(url))
        if browser_profile and browser_profile["status"].startswith("已适配（"):
            browser_profile["note"] = browser_profile["note"].replace("人工验证后的", "自动复用的")
            return browser_profile
    except Exception:
        pass
    return profile


def _walk_record_lists(value, path: str = "$") -> list[tuple[str, list[dict]]]:
    """Find JSON arrays that look like repeated public records."""
    found: list[tuple[str, list[dict]]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(_walk_record_lists(child, f"{path}.{key}"))
    elif isinstance(value, list):
        records = [item for item in value if isinstance(item, dict)]
        if len(records) >= 3 and len(records) >= len(value) * 0.8:
            found.append((path, records))
        for index, child in enumerate(value[:3]):
            if isinstance(child, (dict, list)):
                found.extend(_walk_record_lists(child, f"{path}[{index}]"))
    return found


def _field_for(records: list[dict], preferred: tuple[str, ...], kind: str) -> str:
    keys = list(dict.fromkeys(key for row in records[:10] for key in row))
    for wanted in preferred:
        for key in keys:
            if key.casefold() == wanted:
                return key
    for key in keys:
        values = [str(row.get(key, "")) for row in records[:10] if row.get(key) is not None]
        if kind == "title" and sum(len(value.strip()) >= 8 for value in values) >= 3:
            return key
        if kind == "date" and sum(bool(DATE.search(value)) for value in values) >= 3:
            return key
    return ""


def infer_api_profile(response_url: str, payload, site_url: str) -> dict | None:
    """Infer a reusable, non-secret profile from a same-origin public JSON response."""
    if not _same_public_host(site_url, response_url):
        return None
    best = None
    for path, records in _walk_record_lists(payload):
        title_field = _field_for(records, TITLE_KEYS, "title")
        date_field = _field_for(records, DATE_KEYS, "date")
        if not title_field or not date_field:
            continue
        score = len(records) + sum(bool(DATE.search(str(row.get(date_field, "")))) for row in records)
        candidate = (score, path, records, title_field, date_field)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return None
    _, path, records, title_field, date_field = best
    type_field = _field_for(records, TYPE_KEYS, "type")
    parsed = urlparse(response_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("t", None)
    page_param = next((key for key in query if key.casefold() in {"pagenum", "page", "pageindex", "current"}), "")
    size_param = next((key for key in query if key.casefold() in {"pagesize", "size", "limit"}), "")
    endpoint = urlunparse(parsed._replace(query=urlencode(query)))
    return {"version": 1, "mode": "public_json", "endpoint": endpoint, "records_path": path,
            "title_field": title_field, "date_field": date_field, "type_field": type_field,
            "page_param": page_param, "size_param": size_param, "sample_count": len(records)}


def _jsgx_feed_profiles(profile: dict, site_url: str) -> list[dict]:
    """Expand Jiangsu Guoxin's public portal into all relevant announcement feeds.

    The portal exposes these tabs through the same public, date-filterable GET
    endpoint. Keeping the mapping here avoids relying on visible tab labels or
    clicks, both of which are fragile in its Vue interface.
    """
    parsed = urlparse(profile.get("endpoint", ""))
    if urlparse(site_url).hostname != "ec.jsgx.net" or parsed.path != "/api-base/purchaseInfomation/list":
        return [profile]
    categories = (
        ("招标公告", "MBID_ANNOUNCEMENT,MBID_ANNOUNCEMENT_SELF", "-1", "public"),
        ("公开询比采购", "IQ_INQUIRY,IQ_INQUIRY_SELF", "12", "purchase"),
        ("公开谈判采购", "IQ_INQUIRY_COMPETE,IQ_INQUIRY_COMPETE_SELF", "18", "purchase"),
        ("直接采购公示", "RP_PLANNING,RP_PLANNING_SELF", "17", "public"),
    )
    feeds = []
    for label, business_types, code_mode, detail_mode in categories:
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(businessTypeArr=business_types, codeMode=code_mode, pageNum="1", pageSize="50",
                     custCode=query.get("custCode", "10000000"), searchContent="")
        query.pop("t", None)
        feed = dict(profile)
        feed.update(label=label, endpoint=urlunparse(parsed._replace(query=urlencode(query))),
                    page_param="pageNum", size_param="pageSize", start_date_param="releaseTimeStart",
                    end_date_param="releaseTimeEnd", detail_mode=detail_mode)
        feeds.append(feed)
    return feeds


def expand_api_profile(profile: dict, site_url: str) -> dict:
    feeds = _jsgx_feed_profiles(profile, site_url)
    if len(feeds) == 1:
        return profile
    expanded = dict(profile)
    expanded["feeds"] = feeds
    expanded["feed_count"] = len(feeds)
    return expanded


def _records_at_path(payload, path: str) -> list[dict]:
    value = payload
    for part in path.removeprefix("$.").split(".") if path != "$" else []:
        if "[" in part:
            part = part.split("[", 1)[0]
        value = value.get(part, {}) if isinstance(value, dict) else {}
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


async def profile_site_from_manual_browser(url: str) -> dict[str, str] | None:
    """Profile the page currently opened in the visible, user-approved Chrome.

    This deliberately reuses the browser profile after the user has completed any
    permitted login or verification.  It does not solve, bypass, or retry a
    challenge.  Returning ``None`` means the selected site is not open there.
    """
    safe_url = validate_public_url(url)
    from playwright.async_api import async_playwright

    expected_host = urlparse(safe_url).netloc.lower()
    cdp_url = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        pages = [page for context in browser.contexts for page in context.pages]
        page = next((item for item in reversed(pages) if urlparse(item.url).netloc.lower() == expected_host), None)
        if page is None:
            return None
        if is_cnpc_url(safe_url):
            return await profile_cnpc_page(page, safe_url)
        api_candidates: list[dict] = []

        async def inspect_response(response) -> None:
            try:
                if response.request.method != "GET" or not _same_public_host(safe_url, response.url):
                    return
                content_type = response.headers.get("content-type", "").lower()
                if "json" not in content_type:
                    return
                profile = infer_api_profile(response.url, await response.json(), safe_url)
                if profile:
                    api_candidates.append(profile)
            except Exception:
                return

        page.on("response", inspect_response)
        # Reload once so response listeners can observe the same public requests
        # the page itself makes. No challenge solving, credentials or headers are captured.
        await page.reload(wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1800)
        links = await page.locator("a[href]").evaluate_all(
            """items => items.slice(0, 250).map(item => ({
                title: (item.innerText || item.textContent || '').replace(/\\s+/g, ' ').trim(),
                href: item.href || '',
                context: ((item.closest('tr, li, article, .item, .list-item, .notice-item, .news-item') || item.parentElement || item).innerText || '').replace(/\\s+/g, ' ').trim()
            }))"""
        )
        cards = await page.locator("tr, li, article, [class*='card'], [class*='notice'], [class*='item']").evaluate_all(
            """items => items.slice(0, 350).map(item => {
                const text = (item.innerText || item.textContent || '').replace(/\\s+/g, ' ').trim();
                const heading = item.querySelector('h1,h2,h3,h4,[class*="title"],a');
                return {title: ((heading && (heading.innerText || heading.textContent)) || text).replace(/\\s+/g, ' ').trim(), context: text};
            })"""
        )

    if api_candidates:
        api_profile = expand_api_profile(max(api_candidates, key=lambda item: item.get("sample_count", 0)), safe_url)
        feed_note = f"，并展开 {api_profile['feed_count']} 个公告栏目" if api_profile.get("feed_count") else ""
        note = (f"已自动识别同源公开数据接口和 {api_profile['sample_count']} 条样本公告{feed_note}；"
                f"标题字段 {api_profile['title_field']}，日期字段 {api_profile['date_field']}。采集时按日期完整翻页，无需重复人工确认。")
        return {"url": safe_url, "engine": "智能公开数据接口", "status": "已适配（公开数据接口）",
                "selector": "", "note": note, "profile_json": json.dumps(api_profile, ensure_ascii=False)}
    usable = [item for item in links if len(item["title"]) >= 8 and item["href"].startswith(("http://", "https://"))]
    dated = sum(1 for item in usable if DATE.search(item["context"]))
    if len(usable) >= 3:
        note = f"已从自动复用的浏览器会话读取到 {len(usable)} 条候选链接，其中 {dated} 条标题含日期。"
        return {"url": safe_url, "engine": "可视 Chrome（自动复用）", "status": "已适配（动态浏览器）", "selector": "a", "note": note, "profile_json": ""}
    card_records = [item for item in cards if len(item["title"]) >= 8 and DATE.search(item["context"])]
    if len(card_records) >= 3:
        note = f"识别到 {len(card_records)} 条可点击公告卡片；该站点没有传统链接，后续采集将使用动态卡片模式。"
        profile = {"version": 1, "mode": "rendered_cards"}
        return {"url": safe_url, "engine": "可视 Chrome（智能卡片）", "status": "已适配（动态浏览器）",
                "selector": "tr, li, article, [class*='card'], [class*='notice'], [class*='item']",
                "note": note, "profile_json": json.dumps(profile, ensure_ascii=False)}
    return {"url": safe_url, "engine": "可视 Chrome（自动复用）", "status": "待自动恢复", "selector": "a", "note": "浏览器会话当前未识别到足够的公告链接，后续采集将继续自动尝试。"}


def _normalize_date(value: str) -> str:
    parts = re.findall(r"\d+", value)
    if len(parts) < 3:
        return value
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def _same_public_host(base_url: str, target_url: str) -> bool:
    return urlparse(base_url).hostname == urlparse(target_url).hostname and urlparse(target_url).scheme in {"http", "https"}


def _result_item(site: dict, title: str, href: str, target_date: str, notice_type: str, body: str,
                 keywords: list[str], exclusions: list[str], source_item_id: str = "") -> dict | None:
    result = evaluate_relevance(title, body, keywords, exclusions)
    if result.score < 20:
        return None
    excerpt = " ".join(body.split())[:240]
    return {"source": site["name"], "title": title, "url": href, "published_date": target_date,
            "notice_type": notice_type, "matched_terms": result.terms, "relevance_score": result.score,
            "relevance_level": result.level, "match_reason": "；".join(result.reasons), "excerpt": excerpt,
            "source_item_id": source_item_id}


def _response_text(response) -> str:
    for attribute in ("text", "body"):
        value = getattr(response, attribute, "")
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            return value
    return ""


def _fetch_detail_text(url: str) -> str:
    response = Fetcher.get(url, timeout=20, impersonate="chrome")
    soup = BeautifulSoup(_response_text(response), "html.parser")
    for unwanted in soup.select("script,style,noscript,svg"):
        unwanted.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:30000]


async def _collect_dynamic_site(site: dict, target_date: str, keywords: list[str], exclusions: list[str]) -> tuple[list[dict], str]:
    """Open one temporary tab in the verified browser and always close it again."""
    from playwright.async_api import async_playwright

    cdp_url = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            return [], f"{site['name']}：当前没有可复用的浏览器会话"
        context = browser.contexts[0]
        page = await context.new_page()
        try:
            if is_cnpc_url(site["url"]):
                return await collect_cnpc(page, site, target_date, keywords, exclusions, _result_item)
            await page.goto(site["url"], wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1800)
            entries = await page.locator("a[href]").evaluate_all(
                """items => items.slice(0, 350).map(item => ({
                    title: (item.innerText || item.textContent || '').replace(/\\s+/g, ' ').trim(),
                    href: item.href || '',
                    context: ((item.closest('tr, li, article, .item, .list-item, .notice-item, .news-item') || item.parentElement || item).innerText || '').replace(/\\s+/g, ' ').trim()
                }))"""
            )
            candidates, visited = [], set()
            for item in entries:
                title, href = item["title"], item["href"]
                if len(title) < 8 or href in visited or not _same_public_host(site["url"], href):
                    continue
                visited.add(href)
                date_match = DATE.search(item["context"])
                if date_match and _normalize_date(date_match.group(0)) == target_date:
                    candidates.append((title, href, item["context"]))
            found = []
            for title, href, context_text in candidates[:DETAIL_FETCH_LIMIT]:
                detail = await context.new_page()
                try:
                    await detail.goto(href, wait_until="domcontentloaded", timeout=30000)
                    await detail.wait_for_timeout(int(DETAIL_FETCH_DELAY * 1000))
                    body = await detail.locator("body").inner_text(timeout=10000)
                except Exception:
                    body = context_text
                finally:
                    await detail.close()
                result = _result_item(site, title, href, target_date, "人工验证后动态采集", body, keywords, exclusions)
                if result:
                    found.append(result)
        finally:
            await page.close()
    if not found and not entries:
        return [], f"{site['name']}：浏览器页面未读取到公告链接"
    return found, ""


def _fetch_public_json(url: str, site_url: str):
    if not _same_public_host(site_url, url):
        raise ValueError("接口地址与采集站点不同源")
    validate_public_url(url)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; DataCollectionPlatform/1.0)",
                                    "Accept": "application/json", "Referer": site_url})
    with urlopen(request, timeout=20) as response:
        if "json" not in response.headers.get_content_type():
            raise ValueError("接口没有返回 JSON")
        body = response.read(3 * 1024 * 1024 + 1)
        if len(body) > 3 * 1024 * 1024:
            raise ValueError("接口响应过大")
    return json.loads(body.decode("utf-8"))


def _public_detail_url(site_url: str, record: dict, feed: dict) -> str:
    if urlparse(site_url).hostname != "ec.jsgx.net":
        return site_url
    title = quote(str(record.get(feed["title_field"], "")), safe="")
    if feed.get("detail_mode") == "purchase":
        return (f"https://ec.jsgx.net/#/purchaseInformationDetails?pageType=check"
                f"&mode={quote(str(record.get('codeMode', '')), safe='')}&inquCode={quote(str(record.get('businessCode', '')), safe='')}&title={title}")
    identity = record.get("businessCode") if record.get("businessType") == "RP_PLANNING" else record.get("businessId")
    return (f"https://ec.jsgx.net/#/publicannouncement/publicNewDetail?pageType=check"
            f"&id={quote(str(identity or ''), safe='')}&noticeType={quote(str(record.get('businessType', '')), safe='')}"
            f"&date={quote(str(record.get(feed['date_field'], '')), safe='')}")


def _public_detail_text(site_url: str, record: dict, feed: dict) -> str:
    """Read only detail endpoints that are demonstrably public.

    Jiangsu Guoxin's direct-purchase publicity endpoint is public. Inquiry
    details currently return 401 and are deliberately not accessed further.
    """
    if urlparse(site_url).hostname != "ec.jsgx.net" or record.get("businessType") != "RP_PLANNING":
        return ""
    publicity_id = record.get("businessCode")
    if not publicity_id:
        return ""
    endpoint = f"https://ec.jsgx.net/api-purchase/publicity/supp/get?{urlencode({'publicityId': publicity_id})}"
    payload = _fetch_public_json(endpoint, site_url)
    if not isinstance(payload, dict) or payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
        return ""
    values: list[str] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value[:100]:
                walk(child)
        elif isinstance(value, (str, int, float)):
            text = " ".join(str(value).split())
            if text and text not in values:
                values.append(text)

    walk(payload["data"])
    return " ".join(values)[:30000]


def _collect_public_api(site: dict, target_date: str, keywords: list[str], exclusions: list[str]) -> tuple[list[dict], str]:
    try:
        profile = json.loads(site.get("profile_json") or "{}")
        if profile.get("mode") != "public_json":
            raise ValueError("接口配置无效")
        profile = expand_api_profile(profile, site["url"])
        feeds = profile.get("feeds") or [profile]
        found, visited, audits = [], set(), []
        for feed in feeds:
            parsed = urlparse(feed["endpoint"])
            base_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if feed.get("size_param"):
                base_query[feed["size_param"]] = "50"
            if feed.get("start_date_param"):
                base_query[feed["start_date_param"]] = target_date
            if feed.get("end_date_param"):
                base_query[feed["end_date_param"]] = target_date
            read_count, expected_total = 0, None
            for page_number in range(1, API_PAGE_LIMIT + 1):
                query = dict(base_query)
                if feed.get("page_param"):
                    query[feed["page_param"]] = str(page_number)
                page_url = urlunparse(parsed._replace(query=urlencode(query)))
                payload = _fetch_public_json(page_url, site["url"])
                if expected_total is None and isinstance(payload, dict) and str(payload.get("total", "")).isdigit():
                    expected_total = int(payload["total"])
                records = _records_at_path(payload, feed["records_path"])
                if not records:
                    break
                read_count += len(records)
                for record in records:
                    title = " ".join(str(record.get(feed["title_field"], "")).split())
                    date_match = DATE.search(str(record.get(feed["date_field"], "")))
                    if not title or not date_match or _normalize_date(date_match.group(0)) != target_date:
                        continue
                    identity = str(record.get("id") or record.get("businessId") or record.get("businessCode") or title)
                    unique_key = (feed.get("label", ""), identity)
                    if unique_key in visited:
                        continue
                    visited.add(unique_key)
                    notice_type = feed.get("label") or str(record.get(feed.get("type_field", ""), "公开公告")) or "公开公告"
                    body = " ".join(str(value) for value in record.values() if value is not None)
                    try:
                        detail_body = _public_detail_text(site["url"], record, feed)
                        if detail_body:
                            body = f"{body} {detail_body}"
                            time.sleep(DETAIL_FETCH_DELAY)
                    except Exception:
                        pass
                    result = _result_item(site, title, _public_detail_url(site["url"], record, feed), target_date,
                                          notice_type, body, keywords, exclusions, source_item_id=identity)
                    if result:
                        found.append(result)
                if not feed.get("page_param") or len(records) < int(base_query.get(feed.get("size_param", ""), 50)):
                    break
                if expected_total is not None and read_count >= expected_total:
                    break
                time.sleep(DETAIL_FETCH_DELAY)
            audits.append((feed.get("label", "公告"), expected_total, read_count))
        incomplete = [f"{label} {read}/{total}" for label, total, read in audits if total is not None and read < total]
        if incomplete:
            return found, f"{site['name']}：分页完整性检查未通过（{'；'.join(incomplete)}），请提高 API_PAGE_LIMIT 后重试"
        return found, ""
    except Exception as exc:
        return [], f"{site['name']}：公开数据接口采集失败：{type(exc).__name__}"


def collect_custom_site(site: dict, target_date: str, keywords: list[str], exclusions: list[str] | None = None) -> tuple[list[dict], str]:
    exclusions = exclusions or []
    if site["status"] == "已适配（公开数据接口）":
        return _collect_public_api(site, target_date, keywords, exclusions)
    if site["status"] == "已适配（动态浏览器）":
        try:
            return asyncio.run(_collect_dynamic_site(site, target_date, keywords, exclusions))
        except Exception as exc:
            return [], f"{site['name']}：动态浏览器采集失败：{type(exc).__name__}"
    if site["status"] != "已适配（静态列表）":
        return [], f"{site['name']}：当前规则不可用，等待自动重新适配"
    try:
        page = Fetcher.get(site["url"], timeout=20, impersonate="chrome")
        selector = site["list_selector"] or "a"
        try:
            links = page.css(selector, adaptive=True)
        except Exception:
            links = page.css(selector)
        html = _response_text(page)
        soup = BeautifulSoup(html, "html.parser")
        try:
            elements = soup.select(selector)
        except Exception:
            elements = soup.select("a[href]")
        if not elements:
            elements = soup.select("a[href]")
        candidates, visited = [], set()
        for link in elements:
            title = " ".join(link.get_text(" ", strip=True).split())
            href = urljoin(site["url"], link.get("href", ""))
            if len(title) < 8 or href in visited or not _same_public_host(site["url"], href):
                continue
            visited.add(href)
            container = link.find_parent(["tr", "li", "article"]) or link.parent or link
            context_text = " ".join(container.get_text(" ", strip=True).split())
            date_match = DATE.search(context_text)
            if not date_match or _normalize_date(date_match.group(0)) != target_date:
                continue
            candidates.append((title, href, context_text))
        candidates.sort(key=lambda item: any(word.casefold() in item[0].casefold() for word in keywords), reverse=True)
        found = []
        for index, (title, href, context_text) in enumerate(candidates[:DETAIL_FETCH_LIMIT]):
            if index:
                time.sleep(DETAIL_FETCH_DELAY)
            try:
                body = _fetch_detail_text(href)
            except Exception:
                body = context_text
            result = _result_item(site, title, href, target_date, "自动适配公告", body, keywords, exclusions)
            if result:
                found.append(result)
        return found, ""
    except Exception as exc:
        return [], f"{site['name']}：{type(exc).__name__}；将自动重新适配"
