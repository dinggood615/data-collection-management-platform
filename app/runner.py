from __future__ import annotations

import hashlib
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from .connectors.szecp import collect_szecp
from .connectors.zjenergy import collect_zjenergy
from .database import connect, setting


def collect_enabled_sites(target_date: str) -> tuple[int, int, str]:
    with connect() as db:
        enabled = {row["code"] for row in db.execute("SELECT code FROM sites WHERE enabled=1")}
        keywords = [row["term"] for row in db.execute("SELECT term FROM keywords WHERE enabled=1")]
    items, notices = [], []
    if "szecp_tender" in enabled or "szecp_purchase" in enabled:
        batch, warning = collect_szecp(target_date, enabled, keywords)
        items.extend(batch)
        if warning:
            notices.append(warning)
    if "zjenergy" in enabled:
        batch, warning = collect_zjenergy(target_date, keywords)
        items.extend(batch)
        if warning:
            notices.append(warning)
    new_items = []
    with connect() as db:
        for item in items:
            fingerprint = hashlib.sha256(f"{item['source']}\n{item['title']}\n{item['url']}\n{item['published_date']}".encode()).hexdigest()
            cursor = db.execute("""INSERT OR IGNORE INTO tenders(fingerprint,source,title,url,published_date,notice_type,matched_terms,first_seen_at)
                VALUES(?,?,?,?,?,?,?,?)""", (fingerprint, item["source"], item["title"], item["url"], item["published_date"], item["notice_type"], ",".join(item["matched_terms"]), datetime.now().astimezone().isoformat(timespec="seconds")))
            if cursor.rowcount:
                new_items.append(item)
    recipient = setting("recipient")
    smtp = {"host": setting("smtp_host", os.getenv("SMTP_HOST", "smtp.163.com")), "port": setting("smtp_port", os.getenv("SMTP_PORT", "465")), "user": setting("smtp_user", os.getenv("SMTP_USER", "")), "from": setting("smtp_from", os.getenv("SMTP_FROM", "")), "auth_code": setting("smtp_auth_code", os.getenv("SMTP_AUTH_CODE", ""), secret=True)}
    if recipient and smtp["user"] and smtp["auth_code"]:
        send_report(recipient, target_date, len(items), new_items, notices, smtp)
    return len(items), len(new_items), "; ".join(notices) or "采集完成"


def send_report(recipient: str, target_date: str, matched: int, new_items: list[dict], notices: list[str], smtp_config: dict[str, str]) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"招标采集日报 {target_date}（新增 {len(new_items)} 条）"
    msg["From"] = smtp_config["from"] or smtp_config["user"]
    msg["To"] = recipient
    lines = [f"目标日期：{target_date}", f"关键词命中：{matched} 条；新增：{len(new_items)} 条"]
    for item in new_items:
        lines.extend(("", item["title"], f"来源：{item['source']}；匹配：{','.join(item['matched_terms'])}", item["url"]))
    if notices:
        lines.extend(("", "提示：", *notices))
    msg.set_content("\n".join(lines))
    with smtplib.SMTP_SSL(smtp_config["host"], int(smtp_config["port"]), context=ssl.create_default_context()) as smtp:
        smtp.login(smtp_config["user"], smtp_config["auth_code"])
        smtp.send_message(msg)
