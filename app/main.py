from __future__ import annotations

import base64
import os
import secrets
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import connect, init_db, now_text, set_setting, setting
from .connectors.custom import profile_site, validate_public_url

app = FastAPI(title="招标采集管理平台")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


@app.middleware("http")
async def require_admin(request: Request, call_next):
    """Keep the dashboard private even when Docker publishes port 8000."""
    if request.url.path.startswith("/static/"):
        return await call_next(request)
    configured = setting("admin_password", os.getenv("ADMIN_PASSWORD", "admin"), secret=True)
    username = setting("admin_username", os.getenv("ADMIN_USERNAME", "admin"))
    auth = request.headers.get("authorization", "")
    try:
        scheme, token = auth.split(" ", 1)
        supplied_username, password = base64.b64decode(token).decode().split(":", 1)
    except Exception:
        scheme, supplied_username, password = "", "", ""
    if not configured or scheme.lower() != "basic" or supplied_username != username or not secrets.compare_digest(password, configured):
        return PlainTextResponse("需要管理员登录", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Tender Platform"'})
    return await call_next(request)


def dashboard_context() -> dict:
    with connect() as db:
        sites = db.execute("SELECT * FROM sites ORDER BY name").fetchall()
        keywords = db.execute("SELECT * FROM keywords WHERE enabled=1 ORDER BY term").fetchall()
        runs = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 8").fetchall()
        results = db.execute("SELECT * FROM tenders ORDER BY first_seen_at DESC LIMIT 20").fetchall()
        custom_sites = db.execute("SELECT * FROM custom_sites ORDER BY id DESC").fetchall()
    return {"sites": sites, "keywords": keywords, "runs": runs, "results": results, "custom_sites": custom_sites,
            "schedule": setting("schedule", "08:00"), "recipient": setting("recipient"),
            "smtp_host": setting("smtp_host", "smtp.163.com"), "smtp_port": setting("smtp_port", "465"),
            "smtp_user": setting("smtp_user"), "smtp_from": setting("smtp_from"),
            "smtp_configured": bool(setting("smtp_auth_code", secret=True)), "admin_username": setting("admin_username", "admin")}


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


@app.post("/sites/{code}/toggle")
def toggle_site(code: str):
    with connect() as db:
        db.execute("UPDATE sites SET enabled=1-enabled WHERE code=?", (code,))
    return RedirectResponse("/", 303)


@app.post("/custom-sites")
def add_custom_site(name: str = Form(...), url: str = Form(...)):
    try:
        safe_url = validate_public_url(url)
        profile = profile_site(safe_url)
        with connect() as db:
            db.execute("""INSERT INTO custom_sites(name,url,engine,status,list_selector,profile_note,created_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET name=excluded.name,engine=excluded.engine,status=excluded.status,list_selector=excluded.list_selector,profile_note=excluded.profile_note""", (name.strip() or safe_url, profile["url"], profile["engine"], profile["status"], profile["selector"], profile["note"], now_text()))
    except ValueError as exc:
        set_setting("custom_site_message", str(exc))
    except Exception as exc:
        set_setting("custom_site_message", f"自动适配失败：{type(exc).__name__}")
    return RedirectResponse("/", 303)


@app.post("/custom-sites/{site_id}/toggle")
def toggle_custom_site(site_id: int):
    with connect() as db:
        db.execute("UPDATE custom_sites SET enabled=1-enabled WHERE id=?", (site_id,))
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


@app.post("/admin-credentials")
def save_admin_credentials(admin_username: str = Form(...), new_password: str = Form(...), confirm_password: str = Form(...)):
    if len(admin_username.strip()) < 3 or len(new_password) < 8 or new_password != confirm_password:
        return RedirectResponse("/", 303)
    set_setting("admin_username", admin_username.strip())
    set_setting("admin_password", new_password, secret=True)
    return RedirectResponse("/", 303)


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
    hour, minute = setting("schedule", "08:00").split(":")
    scheduler.add_job(run_collection, "cron", hour=int(hour), minute=int(minute), id="daily-run", replace_existing=True)
