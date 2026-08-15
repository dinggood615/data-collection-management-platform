from app.connectors.custom import _response_text
from app.main import summarize_run_messages


def test_response_text_uses_body_when_scrapling_text_is_empty():
    class EmptyTextHandler(str):
        pass

    class Response:
        text = EmptyTextHandler("")
        body = b"<html><a href='/notice'>notice</a></html>"
        html_content = "ignored"

    assert "notice" in _response_text(Response())


def test_repeated_recovery_warnings_are_collapsed_by_site():
    warning = "国能：页面结构已失效，未发现任何可采集链接；规则已自动更新，但当轮重试仍失败；南网招标公告：页面结构已失效，未发现任何可采集链接"
    summary = summarize_run_messages([warning, warning, warning])

    assert summary.startswith("自动恢复处理中：")
    assert summary.count("国能") == 1
    assert summary.count("南网招标公告") == 1
    assert "规则已自动更新" not in summary
    assert "无需人工操作" in summary


def test_access_restrictions_are_delayed_without_claiming_bypass():
    summary = summarize_run_messages("示例站点：需要登录或验证码，附件需要人工检查")

    assert "自动延后重试" in summary
    assert "不会绕过登录或验证码" in summary
