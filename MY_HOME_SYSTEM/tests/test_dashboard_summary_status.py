# MY_HOME_SYSTEM/tests/test_dashboard_summary_status.py
"""
ステータスカードの判定ヘルパーの回帰テスト(Issue #754)。

判定ロジックは `views/dashboard/summary.py` から
`services/home_status_service.py` へ移した。Streamlit を介さない軽量ページ
(`/dashboard/m`)が同じカードを出すため、判定を2箇所に持たないようにしたもの。

`.coveragerc` の omit から `views/dashboard/*` を外すにあたって追加した。
これらは「DataFrame を受け取って (表示文字列, テーマ名) を返す」純粋関数で、
Streamlit を起動せずに検証できる。omit されていた間はカバレッジのラチェット
(master 比 0.5pt)の対象外だったため、分岐を増やしてもテストを書かずに済む
状態が構造的に許されていた(#367 が解消した「水増しされた指標」の残り分)。
"""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import home_status_service
from views.dashboard import summary

# JST 固定。naive な datetime() を localize するより、オフセット付きの
# ISO 文字列から起こすほうが「どの時刻か」が読んで分かる。
NOW = datetime.fromisoformat("2026-09-19T12:00:00+09:00")


def _sensor_df(rows):
    """センサーDataFrameを作る。列が欠けていると判定側が早期returnするため、
    実際に analysis_service.load_sensor_data が返す列を一通り持たせる。"""
    columns = [
        "timestamp", "location", "device_type", "device_name", "friendly_name",
        "movement_state", "contact_state", "power_watts",
    ]
    df = pd.DataFrame(rows, columns=columns)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def _row(**kwargs):
    base = {
        "timestamp": NOW, "location": "伊丹", "device_type": "Meter",
        "device_name": "", "friendly_name": "", "movement_state": "",
        "contact_state": "", "power_watts": 0,
    }
    base.update(kwargs)
    return base


class TestGetTakasagoStatus:
    def test_empty_dataframe_returns_no_data(self):
        assert home_status_service.get_takasago_status(pd.DataFrame(), NOW) == ("⚪ データなし", "theme-gray")

    def test_missing_columns_returns_no_data(self):
        df = pd.DataFrame([{"timestamp": NOW}])
        assert home_status_service.get_takasago_status(df, NOW) == ("⚪ データなし", "theme-gray")

    def test_no_takasago_rows_returns_no_data(self):
        df = _sensor_df([_row(location="伊丹", contact_state="open")])
        assert home_status_service.get_takasago_status(df, NOW) == ("⚪ データなし", "theme-gray")

    def test_activity_within_one_hour_is_green(self):
        df = _sensor_df([_row(location="高砂", contact_state="detected",
                              timestamp=NOW - timedelta(minutes=30))])
        val, theme = home_status_service.get_takasago_status(df, NOW)
        assert val == "🟢 元気 (1h以内)"
        assert theme == "theme-green"

    def test_activity_within_three_hours_is_yellow(self):
        df = _sensor_df([_row(location="高砂", contact_state="open",
                              timestamp=NOW - timedelta(hours=2))])
        val, theme = home_status_service.get_takasago_status(df, NOW)
        assert val == "🟡 静か (3h以内)"
        assert theme == "theme-yellow"

    def test_no_activity_for_hours_is_red_with_hour_count(self):
        df = _sensor_df([_row(location="高砂", contact_state="open",
                              timestamp=NOW - timedelta(hours=5))])
        val, theme = home_status_service.get_takasago_status(df, NOW)
        assert val == "🔴 5時間 動きなし"
        assert theme == "theme-red"


