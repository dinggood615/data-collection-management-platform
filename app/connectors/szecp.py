from __future__ import annotations

import os


async def _collect(target_date: str, enabled: set[str], keywords: list[str]) -> tuple[list[dict], str]:
    from playwright.async_api import async_playwright
    cdp_url = os.getenv("CHROME_CDP_URL", "http://host.docker.internal:9222")
    entries = []
    sources = (("szecp_tender", "华润守正招标公告", "https://www.szecp.com.cn/first_zbgg/index.html", "/first_zbgg/"), ("szecp_purchase", "华润守正采购公告", "https://www.szecp.com.cn/first_cggg/index.html", "/first_cggg/"))
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            return [], "华润站：未发现已人工验证的 Chrome 会话"
        context = browser.contexts[0]
        for code, name, url, marker in sources:
            if code not in enabled:
                continue
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(2500)
                if not await page.locator("select.szb-zbcgSearch-type").count():
                    return entries, "华润站需要重新人工安全验证"
                await page.locator("select.szb-zbcgSearch-type").select_option(label="服务")
                for selector in ("#date-start", "#date-end"):
                    await page.locator(selector).evaluate("(el,value)=>{el.removeAttribute('readonly');el.value=value;el.dispatchEvent(new Event('change',{bubbles:true}))}", target_date)
                await page.locator("button[class^='szb-zbcgSearch-key']").first.click()
                await page.wait_for_timeout(3500)
                rows = await page.locator(".szb-zbcgTable-other").evaluate_all("rows=>rows.map(r=>{let c=[...r.children],a=r.querySelector('a');return {title:a?.innerText?.trim(),url:a?.href,type:c[3]?.innerText?.trim(),date:c[4]?.innerText?.trim()}})")
                for row in rows:
                    terms = [word for word in keywords if word.casefold() in (row["title"] or "").casefold()]
                    if row["date"] == target_date and row["type"] == "服务" and marker in (row["url"] or "") and terms:
                        entries.append({"source": name, "title": row["title"], "url": row["url"], "published_date": row["date"], "notice_type": "服务", "matched_terms": terms})
            finally:
                await page.close()
    return entries, ""


def collect_szecp(target_date: str, enabled: set[str], keywords: list[str]) -> tuple[list[dict], str]:
    import asyncio
    try:
        return asyncio.run(_collect(target_date, enabled, keywords))
    except Exception as exc:
        return [], f"华润站采集失败：{type(exc).__name__}"
