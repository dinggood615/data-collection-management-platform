from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

CNPC_HOSTS = {"cnpcbidding.com", "www.cnpcbidding.com"}
DATE_RE = re.compile(r"20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}(?:日)?")
CHALLENGE_WORDS = ("安全验证", "拖动滑块", "滑动验证", "点击验证", "验证码", "访问验证")
ROW_SELECTORS = (
    ".el-table__body-wrapper tbody tr", ".el-table__row", ".ant-table-tbody tr",
    "table tbody tr", ".tender-list li", ".notice-list li", ".list-item",
)


def is_cnpc_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in CNPC_HOSTS


def _normal_date(value: str) -> str:
    parts = re.findall(r"\d+", value)
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}" if len(parts) >= 3 else value


async def _rows(page):
    for selector in ROW_SELECTORS:
        candidate = page.locator(selector)
        if await candidate.count() >= 2:
            return candidate, selector
    return None, ""


async def _has_challenge(page) -> bool:
    try:
        text = (await page.locator("body").inner_text(timeout=5000))[:12000]
    except Exception:
        text = ""
    return any(word in text for word in CHALLENGE_WORDS) or await page.locator(
        "iframe[src*='captcha'], iframe[src*='verify'], .captcha, .verify-dialog"
    ).count() > 0


async def _wait_rows(page, timeout_ms: int = 25000):
    for _ in range(max(1, timeout_ms // 500)):
        rows, selector = await _rows(page)
        if rows is not None:
            return rows, selector
        if await _has_challenge(page):
            break
        await page.wait_for_timeout(500)
    return None, ""


async def profile_page(page, safe_url: str) -> dict[str, str]:
    if not is_cnpc_url(page.url):
        return {"url": safe_url, "engine": "中石油专用动态浏览器", "status": "需要人工验证", "selector": ROW_SELECTORS[0], "note": "可视 Chrome 当前没有打开中石油招标公告页，请点击“打开此站验证”。", "profile_json": ""}
    rows, selector = await _wait_rows(page)
    if rows is None:
        challenged = await _has_challenge(page)
        return {"url": safe_url, "engine": "中石油专用动态浏览器", "status": "需要人工验证" if challenged else "等待页面加载", "selector": ROW_SELECTORS[0], "note": "检测到网站验证弹框。请手动完成网站允许的验证，停留在招标公告列表后点击“重新识别”。" if challenged else "页面已打开，但公告表格尚未完成加载，请稍后重新识别。", "profile_json": ""}
    count = await rows.count()
    sample = " ".join((await rows.nth(i).inner_text()) for i in range(min(count, 8)))
    return {"url": safe_url, "engine": "中石油专用动态浏览器", "status": "已适配（动态浏览器）", "selector": selector, "note": f"验证会话有效，已识别 {count} 条公告行（抽样含 {len(DATE_RE.findall(sample))} 个日期）；定时采集将复用同一 Chrome 会话。", "profile_json": ""}


async def collect(page, site: dict, target_date: str, keywords: list[str], exclusions: list[str], result_factory, max_pages: int = 20):
    await page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
    rows, _ = await _wait_rows(page)
    if rows is None:
        if await _has_challenge(page):
            return [], f"{site['name']}：会话已失效，请点击“打开此站验证”完成一次人工验证"
        return [], f"{site['name']}：公告列表未能在限定时间内加载"
    found, seen = [], set()
    target = date.fromisoformat(target_date)
    for _ in range(max_pages):
        rows, _ = await _rows(page)
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
                published = date.fromisoformat(_normal_date(match.group(0)))
            except ValueError:
                continue
            page_dates.append(published)
            if published != target:
                continue
            link = row.locator("a[href]").first
            href = await link.get_attribute("href") if await link.count() else page.url
            title = (await link.inner_text()).strip() if await link.count() else context
            absolute = await page.evaluate("href => new URL(href, location.href).href", href or page.url)
            identity = f"{title}\n{absolute}"
            if identity in seen:
                continue
            seen.add(identity)
            result = result_factory(site, title, absolute, target_date, "中石油招标公告", context, keywords, exclusions)
            if result:
                found.append(result)
        if page_dates and min(page_dates) < target:
            break
        next_button = page.locator(".el-pagination .btn-next:not([disabled]), .ant-pagination-next:not(.ant-pagination-disabled) button, button:has-text('下一页'):not([disabled]), a:has-text('下一页')").first
        if not await next_button.count():
            break
        before = " ".join((await rows.first.inner_text()).split()) if await rows.count() else ""
        await next_button.click()
        try:
            await page.wait_for_function("before => { const row = document.querySelector('.el-table__row, table tbody tr, .list-item'); return row && row.innerText.trim() !== before; }", arg=before, timeout=15000)
        except Exception:
            break
        await page.wait_for_timeout(1200)
    return found, ""
