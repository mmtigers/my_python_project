# MY_HOME_SYSTEM/tests/test_misc_tab_html_escaping.py
"""
views/dashboard/misc_tab.py の unsafe_allow_html=True 埋め込み箇所が、
外部スクレイピング由来の文字列をHTMLエスケープしてから埋め込んでいることの回帰テスト。
(docsバックログ B4: スクレイピング由来文字列の未エスケープHTML埋め込み)
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard import misc_tab

XSS_PAYLOAD = "<script>alert(1)</script>"


def _mock_st():
    mock = MagicMock()
    mock.columns.return_value = (MagicMock(), MagicMock())
    mock.container.return_value = MagicMock()
    return mock


def _rendered_html(mock_markdown) -> str:
    return "\n".join(str(call.args[0]) for call in mock_markdown.call_args_list)


def test_render_traffic_escapes_scraped_status_and_detail():
    malicious_status = f"遅延 {XSS_PAYLOAD}"
    malicious_detail = f"詳細 {XSS_PAYLOAD}"
    fake_status = {
        "宝塚線": {"status": malicious_status, "detail": malicious_detail, "is_delay": True, "is_unavailable": False},
        "神戸線": {"status": malicious_status, "detail": malicious_detail, "is_delay": True, "is_unavailable": False},
    }

    mock_st = _mock_st()
    with patch.object(misc_tab, "st", mock_st), \
         patch.object(misc_tab.train_service, "get_jr_traffic_status", return_value=fake_status), \
         patch.object(misc_tab, "_render_route_search"):
        misc_tab.render_traffic()

    html = _rendered_html(mock_st.markdown)
    assert XSS_PAYLOAD not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_render_route_search_escapes_scraped_fields():
    fake_route = {
        "summary": "取得成功",
        "departure": f"08:00{XSS_PAYLOAD}",
        "arrival": f"08:30{XSS_PAYLOAD}",
        "duration": f"30分{XSS_PAYLOAD}",
        "cost": f"400円{XSS_PAYLOAD}",
        "transfer": f"1回{XSS_PAYLOAD}",
        "details": [f"🚉 出発駅{XSS_PAYLOAD}", f"⬇️ 路線{XSS_PAYLOAD}", f"🔄 乗換駅{XSS_PAYLOAD}"],
        "url": "",
    }

    mock_st = _mock_st()
    with patch.object(misc_tab, "st", mock_st), \
         patch.object(misc_tab.train_service, "get_route_info", return_value=fake_route):
        misc_tab._render_route_search(MagicMock(), "A", "B", "icon")

    html = _rendered_html(mock_st.markdown)
    assert XSS_PAYLOAD not in html
    assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") == len(fake_route["details"]) + 5
