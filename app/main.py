from __future__ import annotations

import base64
import asyncio
import os
import secrets
from datetime import date, timedelta
from urllib.parse import urlparse

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .database import backup_database, connect, export_migration_bundle, import_migration_bundle, init_db, now_text, set_setting, setting
from .connectors.custom import profile_site, profile_site_from_manual_browser, validate_public_url, validate_site_name

app = FastAPI(title="数据采集管理平台")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
SESSION_COOKIE = "tender_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
COLLECTABLE_CUSTOM_STATUSES = {"已适配（静态列表）", "已适配（动态浏览器）", "已适配（专用采集器）"}


def session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(os.getenv("APP_SECRET", "development-secret-change-me"), salt="tender-platform-session")


def has_valid_session(request: Request, username: str) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    try:
        return secrets.compare_digest(session_serializer().loads(token, max_age=SESSION_TTL_SECONDS), username)
    except (BadSignature, TypeError):
        return False


@app.middleware("http")
async def require_admin(request: Request, call_next):
    """Keep the dashboard private even when Docker publishes port 8000."""
    if request.url.path.startswith("/static/") or request.url.path in {"/healthz", "/wecom/callback"}:
        return await call_next(request)
    configured = setting("admin_password", os.getenv("ADMIN_PASSWORD", "admin"), secret=True)
    username = setting("admin_username", os.getenv("ADMIN_USERNAME", "admin"))
    auth = request.headers.get("authorization", "")
    try:
        scheme, token = auth.split(" ", 1)
        supplied_username, password = base64.b64decode(token).decode().split(":", 1)
    except Exception:
        scheme, supplied_username, password = "", "", ""
    basic_ok = bool(configured and scheme.lower() == "basic" and supplied_username == username and secrets.compare_digest(password, configured))
    if not basic_ok and not has_valid_session(request, username):
        return PlainTextResponse("需要管理员登录", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Tender Platform"'})
    response = await call_next(request)
    if basic_ok:
        response.set_cookie(SESSION_COOKIE, session_serializer().dumps(username), max_age=SESSION_TTL_SECONDS, httponly=True, secure=True, samesite="strict", path="/")
    return response


def dashboard_context() -> dict:
    with connect() as db:
        keywords = db.execute("SELECT * FROM keywords WHERE enabled=1 ORDER BY term").fetchall()
        runs = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 8").fetchall()
        results = db.execute("SELECT * FROM tenders ORDER BY first_seen_at DESC LIMIT 20").fetchall()
        custom_sites = [dict(row) for row in db.execute("SELECT * FROM custom_sites ORDER BY id DESC")]
        total_results = db.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
        successful_runs = db.execute("SELECT COUNT(*) FROM runs WHERE status='success'").fetchone()[0]
    for site in custom_sites:
        try:
            validate_site_name(site["name"])
            validate_public_url(site["url"])
            site["entry_invalid"] = False
        except ValueError:
            site["entry_invalid"] = True
            site["status"] = "网址需修正"
            site["engine"] = "尚未识别"
            site["profile_note"] = "该记录的名称或网址格式不完整，尚未发起采集。请直接在下方修正后保存。"
        if site["entry_invalid"]:
            site["next_step"] = "直接修改网站名称和公告列表网址，然后点击“保存并识别”。无需人工验证。"
        elif site["status"] in COLLECTABLE_CUSTOM_STATUSES:
            site["next_step"] = "已可自动采集。确认启用后，点击“立即采集”可先进行一次人工检查。"
        elif "JavaScript" in site["profile_note"] or "会话" in site["profile_note"]:
            site["next_step"] = "点击“打开此站验证”，在可视 Chrome 中完成网站允许的登录或验证后，回到这里点击“完成验证并自动适配”。"
        else:
            site["next_step"] = "请确认填写的是公告列表页而非首页、详情页或搜索页；确认公开可访问后点击“重新识别”。"
    return {"keywords": keywords, "runs": runs, "results": results, "custom_sites": custom_sites,
            "enabled_site_count": sum(1 for site in custom_sites if site["enabled"]),
            "total_result_count": total_results, "successful_run_count": successful_runs,
            "schedule": setting("schedule"), "recipient": setting("recipient"),
            "smtp_host": setting("smtp_host"), "smtp_port": setting("smtp_port"),
            "smtp_user": setting("smtp_user"), "smtp_from": setting("smtp_from"),
            "smtp_configured": bool(setting("smtp_auth_code", secret=True)), "admin_username": setting("admin_username", "admin"),
            "custom_site_message": setting("custom_site_message"),
            "backup_schedule": setting("backup_schedule", "02:20"),
            "backup_retention_days": setting("backup_retention_days", "14"),
            "last_backup": setting("last_backup"), "backup_message": setting("backup_message"),
            "migration_message": setting("migration_message"),
            "wecom_message": setting("wecom_message"),
            "wecom_push_message": setting("wecom_push_message"),
            "wecom_webhook_configured": bool(setting("wecom_webhook", secret=True)),
            "wecom_push_enabled": setting("wecom_push_enabled", "0") == "1",
            "wecom_callback_url": _wecom_callback_url(),
            "wecom_callback_token_value": setting("wecom_callback_token", secret=True),
            "wecom_encoding_aes_key_value": setting("wecom_encoding_aes_key", secret=True)}


@app.on_event("startup")
def startup() -> None:
    init_db()
    scheduler.start()
    reschedule()


@app.on_event("shutdown")
def shutdown() -> None:
    scheduler.shutdown(wait=False)


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", dashboard_context())


@app.get("/healthz")
def healthz():
    """Minimal unauthenticated liveness/readiness check for reverse proxies."""
    try:
        with connect() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except Exception:
        return JSONResponse({"status": "error"}, status_code=503)


@app.get("/_internal/auth-check", status_code=204)
def auth_check():
    """Nginx auth_request target for the embedded manual-verification page."""
    return None


@app.post("/custom-sites")
def add_custom_site(name: str = Form(...), url: str = Form(...)):
    try:
        safe_name = validate_site_name(name)
        safe_url = validate_public_url(url)
        profile = profile_site(safe_url)
        with connect() as db:
            enabled = 1 if profile["status"] in COLLECTABLE_CUSTOM_STATUSES else 0
            db.execute("""INSERT INTO custom_sites(name,url,enabled,engine,status,list_selector,profile_note,created_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,engine=excluded.engine,status=excluded.status,list_selector=excluded.list_selector,profile_note=excluded.profile_note""", (safe_name, profile["url"], enabled, profile["engine"], profile["status"], profile["selector"], profile["note"], now_text()))
        set_setting("custom_site_message", f"{safe_name}：{profile['status']}。请查看下方下一步指引。")
    except ValueError as exc:
        set_setting("custom_site_message", str(exc))
    except Exception as exc:
        set_setting("custom_site_message", f"自动适配失败：{type(exc).__name__}")
    return RedirectResponse("/", 303)


@app.post("/custom-sites/{site_id}/update")
def update_custom_site(site_id: int, name: str = Form(...), url: str = Form(...)):
    try:
        safe_name = validate_site_name(name)
        safe_url = validate_public_url(url)
        profile = profile_site(safe_url)
        enabled = 1 if profile["status"] in COLLECTABLE_CUSTOM_STATUSES else 0
        with connect() as db:
            exists = db.execute("SELECT id,builtin_code FROM custom_sites WHERE id=?", (site_id,)).fetchone()
            if not exists:
                raise ValueError("未找到该站点")
            if exists["builtin_code"]:
                raise ValueError("内置站点的地址由专用采集规则管理；可使用下方操作进行验证、重新识别、启用或删除。")
            db.execute("UPDATE custom_sites SET name=?,url=?,enabled=?,engine=?,status=?,list_selector=?,profile_note=? WHERE id=?", (safe_name, profile["url"], enabled, profile["engine"], profile["status"], profile["selector"], profile["note"], site_id))
        set_setting("custom_site_message", f"{safe_name}：已保存并完成自动识别。")
    except ValueError as exc:
        set_setting("custom_site_message", str(exc))
    except Exception as exc:
        set_setting("custom_site_message", f"保存并识别失败：{type(exc).__name__}")
    return RedirectResponse("/", 303)


@app.post("/custom-sites/{site_id}/toggle")
def toggle_custom_site(site_id: int):
    message = ""
    with connect() as db:
        site = db.execute("SELECT name,status FROM custom_sites WHERE id=?", (site_id,)).fetchone()
        if not site:
            message = "未找到该站点"
        elif site["status"] not in COLLECTABLE_CUSTOM_STATUSES:
            message = f"{site['name']} 尚未完成自动适配，暂不能启用。请按“下一步指引”完成后重新识别。"
        else:
            db.execute("UPDATE custom_sites SET enabled=1-enabled WHERE id=?", (site_id,))
            message = f"{site['name']}：启用状态已更新"
    set_setting("custom_site_message", message)
    return RedirectResponse("/", 303)


@app.post("/custom-sites/{site_id}/profile")
def reprofile_custom_site(site_id: int):
    with connect() as db:
        site = db.execute("SELECT * FROM custom_sites WHERE id=?", (site_id,)).fetchone()
    if not site:
        set_setting("custom_site_message", "未找到该站点")
        return RedirectResponse("/", 303)
    try:
        if site["builtin_code"]:
            with connect() as db:
                db.execute("UPDATE custom_sites SET profile_note=? WHERE id=?", ("已检查并保留平台内置的专用采集规则；如网站需要人工操作，请先点击“打开此站验证”。", site_id))
            set_setting("custom_site_message", f"{site['name']}：已保留专用采集规则。")
            return RedirectResponse("/", 303)
        # Prefer the page the user has just verified in visible Chrome.  Static
        # profiling remains a safe fallback when no matching browser tab exists.
        profile = asyncio.run(profile_site_from_manual_browser(site["url"]))
        if profile is None:
            profile = profile_site(site["url"])
        with connect() as db:
            enabled = 1 if profile["status"] in COLLECTABLE_CUSTOM_STATUSES else 0
            db.execute("UPDATE custom_sites SET enabled=?,engine=?,status=?,list_selector=?,profile_note=? WHERE id=?", (enabled, profile["engine"], profile["status"], profile["selector"], profile["note"], site_id))
        if profile["status"] == "已适配（动态浏览器）":
            set_setting("custom_site_message", f"{site['name']}：已根据人工验证后的 Chrome 页面完成动态适配，并已启用。")
        else:
            set_setting("custom_site_message", f"{site['name']}：自动适配已更新")
    except Exception as exc:
        set_setting("custom_site_message", f"自动适配失败：{type(exc).__name__}")
    return RedirectResponse("/", 303)


async def _open_manual_browser(url: str) -> None:
    """Navigate the already-visible, user-controlled Chrome to a chosen site."""
    from playwright.async_api import async_playwright

    cdp_url = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)


