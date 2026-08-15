from __future__ import annotations

import hashlib
import os
import smtplib
import ssl
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from datetime import datetime
from email.message import EmailMessage

from .connectors.custom import auto_reprofile_site, collect_custom_site
from .connectors.plugins import collect_plugins
from .database import connect, setting
from .emailing import normalize_recipients
from .matching import evaluate_relevance, parse_terms


COLLECTABLE_CUSTOM_STATUSES = {"已适配（静态列表）", "已适配（动态浏览器）", "已适配（公开数据接口）"}


def _collect_custom_with_recovery(site: dict, target_date: str, keywords: list[str], exclusions: list[str],
                                  recovery_attempted: set[int] | None = None) -> tuple[list[dict], str]:
    """Collect once, then rebuild and retry a broken custom-site profile."""
    batch, warning = collect_custom_site(site, target_date, keywords, exclusions)
    if not warning:
        if site.get("failure_count"):
            with connect() as db:
                db.execute("UPDATE custom_sites SET failure_count=0,last_failure_at='' WHERE id=?", (site["id"],))
        return batch, ""
    if recovery_attempted is not None and site["id"] in recovery_attempted:
        return batch, f"{warning}；本轮已尝试自动恢复，不再重复等待"
    if recovery_attempted is not None:
        recovery_attempted.add(site["id"])
    failed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as db:
        db.execute("UPDATE custom_sites SET failure_count=failure_count+1,last_failure_at=? WHERE id=?",
                   (failed_at, site["id"]))
    try:
        profile = auto_reprofile_site(site["url"])
    except Exception as exc:
        return batch, f"{warning}；自动重新适配失败：{type(exc).__name__}，下次采集将继续尝试"
    if profile["status"] not in COLLECTABLE_CUSTOM_STATUSES:
        with connect() as db:
            db.execute("UPDATE custom_sites SET profile_note=? WHERE id=?",
                       (f"自动恢复尚未找到稳定规则：{profile['note']}", site["id"]))
        return batch, f"{warning}；自动恢复尚未完成，下次采集将继续尝试"
    recovered = {**site, "engine": profile["engine"], "status": profile["status"],
                 "list_selector": profile["selector"], "profile_note": profile["note"],
                 "profile_json": profile.get("profile_json", ""), "failure_count": 0}
    with connect() as db:
        db.execute("""UPDATE custom_sites SET enabled=1,engine=?,status=?,list_selector=?,profile_note=?,profile_json=?,
                   failure_count=0,last_failure_at='',last_adapted_at=? WHERE id=?""",
                   (recovered["engine"], recovered["status"], recovered["list_selector"], recovered["profile_note"],
                    recovered["profile_json"], failed_at, site["id"]))
    retry_batch, retry_warning = collect_custom_site(recovered, target_date, keywords, exclusions)
    if retry_warning:
        return batch, f"{warning}；规则已自动更新，但当轮重试仍失败：{retry_warning}"
    return retry_batch, f"{site['name']}：失效规则已自动更新并在当轮恢复采集"


def _collect_custom_timed(site: dict, target_date: str, keywords: list[str], exclusions: list[str],
                          recovery_attempted: set[int]) -> tuple[list[dict], str]:
    started = time.monotonic()
    try:
        return _collect_custom_with_recovery(site, target_date, keywords, exclusions, recovery_attempted)
    finally:
        duration_ms = round((time.monotonic() - started) * 1000)
        with connect() as db:
            db.execute("UPDATE custom_sites SET last_duration_ms=?,last_run_at=? WHERE id=?",
                       (duration_ms, datetime.now().astimezone().isoformat(timespec="seconds"), site["id"]))


