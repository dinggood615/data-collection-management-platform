from __future__ import annotations

import hashlib
import os
import smtplib
import ssl
import json
from urllib.request import Request, urlopen
from datetime import datetime
from email.message import EmailMessage

from .connectors.custom import collect_custom_site
from .connectors.plugins import collect_plugins
from .database import connect, setting
from .emailing import normalize_recipients
from .matching import evaluate_relevance, parse_terms


def collect_enabled_sites(target_date: str) -> tuple[int, int, str]:
    with connect() as db:
        keywords = [row["term"] for row in db.execute("SELECT term FROM keywords WHERE enabled=1")]
        custom_sites = [dict(row) for row in db.execute("SELECT * FROM custom_sites WHERE enabled=1")]
    if not keywords:
        return 0, 0, "尚未设置核心关键词，本次未访问采集站点"
    exclusions = parse_terms(setting("exclude_terms"))
    items, notices = [], []
    for site in (item for item in custom_sites if not item.get("builtin_code")):
        batch, warning = collect_custom_site(site, target_date, keywords, exclusions)
        items.extend(batch)
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
            fingerprint = hashlib.sha256(f"{item['source']}\n{item['title']}\n{item['url']}\n{item['published_date']}".encode()).hexdigest()
            cursor = db.execute("""INSERT OR IGNORE INTO tenders(fingerprint,source,title,url,published_date,notice_type,matched_terms,first_seen_at,relevance_score,relevance_level,match_reason,excerpt)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (fingerprint, item["source"], item["title"], item["url"], item["published_date"], item["notice_type"], ",".join(item["matched_terms"]), datetime.now().astimezone().isoformat(timespec="seconds"), item.get("relevance_score", 0), item.get("relevance_level", ""), item.get("match_reason", ""), item.get("excerpt", "")))
            if cursor.rowcount:
                new_items.append(item)
    recipient_value = setting("recipient")
    smtp = {"host": setting("smtp_host", os.getenv("SMTP_HOST", "smtp.163.com")), "port": setting("smtp_port", os.getenv("SMTP_PORT", "465")), "user": setting("smtp_user", os.getenv("SMTP_USER", "")), "from": setting("smtp_from", os.getenv("SMTP_FROM", "")), "auth_code": setting("smtp_auth_code", os.getenv("SMTP_AUTH_CODE", ""), secret=True)}
    if recipient_value and smtp["user"] and smtp["auth_code"]:
        try:
            recipients = normalize_recipients(recipient_value)
            send_report(recipients, target_date, len(items), new_items, notices, smtp)
        except ValueError:
            notices.append("收件邮箱配置无效，请在邮件与定时中重新保存。")
    webhook = setting("wecom_webhook", secret=True)
    if setting("wecom_push_enabled", "0") == "1" and webhook:
        try:
            send_wecom_robot_message(webhook, build_wecom_report(target_date, len(items), new_items, notices))
        except Exception as exc:
            notices.append(f"企业微信推送失败：{type(exc).__name__}")
    return len(items), len(new_items), "; ".join(notices) or "采集完成"


def send_report(recipients: list[str], target_date: str, matched: int, new_items: list[dict], notices: list[str], smtp_config: dict[str, str]) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"招标采集日报 {target_date}（新增 {len(new_items)} 条）"
    msg["From"] = smtp_config["from"] or smtp_config["user"]
    msg["To"] = ", ".join(recipients)
    lines = [f"目标日期：{target_date}", f"关键词命中：{matched} 条；新增：{len(new_items)} 条"]
    for item in new_items:
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
