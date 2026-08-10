from app.connectors.custom import _records_at_path, infer_api_profile


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
