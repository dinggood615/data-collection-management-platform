from __future__ import annotations

from urllib.parse import urljoin

from scrapling.fetchers import Fetcher

URL = "https://zsrm.zjenergy.com.cn/zjnycms/category/iframe.html?dates=3&categoryId=2&tenderMethod=01&page={page}"


def repair(value: str) -> str:
    try:
        return value.encode("gbk").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def collect_zjenergy(target_date: str, keywords: list[str]) -> tuple[list[dict], str]:
    found = []
    try:
        for page_no in range(1, 11):
            page = Fetcher.get(URL.format(page=page_no), timeout=30, impersonate="chrome")
            rows = page.css("ul.newslist li")
            if not rows:
                break
            dates = []
            for row in rows:
                link = row.css("a").first
                if not link:
                    continue
                title = repair((link.attrib.get("title") or link.css("h5::text").get() or "").strip())
                published = (row.css(".newsDate div::text").get() or "").strip()
                dates.append(published)
                terms = [word for word in keywords if word.casefold() in title.casefold()]
                if published == target_date and terms:
                    found.append({"source": "浙江能源招标项目公告", "title": title, "url": urljoin("https://zsrm.zjenergy.com.cn", link.attrib.get("href", "")), "published_date": published, "notice_type": "招标项目公告", "matched_terms": terms})
            if dates and all(day < target_date for day in dates):
                break
    except Exception as exc:
        return found, f"浙江能源采集失败：{type(exc).__name__}"
    return found, ""