class TestGetItamiStatus:
    def test_empty_dataframe_returns_no_data(self):
        assert home_status_service.get_itami_status(pd.DataFrame(), NOW) == ("⚪ データなし", "theme-gray")

    def test_missing_required_columns_returns_no_data(self):
        df = pd.DataFrame([{"timestamp": NOW, "location": "伊丹"}])
        assert home_status_service.get_itami_status(df, NOW) == ("⚪ データなし", "theme-gray")

    def test_motion_within_ten_minutes_is_active_now(self):
        df = _sensor_df([_row(device_type="Motion Sensor", movement_state="detected",
                              timestamp=NOW - timedelta(minutes=3))])
        assert home_status_service.get_itami_status(df, NOW) == ("🟢 活動中 (今)", "theme-green")

    def test_webhook_device_with_contact_detected_is_also_treated_as_motion(self):
        """webhook_router が movement ではなく contact_state に 'detected' を
        保存してしまう既知の挙動への対応が生きていること。"""
        df = _sensor_df([_row(device_type="Webhook", contact_state="detected",
                              timestamp=NOW - timedelta(minutes=30))])
        val, theme = home_status_service.get_itami_status(df, NOW)
        assert val == "🟢 活動中 (30分前)"
        assert theme == "theme-green"

    def test_motion_over_one_hour_ago_is_quiet(self):
        df = _sensor_df([_row(device_type="Motion Sensor", movement_state="detected",
                              timestamp=NOW - timedelta(hours=3))])
        assert home_status_service.get_itami_status(df, NOW) == ("🟡 静か (3h前)", "theme-yellow")

    def test_falls_back_to_contact_sensor_when_no_motion(self):
        df = _sensor_df([_row(device_type="Contact Sensor", contact_state="open",
                              timestamp=NOW - timedelta(minutes=20))])
        assert home_status_service.get_itami_status(df, NOW) == ("🟢 活動中 (20分前)", "theme-green")

    def test_stale_contact_sensor_stays_no_data(self):
        df = _sensor_df([_row(device_type="Contact Sensor", contact_state="open",
                              timestamp=NOW - timedelta(hours=5))])
        assert home_status_service.get_itami_status(df, NOW) == ("⚪ データなし", "theme-gray")


class TestGetServerStatus:
    def test_low_memory_is_green(self):
        assert home_status_service.get_server_status({"percent": 42.7}) == ("💻 RAM: 42%", "theme-green")

    def test_high_memory_is_red(self):
        assert home_status_service.get_server_status({"percent": 91.0}) == ("💻 RAM: 91%", "theme-red")

    def test_unavailable_memory_is_gray(self):
        assert home_status_service.get_server_status(None) == ("⚪ 取得失敗", "theme-gray")


class TestGetNasStatusSimple:
    def test_none_is_no_data(self):
        assert home_status_service.get_nas_status_simple(None) == ("⚪ データなし", "theme-gray")

    def test_ping_ok_is_green(self):
        assert home_status_service.get_nas_status_simple(pd.Series({"status_ping": "OK"})) == ("🗄️ NAS: 稼働中", "theme-green")

    def test_ping_ng_is_red(self):
        assert home_status_service.get_nas_status_simple(pd.Series({"status_ping": "NG"})) == ("⚠️ NAS: 応答なし", "theme-red")

    def test_missing_key_is_reported_as_broken_data(self):
        assert home_status_service.get_nas_status_simple(pd.Series({"other": 1})) == ("⚠️ NAS: データ異常", "theme-yellow")


class TestGetCarStatus:
    def test_leave_means_out(self):
        df = pd.DataFrame([{"action": "LEAVE"}])
        assert home_status_service.get_car_status(df) == ("🚗 外出中", "theme-yellow")

    def test_arrive_means_home(self):
        df = pd.DataFrame([{"action": "ARRIVE"}])
        assert home_status_service.get_car_status(df) == ("🏠 在宅", "theme-green")

    def test_empty_defaults_to_home(self):
        assert home_status_service.get_car_status(pd.DataFrame()) == ("🏠 在宅", "theme-green")


class TestGetRiceStatus:
    def test_missing_columns_returns_not_cooked(self):
        assert home_status_service.get_rice_status(pd.DataFrame(), NOW) == ("🍚 炊いてない", "theme-red")

    def test_no_rice_cooker_rows_returns_not_cooked(self):
        df = _sensor_df([_row(device_name="エアコン", power_watts=800)])
        assert home_status_service.get_rice_status(df, NOW) == ("🍚 炊いてない", "theme-red")

    def test_high_wattage_today_means_rice_available(self):
        df = _sensor_df([_row(device_name="炊飯器", power_watts=700,
                              timestamp=NOW - timedelta(hours=2))])
        assert home_status_service.get_rice_status(df, NOW) == ("🍚 ご飯あり", "theme-green")

    def test_standby_wattage_does_not_count(self):
        df = _sensor_df([_row(device_name="炊飯器", power_watts=3,
                              timestamp=NOW - timedelta(hours=2))])
        assert home_status_service.get_rice_status(df, NOW) == ("🍚 炊いてない", "theme-red")

    def test_yesterdays_cooking_does_not_count(self):
        df = _sensor_df([_row(device_name="炊飯器", power_watts=700,
                              timestamp=NOW - timedelta(days=1))])
        assert home_status_service.get_rice_status(df, NOW) == ("🍚 炊いてない", "theme-red")


