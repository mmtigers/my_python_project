# MY_HOME_SYSTEM/tests/test_dashboard_summary_status.py
"""
ステータスカードの判定ヘルパーの回帰テスト(Issue #754)。

判定ロジックは `services/home_status_service.py` に一本化されている。
これらは「DataFrame を受け取って (表示文字列, テーマ名) を返す」純粋関数で、
Streamlit を起動せずに検証できる(#829でStreamlit版ダッシュボードは廃止済み)。
"""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import home_status_service

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


class TestGetCameraStatus:
    """カメラの直近検知。**色は情報色(青)かグレーだけ**にする。

    家族が出入りすれば毎日検知するため、赤・黄にすると「気になること」の
    要約行が毎回埋まって意味を失う。
    """

    def _df(self, rows):
        df = pd.DataFrame(rows, columns=["timestamp", "device_type", "movement_state", "friendly_name"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df

    def _row(self, minutes_ago, name="玄関"):
        return {
            "timestamp": NOW - timedelta(minutes=minutes_ago),
            "device_type": home_status_service.CAMERA_DEVICE_TYPE,
            "movement_state": "ON",
            "friendly_name": name,
        }

    def test_empty_is_no_data(self):
        assert home_status_service.get_camera_status(pd.DataFrame(), NOW) == ("⚪ データなし", "theme-gray")

    def test_missing_columns_is_no_data(self):
        df = pd.DataFrame([{"timestamp": NOW}])
        assert home_status_service.get_camera_status(df, NOW) == ("⚪ データなし", "theme-gray")

    def test_other_devices_are_ignored(self):
        """人感センサー等はここでは拾わない(伊丹カードの担当)。"""
        df = self._df([{
            "timestamp": NOW, "device_type": "Motion Sensor",
            "movement_state": "detected", "friendly_name": "リビング",
        }])
        assert home_status_service.get_camera_status(df, NOW) == ("⚪ データなし", "theme-gray")

    def test_just_now_is_reported_as_moving(self):
        val, theme = home_status_service.get_camera_status(self._df([self._row(3)]), NOW)
        assert val == "🎥 いま動きあり"
        assert theme == "theme-blue"

    def test_within_an_hour_shows_minutes(self):
        val, _ = home_status_service.get_camera_status(self._df([self._row(25)]), NOW)
        assert val == "🎥 25分前に検知"

    def test_within_a_day_shows_hours(self):
        val, _ = home_status_service.get_camera_status(self._df([self._row(200)]), NOW)
        assert val == "🎥 3時間前に検知"

    def test_older_than_a_day_is_gray(self):
        val, theme = home_status_service.get_camera_status(self._df([self._row(60 * 30)]), NOW)
        assert val == "🎥 24時間 検知なし"
        assert theme == "theme-gray"

    def test_detection_never_raises_an_alert(self):
        """検知そのものは異常ではない(要約行を毎日埋めない)。"""
        for minutes in (1, 30, 300, 60 * 48):
            _, theme = home_status_service.get_camera_status(self._df([self._row(minutes)]), NOW)
            assert theme in ("theme-blue", "theme-gray"), f"{minutes}分前が警告色になっている"

    def test_the_place_is_added_as_a_supporting_line(self):
        """「5分前に検知」だけでは、どのカメラかが分からない。"""
        df = self._df([self._row(5, name="駐車場")])
        assert home_status_service.describe_camera(df, NOW) == "駐車場 " + (NOW - timedelta(minutes=5)).strftime("%H:%M")


class TestCardGroups:
    """カードは利用頻度順に並び、用途の変わり目に見出しが入る。"""

    def _cards(self):
        return home_status_service.build_status_cards(
            NOW, pd.DataFrame(), None, {"percent": 40}, 1234,
        )

    def test_watch_cards_come_first(self):
        """スマホで最初に見たいのは見守り(実家・自宅・駐車場・カメラ)。"""
        groups = [card.group for card in self._cards()]
        assert groups[:4] == ["watch"] * 4
        assert groups[-2:] == ["sys"] * 2, "毎回は見ないシステム系が最後にあること"

    def test_every_card_belongs_to_a_known_group(self):
        for card in self._cards():
            assert card.group in home_status_service.CARD_GROUP_LABELS, card.title

    def test_groups_are_contiguous(self):
        """同じグループのカードが離れて並んでいると、見出しが2回出てしまう。"""
        seen = []
        for group, _cards in home_status_service.group_cards(self._cards()):
            assert group not in seen, f"{group} のカードが離れて並んでいる"
            seen.append(group)

    def test_headings_are_rendered_once_per_group(self):
        html_out = home_status_service.render_status_grid_html(self._cards())
        for label in home_status_service.CARD_GROUP_LABELS.values():
            assert html_out.count(f">{label}</h2>") == 1, label

    def test_a_lone_card_does_not_stretch_across_the_row(self):
        """1枚だけのグループが行いっぱいに伸びると、上下のグループの
        2列のリズムから外れて間延びして見える。"""
        cards = [
            home_status_service.StatusCard("A", "1", "theme-green", group="watch"),
            home_status_service.StatusCard("B", "2", "theme-green", group="watch"),
            home_status_service.StatusCard("C", "3", "theme-blue", group="solo"),
        ]
        html_out = home_status_service.render_status_grid_html(cards)

        assert "status-grid status-grid-solo" in html_out, "1枚のグループに目印が付いていない"
        assert html_out.count("status-grid-solo") == 1, "2枚以上のグループにも付いている"
        assert ".status-grid-solo > .status-card" in home_status_service.STATUS_CARD_CSS

    def test_cards_without_a_group_are_still_rendered(self):
        """`group` を付け忘れたカードが表示から漏れないこと。"""
        cards = [home_status_service.StatusCard("🔧 テスト", "v", "theme-green")]
        html_out = home_status_service.render_status_grid_html(cards)
        assert "status-card" in html_out
        assert "group-title" not in html_out


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


class TestGetParkingStatus:
    """駐車場カメラの動体検知を流用する(#829。car_recordsは書き込み経路が
    存在せず恒常的に空だったため廃止)。人の往来も拾うため在宅/外出中は
    断定できず、カメラカードと同じく常に情報色(青)かグレー。"""

    def _df(self, rows):
        df = pd.DataFrame(rows, columns=["timestamp", "device_type", "movement_state", "device_id"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df

    def _row(self, minutes_ago, device_id=home_status_service.PARKING_CAMERA_ID):
        return {
            "timestamp": NOW - timedelta(minutes=minutes_ago),
            "device_type": home_status_service.CAMERA_DEVICE_TYPE,
            "movement_state": "ON",
            "device_id": device_id,
        }

    def test_empty_is_no_data(self):
        assert home_status_service.get_parking_status(pd.DataFrame(), NOW) == ("⚪ データなし", "theme-gray")

    def test_no_data_ever_recorded_is_not_reported_as_home(self):
        """以前の car_records 依存版は空データでも「🏠 在宅」を返しており、
        `データが無い`と`本当に在宅`を区別できていなかった(#829)。"""
        val, theme = home_status_service.get_parking_status(pd.DataFrame(), NOW)
        assert val != "🏠 在宅"
        assert theme == "theme-gray"

    def test_other_cameras_are_ignored(self):
        """表示名(name)ではなくidで照合する(devices.jsonで表示名を変えても壊れないため)。"""
        df = self._df([self._row(3, device_id="NC-9820_Entrance")])
        assert home_status_service.get_parking_status(df, NOW) == ("⚪ データなし", "theme-gray")

    def test_just_now_is_reported_as_moving(self):
        val, theme = home_status_service.get_parking_status(self._df([self._row(3)]), NOW)
        assert val == "🚗 いま動きあり"
        assert theme == "theme-blue"

    def test_detection_never_raises_an_alert(self):
        for minutes in (1, 30, 300, 60 * 48):
            _, theme = home_status_service.get_parking_status(self._df([self._row(minutes)]), NOW)
            assert theme in ("theme-blue", "theme-gray"), f"{minutes}分前が警告色になっている"

    def test_describe_reports_last_detection_time(self):
        df = self._df([self._row(5)])
        assert home_status_service.describe_parking(df, NOW) == "最終検知 " + (NOW - timedelta(minutes=5)).strftime("%H:%M")


class TestBuildStatusCards:
    def test_renders_all_status_cards(self):
        df_sensor = _sensor_df([_row(location="高砂", contact_state="detected")])

        cards = home_status_service.build_status_cards(
            NOW, df_sensor, None, {"percent": 50}, 4321,
        )

        assert len(cards) == 7
        titles = [c.title for c in cards]
        assert titles[0] == "👵 高砂 (実家)"
        assert "💰 今月の電気代" in titles
        # 電気代は3桁区切りで整形される
        assert any(c.value == "⚡ 4,321 円" for c in cards)
        # 退役したカード(駐輪場・JR運行情報・ファミクエ・炊飯器)が復活していないこと
        assert not any("駐輪場" in t or "JR" in t or "承認待ち" in t or "炊飯器" in t for t in titles)

    def test_single_card_html_has_no_formatting_whitespace(self):
        card_html = home_status_service.render_status_card_html("タイトル", "値", "theme-green")
        assert "\n" not in card_html
        assert card_html.startswith('<div class="status-card theme-green">')
        assert card_html.endswith("</div>")
