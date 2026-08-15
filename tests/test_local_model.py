import json

from app import local_model
from app.database import connect, init_db


def test_model_queue_deduplicates_active_reference(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "platform.sqlite3"))
    init_db()
    assert local_model.enqueue_site_adaptation(8, "规则失败") is True
    assert local_model.enqueue_site_adaptation(8, "再次失败") is False
    with connect() as db:
        tasks = db.execute("SELECT * FROM model_tasks").fetchall()
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "adapt_site"


def test_model_only_activates_a_validated_candidate(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "platform.sqlite3"))
    init_db()
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO custom_sites(name,url,status,created_at) VALUES(?,?,?,?)",
            ("测试站点", "https://example.com/notices", "待自动恢复", "2026-08-15T00:00:00+08:00"),
        )
        site_id = cursor.lastrowid
    html = b"<ul class='notice-list'>" + b"".join(
        f"<li><a href='/n/{index}'>2026年度信息化采购公告第{index}号</a></li>".encode()
        for index in range(1, 6)
    ) + b"</ul>"

    class Response:
        headers = type("Headers", (), {"get_content_charset": lambda self: "utf-8"})()
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit): return html

    monkeypatch.setattr("app.connectors.custom.validate_public_url", lambda url: url)
    monkeypatch.setattr(local_model, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(local_model, "_infer", lambda prompt: {
        "selector": "li a[href]", "confidence": 91, "reason": "公告标题稳定"
    } if "li a[href]" in prompt else {
        "selector": "ul.notice-list a[href]", "confidence": 91, "reason": "公告标题稳定"
    })
    result = local_model._adapt_site(str(site_id))
    assert result["confidence"] == 91
    with connect() as db:
        site = db.execute("SELECT * FROM custom_sites WHERE id=?", (site_id,)).fetchone()
    assert site["status"] == "已适配（静态列表）"
    assert site["engine"] == "规则引擎 + 本地小模型"


def test_pending_analysis_updates_structured_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "platform.sqlite3"))
    init_db()
    with connect() as db:
        db.execute(
            """INSERT INTO tenders(fingerprint,source,title,url,published_date,notice_type,matched_terms,first_seen_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            ("fp-1", "测试", "信息系统升级实施项目", "https://example.com/1", "2026-08-14", "公告", "信息化", "now"),
        )
    local_model.enqueue_tender_analysis("fp-1")
    monkeypatch.setattr(local_model, "model_available", lambda: True)
    monkeypatch.setattr(local_model, "_infer", lambda *_args, **_kwargs: {
        "category": "软件实施", "summary": "升级并实施业务信息系统", "confidence": 88
    })
    assert local_model.process_pending(1) == 1
    with connect() as db:
        item = db.execute("SELECT * FROM tenders WHERE fingerprint='fp-1'").fetchone()
        task = db.execute("SELECT * FROM model_tasks").fetchone()
    assert item["ai_category"] == "软件实施"
    assert item["ai_confidence"] == 88
    assert json.loads(task["result_json"])["category"] == "软件实施"
    assert task["status"] == "completed"


def test_inference_is_single_turn_and_bounded(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = '{"category":"其他","summary":"测试","confidence":80}'
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(local_model, "model_available", lambda: True)
    monkeypatch.setattr(local_model.subprocess, "run", fake_run)
    result = local_model._infer("测试", 32)
    assert result["confidence"] == 80
    assert "--single-turn" in captured["command"]
    assert captured["kwargs"]["timeout"] == local_model.MODEL_TIMEOUT


def test_json_parser_uses_last_complete_model_object():
    output = 'prompt example {"confidence":"0到100"}\nmodel answer {"confidence":87,"category":"信息化"}\n'
    assert local_model._extract_json(output) == {"confidence": 87, "category": "信息化"}