@app.post("/custom-sites/{site_id}/manual-verify")
def open_site_for_manual_verification(site_id: int):
    with connect() as db:
        site = db.execute("SELECT name,url FROM custom_sites WHERE id=?", (site_id,)).fetchone()
    if not site:
        set_setting("custom_site_message", "未找到该站点")
        return RedirectResponse("/", 303)
    try:
        target_url = validate_public_url(site["url"])
        asyncio.run(_open_manual_browser(target_url))
        set_setting("custom_site_message", f"{site['name']} 已在可视 Chrome 中打开；完成网站允许的操作后，回到平台点击“完成验证并自动适配”。")
        return RedirectResponse("/manual-verify/vnc.html?autoconnect=1&path=manual-verify/websockify", 303)
    except Exception as exc:
        set_setting("custom_site_message", f"无法打开可视 Chrome：{type(exc).__name__}")
        return RedirectResponse("/", 303)


@app.post("/custom-sites/{site_id}/delete")
def delete_custom_site(site_id: int):
    message = ""
    with connect() as db:
        site = db.execute("SELECT name,builtin_code FROM custom_sites WHERE id=?", (site_id,)).fetchone()
        if site:
            db.execute("DELETE FROM custom_sites WHERE id=?", (site_id,))
            if site["builtin_code"]:
                db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (f"retired_builtin_{site['builtin_code']}", site["builtin_code"]))
            message = f"已删除自定义站点：{site['name']}"
        else:
            message = "未找到该站点"
    set_setting("custom_site_message", message)
    return RedirectResponse("/", 303)


