"""Low-frequency China Huadian public-announcement collector.

It reproduces the four approved filters from the retired tenderbot service and
uses the platform's persistent Chrome session.  It never solves challenges.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Frame

LIST_URL = "https://www.chdtp.com/pages/wzglS/cgxx/caigou.jsp?cgtype=4"
DATE_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]")
DETAIL_RE = re.compile(r"toGetContent\(['\"]([^'\"]+)['\"]\)")
AUCTION_RE = re.compile(r"todetail\(['\"]([^'\"]+)['\"]\)")
BLOCK_RE = re.compile(r"访问频繁|请求过于频繁|access denied|too many requests|身份验证|去验证|验证码|人机验证|captcha", re.I)
CATEGORIES = (
    ("招标公告（服务）", 4, "#zbggsearchForm", "iframepage4", "#bustype", "3", "#id_gonggaoshrq", "id_gonggaoshrq", 3, 0, 2, None),
    ("谈判采购公告（服务）", 2, "#jzxtpsearchForm", "iframepage0", "#jhlxs1", "F", "#fbsjs", "fbsjs", 3, None, 0, 2),
    ("竞价采购公告", 7, "#jjcgsearchForm", "iframepage7", None, None, "#fbsjs7", "fbsjs", 2, 0, None, None),
    ("询比采购公告（服务）", 1, "#zbsearchForm", "iframepage1", "#jhlxs", "F", "#fbsj1", "fbsjs", 3, None, 0, 2),
)


async def _rows(frame: Frame) -> list[dict]:
    return await frame.locator("table tr").evaluate_all("""rows => rows.map(row => {
      const link=row.querySelector('td.td_2 a[title]'); return link ? {title:link.getAttribute('title')||'',href:link.getAttribute('href')||'',cells:[...row.querySelectorAll('td')].map(x=>(x.innerText||'').replace(/\\s+/g,' ').trim())}:null
    }).filter(Boolean)""")


async def _collect(target_date: str, keywords: list[str]) -> tuple[list[dict], str]:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222"))
        if not browser.contexts:
            return [], "中国华电：未发现已验证 Chrome 会话，请先人工验证"
        page = await browser.contexts[0].new_page()
        try:
            await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=45000)
            if BLOCK_RE.search((await page.locator("body").inner_text())[:20000]):
                return [], "中国华电：网站要求人工验证或限制访问，本次采集已停止"
            found=[]
            compact=target_date.replace("-", "")
            for name, tab_no, form_sel, frame_name, business_sel, business_val, date_sel, date_field, date_col, status_col, business_col, org_col in CATEGORIES:
                await page.locator(f'a[onclick*="showywtdT({tab_no})"]').first.click()
                form=page.locator(form_sel); await form.wait_for(state="visible", timeout=10000)
                frame=page.frame(name=frame_name)
                if frame is None: return found, f"中国华电：{name}结果框架未出现"
                if business_sel: await form.locator(business_sel).select_option(business_val)
                await form.locator(date_sel).evaluate("(e,v)=>{e.value=v;e.dispatchEvent(new Event('change',{bubbles:true}))}", compact)
                await form.locator("button.btn_t").first.click(); await frame.wait_for_timeout(1800)
                body=(await frame.locator("body").inner_text())[:20000]
                if BLOCK_RE.search(body): return found, "中国华电：网站要求人工验证或限制访问，本次采集已停止"
                for row in await _rows(frame):
                    cells=row["cells"]; date=DATE_RE.search(cells[date_col]) if len(cells)>date_col else None
                    if not date or date.group(1)!=target_date: continue
                    title=row["title"]; status=cells[status_col] if status_col is not None and len(cells)>status_col else ""; business=cells[business_col] if business_col is not None and len(cells)>business_col else ""; org=cells[org_col] if org_col is not None and len(cells)>org_col else ""
                    terms=[word for word in keywords if word.casefold() in "\n".join((title,status,business,org,name)).casefold()]
                    match=DETAIL_RE.search(row["href"]) or AUCTION_RE.search(row["href"])
                    if terms and match:
                        url=urljoin("https://www.chdtp.com/staticPage/",match.group(1)) if DETAIL_RE.search(row["href"]) else urljoin("https://www.chdtp.com",f"/webs/detailJjgg.action?chkedId={match.group(1)}")
                        found.append({"source":"中国华电电子商务平台","title":title,"url":url,"published_date":target_date,"notice_type":" · ".join(x for x in (name,business,status,org and f'采购组织：{org}') if x),"matched_terms":terms})
                await asyncio.sleep(random.uniform(3,5))
            return found, ""
        finally:
            await page.close()


def collect_chdtp(target_date: str, keywords: list[str]) -> tuple[list[dict], str]:
    try: return asyncio.run(_collect(target_date, keywords))
    except Exception as exc: return [], f"中国华电采集失败：{type(exc).__name__}"