class TestRenderSummary:
    def test_renders_all_status_cards(self):
        df_sensor = _sensor_df([_row(location="高砂", contact_state="detected")])
        df_car = pd.DataFrame([{"action": "ARRIVE"}])

        with patch.object(summary.view_common, "render_status_grid") as mock_grid, \
             patch.object(summary.view_common, "get_monthly_cost_cached", return_value=4321), \
             patch.object(summary.view_common, "get_memory_usage_cached", return_value={"percent": 50}):
            summary.render_summary(NOW, df_sensor, df_car, None)

        cards = mock_grid.call_args[0][0]
        assert len(cards) == 7
        titles = [c.title for c in cards]
        assert titles[0] == "👵 高砂 (実家)"
        assert "💰 今月の電気代" in titles
        # 電気代は3桁区切りで整形される
        assert any(c.value == "⚡ 4,321 円" for c in cards)
        # 退役したカード(駐輪場・JR運行情報)が復活していないこと
        assert not any("駐輪場" in t or "JR" in t for t in titles)

    def test_section_failure_propagates_to_caller_for_safe_section_to_catch(self):
        """render_summary 自身は例外を握りつぶさず、dashboard.py 側の
        safe_section(#438)が隔離する設計であること。"""
        with patch.object(summary.view_common, "get_monthly_cost_cached",
                          side_effect=RuntimeError("boom")), \
             patch.object(summary.view_common, "render_status_grid") as mock_grid:
            try:
                summary.render_summary(NOW, pd.DataFrame(), pd.DataFrame(), None)
            except RuntimeError:
                pass
            else:
                raise AssertionError("例外が呼び出し元へ伝播しなかった")
        mock_grid.assert_not_called()


class TestRenderStatusGridIntegration:
    def test_grid_emits_a_single_markdown_block_with_all_cards(self):
        from views.dashboard import common as view_common

        mock_st = MagicMock()
        cards = [
            view_common.StatusCard("A", "1", "theme-green"),
            view_common.StatusCard("B", "2", "theme-red"),
        ]
        with patch.object(view_common, "st", mock_st):
            view_common.render_status_grid(cards)

        mock_st.markdown.assert_called_once()
        html_out = mock_st.markdown.call_args[0][0]
        assert html_out.count("status-card") == 2
        assert html_out.startswith('<div class="status-grid">')
        assert mock_st.markdown.call_args[1]["unsafe_allow_html"] is True

    def test_grid_html_survives_streamlit_markdown_preprocessing(self):
        """カードのHTMLが「Markdownのインデントコードブロック」として
        生のタグ文字列で表示されてしまう回帰の防止。

        `st.markdown` は本文に `textwrap.dedent()` を掛けてからMarkdownとして
        解釈する(`streamlit.string_util.clean_text`)。グリッドの先頭行
        `<div class="status-grid">` はインデント0なので共通インデントが0になり、
        dedentは何も削らない。以前の `render_status_card_html` は整形用の改行と
        4スペース字下げを含む複数行を返していたため、

          - カードとカードの間に「空白だけの行」ができてHTMLブロックが終端され
          - 続く4スペース字下げの行がインデントコードブロックと解釈され

        2枚目以降のカードがスマホ画面に `<div class="status-c...` という
        生のタグ文字列として並んでいた(横幅も溢れた)。整形用の空白を
        一切持たないことをここで固定する。
        """
        import textwrap

        from views.dashboard import common as view_common

        mock_st = MagicMock()
        cards = [
            view_common.StatusCard("👵 高砂 (実家)", "🟢 元気 (1h以内)", "theme-green"),
            view_common.StatusCard("🏠 伊丹 (自宅)", "🟢 活動中 (今)", "theme-green"),
            view_common.StatusCard("🔧 テスト", "<b>0</b>件<br><b>1</b>件",
                                   "theme-yellow", value_is_html=True),
        ]
        with patch.object(view_common, "st", mock_st):
            view_common.render_status_grid(cards)

        html_out = mock_st.markdown.call_args[0][0]
        # Streamlit が実際に行う前処理を再現する
        rendered = textwrap.dedent(html_out).strip()

        assert "\n" not in rendered, (
            "グリッドのHTMLに改行が含まれている。空白行やインデント行ができると "
            f"Markdownがコードブロックとして解釈する: {rendered!r}"
        )
        assert rendered.count('class="status-card') == 3

    def test_single_card_html_has_no_formatting_whitespace(self):
        from views.dashboard import common as view_common

        card_html = view_common.render_status_card_html("タイトル", "値", "theme-green")
        assert "\n" not in card_html
        assert card_html.startswith('<div class="status-card theme-green">')
        assert card_html.endswith("</div>")
