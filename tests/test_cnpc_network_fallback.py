from pathlib import Path

from app.connectors.cnpc import DATE_FIELDS, TITLE_FIELDS, _record_lists, _record_value
from app.connectors.custom import profile_site


def test_cnpc_finds_records_in_nested_public_json():
    payload = {"code": 200, "data": {"rows": [
        {"articleId": "1", "noticeTitle": "数字化平台建设项目", "publishDate": "2026-08-14"},
        {"articleId": "2", "noticeTitle": "信息系统运维服务", "publishDate": "2026-08-14"},
    ]}}

    records = _record_lists(payload)[0]

    assert _record_value(records[0], TITLE_FIELDS) == "数字化平台建设项目"
    assert _record_value(records[0], DATE_FIELDS) == "2026-08-14"


def test_navigation_matches_visible_section_order():
    template = Path("app/templates/index.html").read_text(encoding="utf-8")
    navigation = template.split("<nav", 1)[1].split("</nav>", 1)[0]
    anchors = ["#results", "#activity", "#sites", "#local-model", "#delivery", "#system"]

    assert [navigation.index(anchor) for anchor in anchors] == sorted(navigation.index(anchor) for anchor in anchors)


def test_cnpc_is_enabled_as_a_resilient_dynamic_adapter(monkeypatch):
    monkeypatch.setattr("app.connectors.custom.validate_public_url", lambda url: url)

    profile = profile_site("https://www.cnpcbidding.com/#/tenders")

    assert profile["status"] == "已适配（动态浏览器）"
    assert profile["selector"] == "network:json"