def collect_enabled_sites(target_date: str, send_email: bool = True, *, historical: bool = False,
                          recovery_attempted: set[int] | None = None) -> tuple[int, int, str]:
    with connect() as db:
        keywords = [row["term"] for row in db.execute("SELECT term FROM keywords WHERE enabled=1")]
        custom_sites = [dict(row) for row in db.execute("SELECT * FROM custom_sites WHERE enabled=1")]
    if not keywords:
        return 0, 0, "尚未设置核心关键词，本次未访问采集站点"
    exclusions = parse_terms(setting("exclude_terms"))
    items, notices = [], []
    recovery_attempted = recovery_attempted if recovery_attempted is not None else set()
    custom = [item for item in custom_sites if not item.get("builtin_code")]
    if historical:
        custom = [item for item in custom if item["status"] == "已适配（公开数据接口）"]
    parallel = [item for item in custom if item["status"] != "已适配（动态浏览器）"]
    browser = [item for item in custom if item["status"] == "已适配（动态浏览器）"]
    workers = max(1, min(int(os.getenv("CUSTOM_SITE_WORKERS", "3")), 4))
    results: list[tuple[dict, list[dict], str]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="custom-site") as pool:
        futures = {pool.submit(_collect_custom_timed, site, target_date, keywords, exclusions, recovery_attempted): site
                   for site in parallel}
        # Keep browser work single-file, but overlap it with independent HTTP
        # collectors so the VPS spends less time idle on network responses.
        for site in browser:
            batch, warning = _collect_custom_timed(site, target_date, keywords, exclusions, recovery_attempted)
            results.append((site, batch, warning))
        for future in as_completed(futures):
            site = futures[future]
            try:
                batch, warning = future.result()
            except Exception as exc:
                batch, warning = [], f"{site['name']}：并发采集失败：{type(exc).__name__}"
            results.append((site, batch, warning))
    for site, batch, warning in results:
        items.extend(batch)
        with connect() as db:
            db.execute("UPDATE custom_sites SET last_item_count=? WHERE id=?", (len(batch), site["id"]))
        if warning:
            notices.append(warning)
    enabled_codes = {site["builtin_code"] for site in custom_sites if site.get("builtin_code")}
    plugin_items, plugin_notices = collect_plugins(target_date, enabled_codes, keywords, exclusions)
    items.extend(plugin_items)
    notices.extend(plugin_notices)
    ranked_items = []
    for item in items:
        if "relevance_score" not in item:
            relevance = evaluate_relevance(item["title"], "", keywords, exclusions)
            if relevance.score < 20:
                continue
            item.update(relevance_score=relevance.score, relevance_level=relevance.level,
                        match_reason="；".join(relevance.reasons), excerpt="")
            item["matched_terms"] = relevance.terms
        ranked_items.append(item)
    items = ranked_items
    new_items = []
    with connect() as db:
        for item in items:
            source_item_id = item.get("source_item_id", "")
            identity = source_item_id or f"{item['title']}\n{item['url']}\n{item['published_date']}"
            fingerprint = hashlib.sha256(f"{item['source']}\n{identity}".encode()).hexdigest()
            revision_hash = hashlib.sha256(f"{item['title']}\n{item.get('excerpt', '')}\n{item['published_date']}".encode()).hexdigest()
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            existed = db.execute("SELECT 1 FROM tenders WHERE fingerprint=?", (fingerprint,)).fetchone() is not None
            db.execute("""INSERT INTO tenders(fingerprint,source,title,url,published_date,notice_type,matched_terms,first_seen_at,relevance_score,relevance_level,match_reason,excerpt,source_item_id,last_seen_at,revision_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(fingerprint) DO UPDATE SET title=excluded.title,url=excluded.url,published_date=excluded.published_date,
                notice_type=excluded.notice_type,matched_terms=excluded.matched_terms,relevance_score=excluded.relevance_score,
                relevance_level=excluded.relevance_level,match_reason=excluded.match_reason,excerpt=excluded.excerpt,
                source_item_id=excluded.source_item_id,last_seen_at=excluded.last_seen_at,revision_hash=excluded.revision_hash""",
                (fingerprint, item["source"], item["title"], item["url"], item["published_date"], item["notice_type"],
                 ",".join(item["matched_terms"]), now, item.get("relevance_score", 0), item.get("relevance_level", ""),
                 item.get("match_reason", ""), item.get("excerpt", ""), source_item_id, now, revision_hash))
            if not existed:
                new_items.append(item)
        report_items = [dict(row) for row in db.execute(
            "SELECT * FROM tenders WHERE published_date=? ORDER BY relevance_score DESC, first_seen_at DESC",
            (target_date,),
        ).fetchall()]
    for item in report_items:
        item["matched_terms"] = [term for term in item["matched_terms"].split(",") if term]
    recipient_value = setting("recipient")
    smtp = {"host": setting("smtp_host", os.getenv("SMTP_HOST", "smtp.163.com")), "port": setting("smtp_port", os.getenv("SMTP_PORT", "465")), "user": setting("smtp_user", os.getenv("SMTP_USER", "")), "from": setting("smtp_from", os.getenv("SMTP_FROM", "")), "auth_code": setting("smtp_auth_code", os.getenv("SMTP_AUTH_CODE", ""), secret=True)}
    if send_email and recipient_value and smtp["user"] and smtp["auth_code"]:
        try:
            recipients = normalize_recipients(recipient_value)
            send_report(recipients, target_date, report_items, notices, smtp)
        except ValueError:
            notices.append("收件邮箱配置无效，请在邮件与定时中重新保存。")
    return len(items), len(new_items), "; ".join(notices) or "采集完成"


def send_report(recipients: list[str], target_date: str, report_items: list[dict], notices: list[str], smtp_config: dict[str, str]) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"招标采集日报 {target_date}（共 {len(report_items)} 条）"
    msg["From"] = smtp_config["from"] or smtp_config["user"]
    msg["To"] = ", ".join(recipients)
    lines = [f"目标日期：{target_date}", f"前一自然日命中结果：{len(report_items)} 条"]
    for item in report_items:
        lines.extend(("", f"[{item.get('relevance_level', '相关')} {item.get('relevance_score', 0)}分] {item['title']}", f"来源：{item['source']}；匹配：{','.join(item['matched_terms'])}", item.get("match_reason", ""), item["url"]))
    if notices:
        lines.extend(("", "提示：", *notices))
    msg.set_content("\n".join(lines))
    with smtplib.SMTP_SSL(smtp_config["host"], int(smtp_config["port"]), context=ssl.create_default_context()) as smtp:
        smtp.login(smtp_config["user"], smtp_config["auth_code"])
        smtp.send_message(msg, to_addrs=recipients)


def build_wecom_report(target_date: str, matched: int, new_items: list[dict], notices: list[str]) -> str:
    lines = [f"数据采集日报 {target_date}", f"关键词命中：{matched} 条｜新增：{len(new_items)} 条"]
    for item in new_items[:10]:
        lines.extend(("", f"[{item.get('relevance_level', '相关')} {item.get('relevance_score', 0)}分] {item['title'][:120]}", item.get("match_reason", ""), item["url"]))
    if len(new_items) > 10:
        lines.append(f"另有 {len(new_items) - 10} 条结果，请登录平台查看。")
    if notices:
        lines.extend(("", "提示：", *notices[:3]))
    return "\n".join(lines)


def send_wecom_robot_message(webhook: str, text: str) -> None:
    payload = json.dumps({"msgtype": "text", "text": {"content": text[:4000]}}, ensure_ascii=False).encode("utf-8")
    request = Request(webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    if body.get("errcode") != 0:
        raise RuntimeError(f"wechat error {body.get('errcode')}")
