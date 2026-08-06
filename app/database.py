from __future__ import annotations

import os
import sqlite3
import base64
import hashlib
from contextlib import contextmanager
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken


DEFAULT_SITES = (
    ("szecp_tender", "华润守正招标公告", "服务", "动态浏览器（已验证 Chrome）"),
    ("szecp_purchase", "华润守正采购公告", "服务", "动态浏览器（已验证 Chrome）"),
    ("zjenergy", "浙江能源招标项目公告", "全部", "普通 Fetcher"),
)
BUILTIN_CUSTOM_SITES = (
    ("chdtp", "中国华电电子商务平台（四类公告）", "https://www.chdtp.com/pages/wzglS/cgxx/caigou.jsp?cgtype=4", "动态浏览器（已验证 Chrome）"),
    ("szecp_tender", "华润守正招标公告", "https://www.szecp.com.cn/first_zbgg/index.html", "动态浏览器（已验证 Chrome）"),
    ("szecp_purchase", "华润守正采购公告", "https://www.szecp.com.cn/first_cggg/index.html", "动态浏览器（已验证 Chrome）"),
    ("zjenergy", "浙江能源招标项目公告", "https://zsrm.zjenergy.com.cn/zjnycms/category/iframe.html?dates=3&categoryId=2&tenderMethod=01&page=1", "专用 Fetcher"),
)
DEFAULT_KEYWORDS = "软件开发,人力外包,信息化,数字化,劳务外包,人员技术服务,外包服务,系统开发,项目实施,协作开发,编码开发,数据处理,数据治理,信息系统建设,信息系统运维,管理系统,智能管控,智慧运营,网络安全,数字孪生,数字化平台,数字化系统,数智化,AIoT,云平台"


def db_path() -> str:
    return os.getenv("DATABASE_PATH", "/data/platform.sqlite3")


@contextmanager
def connect():
    db = sqlite3.connect(db_path(), timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sites (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
            engine TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS keywords (
            term TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tenders (
            fingerprint TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
            url TEXT NOT NULL, published_date TEXT NOT NULL, notice_type TEXT NOT NULL,
            matched_terms TEXT NOT NULL, first_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
            finished_at TEXT, target_date TEXT NOT NULL, status TEXT NOT NULL,
            matched_count INTEGER NOT NULL DEFAULT 0, new_count INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS custom_sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            engine TEXT NOT NULL DEFAULT 'Fetcher',
            status TEXT NOT NULL DEFAULT '待自动适配',
            list_selector TEXT NOT NULL DEFAULT 'a',
            date_pattern TEXT NOT NULL DEFAULT '',
            profile_note TEXT NOT NULL DEFAULT '',
            builtin_code TEXT,
            created_at TEXT NOT NULL
        );
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(custom_sites)")}
        if "builtin_code" not in columns:
            db.execute("ALTER TABLE custom_sites ADD COLUMN builtin_code TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_sites_builtin_code ON custom_sites(builtin_code) WHERE builtin_code IS NOT NULL")
        for site in DEFAULT_SITES:
            db.execute("INSERT OR IGNORE INTO sites(code,name,category,engine) VALUES(?,?,?,?)", site)
        for word in DEFAULT_KEYWORDS.split(","):
            db.execute("INSERT OR IGNORE INTO keywords(term) VALUES(?)", (word,))
        defaults = (
            ("admin_username", os.getenv("ADMIN_USERNAME", "admin")),
            ("schedule", "08:00"), ("recipient", os.getenv("SMTP_TO", "")),
            ("smtp_host", os.getenv("SMTP_HOST", "smtp.163.com")),
            ("smtp_port", os.getenv("SMTP_PORT", "465")),
            ("smtp_user", os.getenv("SMTP_USER", "")),
            ("smtp_from", os.getenv("SMTP_FROM", "")),
        )
        for key, value in defaults:
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
        migrated = db.execute("SELECT value FROM settings WHERE key='builtin_custom_sites_migrated'").fetchone()
        if not migrated:
            enabled_by_code = {row["code"]: row["enabled"] for row in db.execute("SELECT code,enabled FROM sites")}
            for code, name, url, engine in BUILTIN_CUSTOM_SITES:
                db.execute("""INSERT OR IGNORE INTO custom_sites
                    (name,url,enabled,engine,status,list_selector,profile_note,builtin_code,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (name, url, enabled_by_code.get(code, 1), engine,
                        "已适配（专用采集器）", "a", "平台内置的专用采集规则。可打开此站进行人工查看；删除仅从平台管理列表移除。", code, now_text()))
            db.execute("INSERT INTO settings(key,value) VALUES('builtin_custom_sites_migrated','1')")
        else:
            # New built-in sources may be added by platform upgrades; deleted
            # entries are remembered and therefore are not restored here.
            known = {row["builtin_code"] for row in db.execute("SELECT builtin_code FROM custom_sites WHERE builtin_code IS NOT NULL")}
            retired = {row["value"] for row in db.execute("SELECT value FROM settings WHERE key LIKE 'retired_builtin_%'")}
            for code, name, url, engine in BUILTIN_CUSTOM_SITES:
                if code not in known and code not in retired:
                    db.execute("""INSERT INTO custom_sites(name,url,enabled,engine,status,list_selector,profile_note,builtin_code,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?)""", (name,url,1,engine,"已适配（专用采集器）","a","平台内置的专用采集规则。",code,now_text()))


def _cipher() -> Fernet:
    secret = os.getenv("APP_SECRET", "development-secret-change-me").encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def setting(key: str, default: str = "", secret: bool = False) -> str:
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    value = row["value"] if row else default
    if secret and value.startswith("enc:"):
        try:
            return _cipher().decrypt(value[4:].encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return ""
    return value


def set_setting(key: str, value: str, secret: bool = False) -> None:
    if secret:
        value = "enc:" + _cipher().encrypt(value.encode("utf-8")).decode("utf-8")
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
