from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

CNPC_HOSTS = {"cnpcbidding.com", "www.cnpcbidding.com"}
CNPC_LIST_URL = "https://www.cnpcbidding.com/#/tenders"
DATE_RE = re.compile(r"20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}(?:日)?")
CHALLENGE_WORDS = ("安全验证", "拖动滑块", "滑动验证", "点击验证", "验证码", "访问验证")
ROW_SELECTORS = (
    ".el-table__body-wrapper tbody tr", ".el-table__row", ".ant-table-tbody tr",
    "table tbody tr", ".tender-list li", ".notice-list li", ".list-item",
)


def is_cnpc_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in CNPC_HOSTS


def normalize_date(value: str) -> str:
    return value.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")


async def _find_rows(page):
    for selector in ROW_SELECTORS:
        locator = page.locator(selector)
        if await locator.count() >= 2:
            return locator
    return None


async def _challenge_visible(page) -> bool:
    try:
        text = (await page.locator("body").inner_text(timeout=5000))[:12000]
    except Exception:
        text = ""
    if any(word in text for word in CHALLENGE_WORDS):
        return True
    return await page.locator("iframe[src*='captcha'], iframe[src*='verify'], .captcha, .verify-dialog").count() > 0


async def _wait_for_list(page, timeout_ms: int = 25000):
    # SPA 页面不能用初始 HTML 或 network-idle 判断完成状态。
    for _ in range(max(1, timeout_ms // 500)):
        rows = await _find_rows(page)
        if rows is not None:
            return rows
        if await _challenge_visible(page):
            return None
        await page.wait_for_timeout(500)
    return None


async def profile_cnpc_page(page, safe_url: str) -> dict[str, str]:
    if not is_cnpc_url(page.url):
        return {"url": safe_url, "engine": "中石油专用动态浏览器", "status": "需要人工验证", "selector": ROW_SELECTORS[0], "note": "可视 Chrome 当前没有打开中石油招标公告页，请点击“打开此站验证”。"}
    rows = await _wait_for_list(page)
    if rows is None:
        challenged = await _challenge_visible(page)
        return {
            "url": safe_url, "engine": "中石油专用动态浏览器",
            "status": "需要人工验证" if challenged else "等待页面加载",
            "selector": ROW_SELECTORS[0],
            "note": "检测到网站验证弹框。请在可视 Chrome 中手动完成验证，停留在招标公告列表后点击“重新识别”。" if challenged else "页面已打开，但公告表格尚未完成加载；请稍后重新识别。",
        }
    count = await rows.count()
    sample = " ".join((await rows.nth(i).inner_text()) for i in range(min(count, 8)))
    dated = len(DATE_RE.findall(sample))
    return {"url": safe_url, "engine": "中石油专用动态浏览器", "status": "已适配（动态浏览器）", "selector": ROW_SELECTORS[0], "note": f"验证会话有效，已识别 {count} 条公告行（抽样含 {dated} 个日期）；定时采集将复用同一 Chrome 会话。"}


async def collect_cnpc(page, site: dict, target_date: str, keywords: list[str], max_pages: int = 20) -> tuple[list[dict], str]:
    await page.goto(site.get("url") or CNPC_LIST_URL, wait_until="domcontentloaded", timeout=60000)
    rows = await _wait_for_list(page)
    if rows is None:
        if await _challenge_visible(page):
            return [], f"{site['name']}：会话已失效，请点击“打开此站验证”完成一次人工验证"
        return [], f"{site['name']}：公告列表未能在限定时间内加载"

    results, seen = [], set()
    target = date.fromisoformat(target_date)
    keyword_pairs = [(word, word.casefold()) for word in keywords if word.strip()]
    for _ in range(max_pages):
        rows = await _find_rows(page)
        if rows is None:
            break
        page_dates = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            context = " ".join((await row.inner_text()).split())
            match = DATE_RE.search(context)
            if not match:
                continue
            try:
                published = date.fromisoformat(normalize_date(match.group(0)))
            except ValueError:
                continue
            page_dates.append(published)
            if published != target:
                continue
            link = row.locator("a[href]").first
            href = await link.get_attribute("href") if await link.count() else page.url
            title = (await link.inner_text()).strip() if await link.count() else context
            absolute_href = await page.evaluate("href => new URL(href, location.href).href", href or page.url)
            identity = f"{title}\n{absolute_href}"
            if identity in seen:
                continue
            seen.add(identity)
            terms = [word for word, folded in keyword_pairs if folded in context.casefold()]
            if terms:
                results.append({"source": site["name"], "title": title, "url": absolute_href, "published_date": target_date, "notice_type": "中石油招标公告", "matched_terms": terms})
        if page_dates and min(page_dates) < target:
            break
        next_button = page.locator(".el-pagination .btn-next:not([disabled]), .ant-pagination-next:not(.ant-pagination-disabled) button, button:has-text('下一页'):not([disabled]), a:has-text('下一页')").first
        if not await next_button.count():
            break
        before = " ".join((await rows.first.inner_text()).split()) if await rows.count() else ""
        await next_button.click()
        try:
            await page.wait_for_function("before => { const row = document.querySelector('.el-table__row, table tbody tr, .list-item'); return row && row.innerText.trim() !== before; }", before, timeout=15000)
        except Exception:
            break
        await page.wait_for_timeout(1200)
    return results, ""
