import json
from urllib.parse import parse_qs, urlparse

from app.connectors import custom
from app.connectors.custom import _records_at_path, expand_api_profile, infer_api_profile


def test_infers_vue_public_json_list():
    payload = {"code": 200, "total": 3, "dataList": [
        {"id": str(index), "dataTitle": f"测试公告项目 {index}", "releaseTimeStr": "2026-08-07", "codeModeName": "公开招标"}
        for index in range(3)
    ]}
    profile = infer_api_profile(
        "https://example.com/api/list?pageNum=1&pageSize=10&t=123",
        payload,
        "https://example.com/#/announcements",
    )
    assert profile is not None
    assert profile["records_path"] == "$.dataList"
    assert profile["title_field"] == "dataTitle"
    assert profile["date_field"] == "releaseTimeStr"
    assert profile["page_param"] == "pageNum"
    assert "t=" not in profile["endpoint"]
    assert len(_records_at_path(payload, profile["records_path"])) == 3


def test_rejects_cross_origin_api():
    payload = {"items": [{"title": f"公告 {i} 测试项目", "date": "2026-08-07"} for i in range(3)]}
    assert infer_api_profile("https://other.example/api", payload, "https://example.com/notices") is None


def test_jsgx_expands_all_required_public_feeds():
    base = {"version": 1, "mode": "public_json", "endpoint": "https://ec.jsgx.net/api-base/purchaseInfomation/list?pageNum=1&pageSize=10",
            "records_path": "$.dataList", "title_field": "dataTitle", "date_field": "releaseTimeStr",
            "type_field": "codeModeName", "page_param": "pageNum", "size_param": "pageSize"}
    profile = expand_api_profile(base, "https://ec.jsgx.net/#/publicannouncement")
    assert [feed["label"] for feed in profile["feeds"]] == ["招标公告", "公开询比采购", "公开谈判采购", "直接采购公示"]
    assert all(feed["start_date_param"] == "releaseTimeStart" for feed in profile["feeds"])


def test_multifeed_collector_uses_date_filter_and_all_pages(monkeypatch):
    base = {"version": 1, "mode": "public_json", "endpoint": "https://ec.jsgx.net/api-base/purchaseInfomation/list?pageNum=1&pageSize=10",
            "records_path": "$.dataList", "title_field": "dataTitle", "date_field": "releaseTimeStr",
            "type_field": "codeModeName", "page_param": "pageNum", "size_param": "pageSize"}
    calls = []

    def fake_fetch(url, _site_url):
        if "/api-purchase/publicity/supp/get" in url:
            return {"code": 401}
        query = parse_qs(urlparse(url).query)
        calls.append(query)
        page = int(query["pageNum"][0])
        count = 50 if page == 1 else 1
        prefix = query["businessTypeArr"][0]
        return {"total": 51, "dataList": [
            {"id": f"{prefix}-{page}-{index}", "businessId": f"b-{page}-{index}", "businessCode": f"c-{page}-{index}",
             "businessType": prefix.split(",")[0], "codeMode": query["codeMode"][0],
             "dataTitle": f"JSTCC project {page}-{index}", "releaseTimeStr": "2026-08-07", "codeModeName": "notice"}
            for index in range(count)
        ]}

    monkeypatch.setattr(custom, "_fetch_public_json", fake_fetch)
    monkeypatch.setattr(custom.time, "sleep", lambda _seconds: None)
    site = {"name": "test", "url": "https://ec.jsgx.net/#/publicannouncement", "profile_json": json.dumps(base)}
    items, warning = custom._collect_public_api(site, "2026-08-07", ["project"], [])
    assert warning == ""
    assert len(items) == 204
    assert len(calls) == 8
    assert all(call["releaseTimeStart"] == ["2026-08-07"] and call["releaseTimeEnd"] == ["2026-08-07"] for call in calls)
