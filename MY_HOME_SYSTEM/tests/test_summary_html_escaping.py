# MY_HOME_SYSTEM/tests/test_summary_html_escaping.py
"""
views/dashboard/common.py の render_status_card_html が title/value を
HTMLエスケープすること(Issue #378)、および views/dashboard/summary.py の
get_bicycle_status のように意図的なHTML断片を渡す呼び出し元では
value_is_html=True でエスケープをスキップできることの回帰テスト。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard.common import render_status_card_html

XSS_PAYLOAD = "<img src=x onerror=alert(1)>"


class TestRenderStatusCardHtmlEscaping:
    def test_malicious_title_is_escaped(self):
        html_out = render_status_card_html(f"タイトル{XSS_PAYLOAD}", "value", "theme-green")
        assert XSS_PAYLOAD not in html_out
        assert "&lt;img src=x onerror=alert(1)&gt;" in html_out

    def test_malicious_value_is_escaped_by_default(self):
        html_out = render_status_card_html("title", f"値{XSS_PAYLOAD}", "theme-green")
        assert XSS_PAYLOAD not in html_out
        assert "&lt;img src=x onerror=alert(1)&gt;" in html_out

    def test_plain_title_and_value_render_unchanged(self):
        html_out = render_status_card_html("🏠 伊丹 (自宅)", "🟢 活動中 (今)", "theme-green")
        assert "🏠 伊丹 (自宅)" in html_out
        assert "🟢 活動中 (今)" in html_out

    def test_value_is_html_true_preserves_intentional_html_fragment(self):
        """get_bicycle_status のように前日比の色付け(<span>)を意図的に組み立てる
        呼び出し元は value_is_html=True でHTML断片をそのまま埋め込めること。"""
        intentional_html = "第1A: <b>3</b>台 <span style='color:#d32f2f;'>(🔺1)</span>"
        html_out = render_status_card_html("🚲 駐輪場待機", intentional_html, "theme-yellow", value_is_html=True)
        assert "<b>3</b>" in html_out
        assert "<span style='color:#d32f2f;'>(🔺1)</span>" in html_out

    def test_title_is_still_escaped_even_when_value_is_html(self):
        """value_is_html=True はvalueのみに適用され、titleは常にエスケープされること。"""
        html_out = render_status_card_html(f"タイトル{XSS_PAYLOAD}", "<b>安全なHTML</b>", "theme-green", value_is_html=True)
        assert XSS_PAYLOAD not in html_out
        assert "&lt;img src=x onerror=alert(1)&gt;" in html_out
        assert "<b>安全なHTML</b>" in html_out