@app.post("/keywords")
def add_keywords(terms: str = Form(...)):
    words = {item.strip() for item in terms.replace("，", ",").replace("\n", ",").split(",") if item.strip()}
    with connect() as db:
        for word in words:
            db.execute("INSERT OR IGNORE INTO keywords(term) VALUES(?)", (word,))
    return RedirectResponse("/", 303)


@app.post("/keywords/{term}/toggle")
def toggle_keyword(term: str):
    with connect() as db:
        db.execute("UPDATE keywords SET enabled=1-enabled WHERE term=?", (term,))
    return RedirectResponse("/", 303)


@app.post("/settings")
def save_settings(schedule: str = Form(...), recipient: str = Form(...), smtp_host: str = Form(...), smtp_port: str = Form(...), smtp_user: str = Form(...), smtp_from: str = Form(...), smtp_auth_code: str = Form("")):
    set_setting("schedule", schedule)
    set_setting("recipient", recipient.strip())
    set_setting("smtp_host", smtp_host.strip())
    set_setting("smtp_port", smtp_port.strip())
    set_setting("smtp_user", smtp_user.strip())
    set_setting("smtp_from", smtp_from.strip())
    if smtp_auth_code.strip():
        set_setting("smtp_auth_code", smtp_auth_code.strip(), secret=True)
    reschedule()
    return RedirectResponse("/", 303)


