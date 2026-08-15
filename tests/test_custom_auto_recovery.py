from app import runner
from app.database import connect, init_db


def _site() -> dict:
    with connect() as db:
        cursor = db.execute("""INSERT INTO custom_sites(name,url,engine,status,list_selector,profile_note,created_at)
            VALUES(?,?,?,?,?,?,?)""", ("测试站点", "https://example.com/notices", "旧引擎", "已适配（静态列表）",
                                    ".old-list a", "旧规则", "2026-08-15T00:00:00+08:00"))
        return dict(db.execute("SELECT * FROM custom_sites WHERE id=?", (cursor.lastrowid,)).fetchone())


def test_failed_custom_site_is_reprofiled_and_retried(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "platform.sqlite3"))
    init_db()
    site = _site()
    calls = []

    def fake_collect(current, *_args):
        calls.append(current["list_selector"])
        if current["list_selector"] == ".old-list a":
            return [], "测试站点：旧选择器失效"
        return [{"title": "恢复成功"}], ""

    monkeypatch.setattr(runner, "collect_custom_site", fake_collect)
    monkeypatch.setattr(runner, "auto_reprofile_site", lambda _url: {
        "engine": "Fetcher + 自适应选择器", "status": "已适配（静态列表）",
        "selector": ".new-list a", "note": "新规则", "profile_json": "",
    })
    items, notice = runner._collect_custom_with_recovery(site, "2026-08-15", ["软件"], [])
    assert items == [{"title": "恢复成功"}]
    assert calls == [".old-list a", ".new-list a"]
    assert "当轮恢复采集" in notice
    with connect() as db:
        updated = db.execute("SELECT * FROM custom_sites WHERE id=?", (site["id"],)).fetchone()
    assert updated["list_selector"] == ".new-list a"
    assert updated["failure_count"] == 0
    assert updated["last_adapted_at"]


def test_unsuccessful_reprofile_keeps_old_rule(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "platform.sqlite3"))
    init_db()
    site = _site()
    monkeypatch.setattr(runner, "collect_custom_site", lambda *_args: ([], "测试站点：暂时失败"))
    monkeypatch.setattr(runner, "auto_reprofile_site", lambda _url: {
        "engine": "自动恢复", "status": "待自动恢复", "selector": "a", "note": "尚未识别",
    })
    _items, notice = runner._collect_custom_with_recovery(site, "2026-08-15", ["软件"], [])
    assert "下次采集将继续尝试" in notice
    with connect() as db:
        updated = db.execute("SELECT * FROM custom_sites WHERE id=?", (site["id"],)).fetchone()
    assert updated["list_selector"] == ".old-list a"
    assert updated["status"] == "已适配（静态列表）"
    assert updated["failure_count"] == 1
