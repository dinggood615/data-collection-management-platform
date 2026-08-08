from __future__ import annotations

import os
import sqlite3
import base64
import hashlib
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken




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
        defaults = (
            ("admin_username", os.getenv("ADMIN_USERNAME", "admin")),
            ("schedule", "08:00"), ("recipient", os.getenv("SMTP_TO", "")),
            ("smtp_host", os.getenv("SMTP_HOST", "smtp.163.com")),
            ("smtp_port", os.getenv("SMTP_PORT", "465")),
            ("smtp_user", os.getenv("SMTP_USER", "")),
            ("smtp_from", os.getenv("SMTP_FROM", "")),
            ("wecom_corp_id", os.getenv("WECOM_CORP_ID", "")),
            ("wecom_callback_token", os.getenv("WECOM_CALLBACK_TOKEN", "")),
            ("wecom_encoding_aes_key", os.getenv("WECOM_ENCODING_AES_KEY", "")),
            ("wecom_admin_users", os.getenv("WECOM_ADMIN_USERS", "")),
            ("wecom_public_url", os.getenv("WECOM_PUBLIC_URL", "")),
        )
        for key, value in defaults:
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))


def backup_database(retention_days: int) -> Path:
    """Create a consistent SQLite backup and prune older platform backups."""
    retention_days = max(1, min(int(retention_days), 3650))
    source_path = Path(db_path())
    backup_dir = Path(os.getenv("BACKUP_DIR", str(source_path.parent / "backups")))
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"platform-{datetime.now().astimezone():%Y%m%d-%H%M%S}.sqlite3"
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    cutoff = datetime.now().astimezone().timestamp() - retention_days * 86400
    for candidate in backup_dir.glob("platform-*.sqlite3"):
        if candidate != target and candidate.stat().st_mtime < cutoff:
            candidate.unlink(missing_ok=True)
    return target


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