@app.post("/backup-settings")
def save_backup_settings(backup_schedule: str = Form(...), backup_retention_days: int = Form(...)):
    if not 1 <= backup_retention_days <= 3650:
        return RedirectResponse("/", 303)
    try:
        hour, minute = backup_schedule.split(":")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ValueError
    except ValueError:
        return RedirectResponse("/", 303)
    set_setting("backup_schedule", backup_schedule)
    set_setting("backup_retention_days", str(backup_retention_days))
    reschedule()
    return RedirectResponse("/", 303)


@app.post("/admin-credentials")
def save_admin_credentials(admin_username: str = Form(...), new_password: str = Form(...), confirm_password: str = Form(...)):
    if len(admin_username.strip()) < 3 or len(new_password) < 8 or new_password != confirm_password:
        return RedirectResponse("/", 303)
    set_setting("admin_username", admin_username.strip())
    set_setting("admin_password", new_password, secret=True)
    return RedirectResponse("/", 303)


@app.get("/migration/export")
def download_migration_bundle():
    payload = export_migration_bundle()
    filename = f"data-collection-platform-{date.today().isoformat()}.zip"
    return StreamingResponse(
        iter([payload]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@app.post("/migration/import")
async def upload_migration_bundle(bundle: UploadFile = File(...), confirm_restore: str = Form("")):
    if confirm_restore != "yes":
        set_setting("migration_message", "请勾选确认后再导入。")
        return RedirectResponse("/", 303)
    try:
        payload = await bundle.read(100 * 1024 * 1024 + 1)
        rollback = import_migration_bundle(payload)
        set_setting("migration_message", f"导入成功，旧站点、关键词、配置和历史结果已恢复。回滚备份：{rollback.name}")
        reschedule()
    except (ValueError, OSError) as exc:
        set_setting("migration_message", f"导入失败：{exc}")
    except Exception as exc:
        set_setting("migration_message", f"导入失败：{type(exc).__name__}")
    return RedirectResponse("/", 303)


def _wecom_callback_url() -> str:
    public_url = setting("wecom_public_url", "").rstrip("/")
    return f"{public_url}/wecom/callback" if public_url else ""


def _validate_wecom_webhook(value: str) -> str:
    """Accept only Enterprise WeChat robot webhooks, never arbitrary outbound URLs."""
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.netloc != "qyapi.weixin.qq.com" or not parsed.path.startswith("/cgi-bin/webhook/send"):
        raise ValueError("请输入企业微信机器人 Webhook 地址。")
    if not parsed.query:
        raise ValueError("Webhook 地址缺少 key 参数。")
    return value.strip()


@app.post("/wecom/push-settings")
def save_wecom_push_settings(webhook: str = Form(""), enabled: str = Form("0")):
    try:
        if webhook.strip():
            set_setting("wecom_webhook", _validate_wecom_webhook(webhook), secret=True)
        set_setting("wecom_push_enabled", "1" if enabled == "1" else "0")
        set_setting("wecom_push_message", "企业微信推送设置已保存。")
    except ValueError as exc:
        set_setting("wecom_push_message", str(exc))
    return RedirectResponse("/", 303)


@app.post("/wecom/push-test")
def test_wecom_push():
    from .runner import send_wecom_robot_message

    webhook = setting("wecom_webhook", secret=True)
    if not webhook:
        set_setting("wecom_push_message", "请先填写并保存机器人 Webhook。")
    else:
        try:
            send_wecom_robot_message(webhook, "数据采集管理平台测试消息\n企业微信推送已连接。")
            set_setting("wecom_push_message", "测试消息已发送，请查看企业微信群。")
        except Exception as exc:
            set_setting("wecom_push_message", f"测试发送失败：{type(exc).__name__}")
    return RedirectResponse("/", 303)


def _new_wecom_aes_key() -> str:
    """Enterprise WeChat requires a 43-character base64 key without padding."""
    return base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def _wecom_crypto():
    from wechatpy.enterprise.crypto import WeChatCrypto

    corp_id = setting("wecom_corp_id", secret=True)
    token = setting("wecom_callback_token", secret=True)
    aes_key = setting("wecom_encoding_aes_key", secret=True)
    if not all((corp_id, token, aes_key)):
        raise RuntimeError("企业微信配置不完整")
    return WeChatCrypto(token, aes_key, corp_id)


def _wecom_sender_allowed(user_id: str) -> bool:
    allowed = {item.strip() for item in setting("wecom_admin_users", "").replace("，", ",").split(",") if item.strip()}
    return bool(allowed) and user_id in allowed


def run_assistant_command(message: str) -> str:
    """A small allow-list of safe platform operations for chat assistants."""
    text = message.strip().lower()
    if any(word in text for word in ("状态", "健康", "status", "health")):
        with connect() as db:
            enabled = db.execute("SELECT COUNT(*) FROM custom_sites WHERE enabled=1").fetchone()[0]
            latest = db.execute("SELECT status,started_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        recent = f"最近任务：{latest['status']}（{latest['started_at']}）" if latest else "尚无运行记录"
        return f"平台在线，已启用采集站点 {enabled} 个。{recent}。"
    if any(word in text for word in ("采集", "抓取", "collect")):
        scheduler.add_job(run_collection, id="manual-run", replace_existing=True)
        return "已提交一次采集任务，请稍后在平台“最近运行”查看结果。"
    if any(word in text for word in ("最新", "结果", "latest")):
        with connect() as db:
            rows = db.execute("SELECT title FROM tenders ORDER BY first_seen_at DESC LIMIT 3").fetchall()
        return "最近采集结果：" + ("；".join(row["title"][:70] for row in rows) if rows else "暂无入库结果。")
    if any(word in text for word in ("备份", "backup")):
        target = backup_database(int(setting("backup_retention_days", "14")))
        return f"数据库备份已创建：{target.name}。"
    return "支持的指令：状态、立即采集、最新结果、备份。"


@app.post("/wecom/quick-settings")
def save_wecom_quick_settings(corp_id: str = Form(""), public_url: str = Form(""), admin_users: str = Form("")):
    if corp_id.strip():
        set_setting("wecom_corp_id", corp_id.strip(), secret=True)
    if public_url.strip():
        parsed = urlparse(public_url.strip())
        if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
            set_setting("wecom_message", "请输入有效的 HTTPS 公网根地址，例如 https://tender.example.com。")
            return RedirectResponse("/", 303)
        set_setting("wecom_public_url", public_url.strip().rstrip("/"))
    if admin_users.strip():
        users = [item.strip() for item in admin_users.replace("，", ",").split(",") if item.strip()]
        set_setting("wecom_admin_users", ",".join(dict.fromkeys(users)))
    if not setting("wecom_callback_token", secret=True):
        set_setting("wecom_callback_token", secrets.token_hex(16), secret=True)
    if not setting("wecom_encoding_aes_key", secret=True):
        set_setting("wecom_encoding_aes_key", _new_wecom_aes_key(), secret=True)
    set_setting("wecom_message", "已生成企业微信配置。请复制下方三项到企业微信自建应用的“接收消息”页面并保存验证。")
    return RedirectResponse("/", 303)


@app.post("/wecom/check")
def check_wecom_setup():
    missing = []
    if not setting("wecom_corp_id", secret=True): missing.append("CorpID")
    if not setting("wecom_public_url", ""): missing.append("HTTPS 公网地址")
    if not setting("wecom_admin_users", ""): missing.append("管理员 UserID")
    if not setting("wecom_callback_token", secret=True): missing.append("Token")
    if not setting("wecom_encoding_aes_key", secret=True): missing.append("EncodingAESKey")
    if missing:
        set_setting("wecom_message", "配置尚不完整：" + "、".join(missing))
    else:
        try:
            _wecom_crypto()
            set_setting("wecom_message", "配置已就绪。请在企业微信后台粘贴下方三项并保存验证，然后向应用发送“状态”。")
        except Exception:
            set_setting("wecom_message", "本地参数校验失败，请重新保存企业微信助手配置。")
    return RedirectResponse("/", 303)


@app.get("/wecom/callback")
def verify_wecom_callback(request: Request):
    try:
        crypto = _wecom_crypto()
        args = request.query_params
        echo = crypto.check_signature(args["msg_signature"], args["timestamp"], args["nonce"], args["echostr"])
        return PlainTextResponse(echo)
    except Exception:
        return PlainTextResponse("invalid callback", 403)


@app.post("/wecom/callback")
async def receive_wecom_message(request: Request):
    try:
        from wechatpy.enterprise import create_reply, parse_message

        crypto = _wecom_crypto()
        args = request.query_params
        decrypted = crypto.decrypt_message(await request.body(), args["msg_signature"], args["timestamp"], args["nonce"])
        message = parse_message(decrypted)
        if getattr(message, "type", "") != "text":
            reply_text = "仅支持文本指令：状态、立即采集、最新结果、备份。"
        elif not _wecom_sender_allowed(message.source):
            reply_text = "当前企业微信账号未获授权，请联系平台管理员。"
        else:
            reply_text = run_assistant_command(message.content)
        encrypted = crypto.encrypt_message(create_reply(reply_text, message).render(), args["nonce"], args["timestamp"])
        return Response(encrypted, media_type="application/xml")
    except Exception:
        return PlainTextResponse("invalid callback", 403)


def run_collection() -> None:
    # Connector execution is deliberately isolated here. The production collector
    # uses only enabled site adapters and never attempts CAPTCHA/anti-bot bypass.
    target = (date.today() - timedelta(days=1)).isoformat()
    with connect() as db:
        cursor = db.execute("INSERT INTO runs(started_at,target_date,status,message) VALUES(?,?,?,?)", (now_text(), target, "running", "正在采集已启用站点"))
        run_id = cursor.lastrowid
    try:
        from .runner import collect_enabled_sites
        matched, new_count, message = collect_enabled_sites(target)
        status = "success"
    except Exception as exc:
        matched, new_count, status, message = 0, 0, "failed", f"{type(exc).__name__}: {exc}"
    with connect() as db:
        db.execute("UPDATE runs SET finished_at=?,status=?,matched_count=?,new_count=?,message=? WHERE id=?", (now_text(), status, matched, new_count, message, run_id))


@app.post("/run")
def run_now():
    scheduler.add_job(run_collection, id="manual-run", replace_existing=True)
    return RedirectResponse("/", 303)


def reschedule() -> None:
    if scheduler.get_job("daily-run"):
        scheduler.remove_job("daily-run")
    schedule = setting("schedule").strip()
    if schedule:
        try:
            hour, minute = schedule.split(":")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError
        except (TypeError, ValueError):
            set_setting("custom_site_message", "每日采集时间格式无效，定时采集未启用。")
        else:
            scheduler.add_job(run_collection, "cron", hour=int(hour), minute=int(minute), id="daily-run", replace_existing=True)
    backup_hour, backup_minute = setting("backup_schedule", "02:20").split(":")
    scheduler.add_job(run_backup, "cron", hour=int(backup_hour), minute=int(backup_minute), id="daily-backup", replace_existing=True)


def run_backup() -> None:
    try:
        target = backup_database(int(setting("backup_retention_days", "14")))
        set_setting("last_backup", now_text())
        set_setting("backup_message", f"备份完成：{target.name}")
    except Exception as exc:
        set_setting("backup_message", f"备份失败：{type(exc).__name__}")
