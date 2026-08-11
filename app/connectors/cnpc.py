from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

CNPC_HOSTS = {"cnpcbidding.com", "www.cnpcbidding.com"}
CHALLENGE_WORDS = ("安全验证", "拖动滑块", "滑动验证", "点击验证", "验证码", "访问验证")
VUE_COMPONENT = """() => {
  const app = document.querySelector('#app');
  const root = app && app.__vue__ && app.__vue__.$root;
  let hit = null;
  const walk = value => {
    if (!value || hit) return;
    const data = value.$data || {};
    const methods = (value.$options && value.$options.methods) || {};
    if (Array.isArray(data.list) && data.pageInfo && methods.articlePage && methods.handleCurrentChange) {
      hit = value;
      return;
    }
    for (const child of value.$children || []) walk(child);
  };
  walk(root);
  return hit;
}"""


def is_cnpc_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in CNPC_HOSTS


def _normal_date(value: str) -> str:
    parts = re.findall(r"\d+", value)
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}" if len(parts) >= 3 else value


async def _has_challenge(page) -> bool:
    try:
        text = (await page.locator("body").inner_text(timeout=5000))[:12000]
    except Exception:
        text = ""
    if any(word in text for word in CHALLENGE_WORDS):
        return True
    return await page.locator(
        "iframe[src*='captcha']:visible, iframe[src*='verify']:visible, "
        ".captcha:visible, .verify-dialog:visible"
    ).count() > 0


async def _snapshot(page) -> dict | None:
    return await page.evaluate(
        """find => {
          const component = eval(`(${find})`)();
          if (!component) return null;
          return {
            list: JSON.parse(JSON.stringify(component.$data.list || [])),
            pageInfo: JSON.parse(JSON.stringify(component.$data.pageInfo || {})),
            loading: Boolean(component.$data.loadingList),
            challenge: Boolean(component.$data.dialogVisibleCode)
          };
        }""",
        VUE_COMPONENT,
    )


async def _wait_snapshot(page, timeout_ms: int = 30000) -> dict | None:
    for _ in range(max(1, timeout_ms // 500)):
        snapshot = await _snapshot(page)
        if snapshot and snapshot["list"] and not snapshot["loading"]:
            return snapshot
        if (snapshot and snapshot["challenge"]) or await _has_challenge(page):
            return None
        await page.wait_for_timeout(500)
    return None


async def profile_page(page, safe_url: str) -> dict[str, str]:
    base = {"url": safe_url, "engine": "中石油专用动态浏览器", "selector": "vue:list", "profile_json": ""}
    if not is_cnpc_url(page.url):
        return {**base, "status": "需要人工验证", "note": "可视 Chrome 当前没有打开中石油招标公告页，请点击“打开此站验证”。"}
    snapshot = await _wait_snapshot(page)
    if snapshot is None:
        challenged = await _has_challenge(page)
        return {**base, "status": "需要人工验证" if challenged else "等待页面加载", "note": "检测到网站验证弹框。请手动完成网站允许的验证，停留在招标公告列表后点击“重新识别”。" if challenged else "页面脚本尚未生成公告数据，请刷新页面后重新识别。"}
    count = len(snapshot["list"])
    total = int(snapshot["pageInfo"].get("pageTotal") or count)
    return {**base, "status": "已适配（动态浏览器）", "note": f"验证会话有效，已从页面数据识别当前页 {count} 条、总计 {total} 条公告；采集时将调用页面自身的翻页与详情操作。"}


async def _change_page(page, page_number: int, previous_id) -> dict | None:
    await page.evaluate(
        """([find, number]) => {
          const component = eval(`(${find})`)();
          if (!component) throw new Error('CNPC Vue component not found');
          component.handleCurrentChange(number);
        }""",
        [VUE_COMPONENT, page_number],
    )
    for _ in range(40):
        snapshot = await _snapshot(page)
        first_id = snapshot["list"][0].get("id") if snapshot and snapshot["list"] else None
        if snapshot and not snapshot["loading"] and first_id != previous_id:
            return snapshot
        if snapshot and snapshot["challenge"]:
            return None
        await page.wait_for_timeout(500)
    return None


async def _detail_text(page, item: dict) -> tuple[str, bool]:
    detail = await page.evaluate(
        """async ([find, item]) => {
          const component = eval(`(${find})`)();
          if (!component) throw new Error('CNPC Vue component not found');
          component.goToDetails(item);
          for (let index = 0; index < 60; index += 1) {
            if (component.$data.dialogVisibleCode) return {challenge: true, text: ''};
            if (!component.$data.loading && component.$data.contentTitle) {
              const holder = document.createElement('div');
              holder.innerHTML = component.$data.content || '';
              const text = [component.$data.contentTitle, holder.textContent || holder.innerText || '',
                ...(component.$data.attachments || []).map(value => value.fileName || value.name || '')].join(' ');
              if (component.$data.isShow === false && component.clickReturn) component.clickReturn();
              return {challenge: false, text};
            }
            await new Promise(resolve => setTimeout(resolve, 250));
          }
          if (component.$data.isShow === false && component.clickReturn) component.clickReturn();
          return {challenge: false, text: ''};
        }""",
        [VUE_COMPONENT, item],
    )
    return " ".join(detail.get("text", "").split())[:30000], bool(detail.get("challenge"))


async def collect(page, site: dict, target_date: str, keywords: list[str], exclusions: list[str], result_factory, max_pages: int = 30):
    await page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
    snapshot = await _wait_snapshot(page)
    if snapshot is None:
        if await _has_challenge(page):
            return [], f"{site['name']}：会话已失效，请点击“打开此站验证”完成一次人工验证"
        return [], f"{site['name']}：页面脚本未生成公告数据"

    found, seen = [], set()
    target = date.fromisoformat(target_date)
    page_number = int(snapshot["pageInfo"].get("currentPage") or 1)
    page_size = max(1, int(snapshot["pageInfo"].get("pageSize") or len(snapshot["list"]) or 10))
    total = max(0, int(snapshot["pageInfo"].get("pageTotal") or 0))
    total_pages = min(max_pages, max(1, (total + page_size - 1) // page_size))

    while page_number <= total_pages:
        page_dates = []
        for item in snapshot["list"]:
            published_text = str(item.get("publishedTime") or item.get("publishTime") or "")
            try:
                published = date.fromisoformat(_normal_date(published_text))
            except ValueError:
                continue
            page_dates.append(published)
            if published != target:
                continue
            identity = str(item.get("id") or f"{item.get('title', '')}-{published_text}")
            if identity in seen:
                continue
            seen.add(identity)
            title = " ".join(str(item.get("title") or "").split())
            body, challenged = await _detail_text(page, item)
            if challenged:
                return found, f"{site['name']}：读取详情时验证会话失效，请重新进行人工验证"
            href = f"https://www.cnpcbidding.com/#/tenders?articleId={identity}"
            result = result_factory(site, title, href, target_date, "中石油招标公告", body or title, keywords, exclusions, identity)
            if result:
                found.append(result)
            await page.wait_for_timeout(500)
        if page_dates and min(page_dates) < target:
            break
        page_number += 1
        if page_number > total_pages:
            break
        previous_id = snapshot["list"][0].get("id") if snapshot["list"] else None
        snapshot = await _change_page(page, page_number, previous_id)
        if snapshot is None:
            return found, f"{site['name']}：翻页时出现验证或页面未响应"
        await page.wait_for_timeout(700)
    return found, ""
