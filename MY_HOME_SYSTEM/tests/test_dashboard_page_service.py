# MY_HOME_SYSTEM/tests/test_dashboard_page_service.py
"""services/dashboard_page_service.py の見守り/くらしページ組み立てのテスト。

NAS(config.ASSETS_DIR)には一切触れず、tmp_pathへ差し替える。
"""
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
import pytz

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import dashboard_page_service

_JST = pytz.timezone("Asia/Tokyo")


def _jst(*args) -> datetime:
    return _JST.localize(datetime(*args))  # noqa: DTZ001 -- localize()で直後にtz付与する


@pytest.fixture(autouse=True)
def _isolate_assets_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASSETS_DIR", str(tmp_path / "assets"))


class TestListSnapshotFiles:
    def test_returns_jpg_filenames_newest_first(self, tmp_path):
        snap_dir = os.path.join(config.ASSETS_DIR, "snapshots")
        os.makedirs(snap_dir)
        for name in ("a.jpg", "b.jpg"):
            with open(os.path.join(snap_dir, name), "w") as f:
                f.write("x")
        files = dashboard_page_service._list_snapshot_files()
        assert set(files) == {"a.jpg", "b.jpg"}

    def test_missing_directory_returns_empty_list(self):
        assert dashboard_page_service._list_snapshot_files() == []

    def test_oserror_is_swallowed(self, monkeypatch):
        def _raise(*args, **kwargs):
            raise OSError("NAS unreachable")

        monkeypatch.setattr(dashboard_page_service.glob, "glob", _raise)
        assert dashboard_page_service._list_snapshot_files() == []


class TestResolveSnapshotPath:
    def test_existing_file_resolves(self):
        snap_dir = os.path.join(config.ASSETS_DIR, "snapshots")
        os.makedirs(snap_dir)
        target = os.path.join(snap_dir, "photo.jpg")
        with open(target, "w") as f:
            f.write("x")
        assert dashboard_page_service.resolve_snapshot_path("photo.jpg") == os.path.realpath(target)

    def test_missing_file_returns_none(self):
        os.makedirs(os.path.join(config.ASSETS_DIR, "snapshots"))
        assert dashboard_page_service.resolve_snapshot_path("missing.jpg") is None

    def test_path_traversal_is_rejected(self):
        os.makedirs(os.path.join(config.ASSETS_DIR, "snapshots"))
        with open(os.path.join(config.ASSETS_DIR, "secret.txt"), "w") as f:
            f.write("x")
        assert dashboard_page_service.resolve_snapshot_path("../secret.txt") is None

    def test_oserror_while_resolving_base_dir_returns_none(self, monkeypatch):
        def _raise(*args, **kwargs):
            raise OSError("NAS unreachable")

        monkeypatch.setattr(dashboard_page_service.os.path, "realpath", _raise)
        assert dashboard_page_service.resolve_snapshot_path("photo.jpg") is None


class TestRenderSnapshotGallery:
    def test_empty_shows_placeholder(self):
        html = dashboard_page_service._render_snapshot_gallery("/dashboard/snapshot")
        assert "写真なし" in html

    def test_non_empty_renders_img_tags(self):
        snap_dir = os.path.join(config.ASSETS_DIR, "snapshots")
        os.makedirs(snap_dir)
        with open(os.path.join(snap_dir, "photo.jpg"), "w") as f:
            f.write("x")
        html = dashboard_page_service._render_snapshot_gallery("/dashboard/snapshot")
        assert "<img" in html
        assert "/dashboard/snapshot/photo.jpg" in html

    def test_image_is_wrapped_in_a_link_to_enlarge_it(self):
        """不具合修正: 以前は<img>のみで、タップしても拡大表示できなかった。"""
        snap_dir = os.path.join(config.ASSETS_DIR, "snapshots")
        os.makedirs(snap_dir)
        with open(os.path.join(snap_dir, "photo.jpg"), "w") as f:
            f.write("x")
        html = dashboard_page_service._render_snapshot_gallery("/dashboard/snapshot")
        assert '<a class="snapshot-item" href="/dashboard/snapshot/photo.jpg"' in html
        assert 'target="_blank"' in html

    def test_shows_capture_time_parsed_from_filename(self):
        """不具合修正: 以前は撮影日時がどこにも表示されなかった。"""
        snap_dir = os.path.join(config.ASSETS_DIR, "snapshots")
        os.makedirs(snap_dir)
        with open(os.path.join(snap_dir, "玄関_motion_20260115_083045.jpg"), "w") as f:
            f.write("x")
        html = dashboard_page_service._render_snapshot_gallery("/dashboard/snapshot")
        assert "01/15 08:30" in html

    def test_unparseable_filename_shows_no_caption_but_still_renders(self):
        snap_dir = os.path.join(config.ASSETS_DIR, "snapshots")
        os.makedirs(snap_dir)
        with open(os.path.join(snap_dir, "legacy.jpg"), "w") as f:
            f.write("x")
        html = dashboard_page_service._render_snapshot_gallery("/dashboard/snapshot")
        assert "snapshot-caption" not in html
        assert "<img" in html


class TestSnapshotTimestamp:
    def test_parses_the_trailing_timestamp(self):
        moment = dashboard_page_service._snapshot_timestamp("駐車場_motion_20260115_083045.jpg")
        assert moment is not None
        assert moment.strftime("%Y-%m-%d %H:%M:%S") == "2026-01-15 08:30:45"

    def test_camera_name_containing_underscores_does_not_confuse_parsing(self):
        moment = dashboard_page_service._snapshot_timestamp("entrance_cam_1_motion_20260115_083045.jpg")
        assert moment is not None

    def test_unrecognized_format_returns_none(self):
        assert dashboard_page_service._snapshot_timestamp("legacy.jpg") is None


class TestRenderSimpleTable:
    def test_empty_dataframe_shows_placeholder(self):
        html = dashboard_page_service._render_simple_table(pd.DataFrame(), {"timestamp": "時刻"})
        assert "表示できるデータがありません" in html

    def test_renders_rows_with_relative_timestamp(self):
        df = pd.DataFrame({
            "timestamp": [pd.Timestamp("2026-01-01 00:00:00")],
            "friendly_name": ["玄関"],
        })
        html = dashboard_page_service._render_simple_table(
            df, {"timestamp": "時刻", "friendly_name": "デバイス"}
        )
        assert "<table" in html
        assert "玄関" in html


class TestRenderCollapsibleLogTable:
    """UI改善: 防犯ログ・センサーログが縦に長く連なりスクロールが大変だった問題の改善。"""

    def _df(self, n: int) -> pd.DataFrame:
        return pd.DataFrame({
            "timestamp": [pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i) for i in range(n)],
            "friendly_name": [f"デバイス{i}" for i in range(n)],
        })

    def test_empty_dataframe_shows_placeholder(self):
        html = dashboard_page_service._render_collapsible_log_table(pd.DataFrame(), {"timestamp": "時刻"})
        assert "表示できるデータがありません" in html

    def test_few_rows_show_no_details_disclosure(self):
        """行数が常時表示件数以下なら、折りたたみ自体が不要。"""
        html = dashboard_page_service._render_collapsible_log_table(
            self._df(3), {"timestamp": "時刻", "friendly_name": "デバイス"}, visible=5
        )
        assert "<details>" not in html
        assert "デバイス0" in html
        assert "デバイス2" in html

    def test_extra_rows_are_collapsed_behind_details(self):
        html = dashboard_page_service._render_collapsible_log_table(
            self._df(8), {"timestamp": "時刻", "friendly_name": "デバイス"}, visible=5
        )
        assert "<details>" in html
        assert "さらに3件を表示" in html
        # 常時表示分(0〜4)は<details>の外、残り(5〜7)は<details>で折りたたまれる
        visible_part = html.split("<details>", 1)[0]
        collapsed_part = html.split("<details>", 1)[1]
        assert "デバイス0" in visible_part
        assert "デバイス4" in visible_part
        assert "デバイス5" not in visible_part
        assert "デバイス7" in collapsed_part


class TestRenderCameraSelector:
    def test_no_cameras_shows_placeholder(self):
        html = dashboard_page_service._render_camera_selector([])
        assert "カメラが登録されていません" in html

    def test_renders_a_button_per_camera(self):
        html = dashboard_page_service._render_camera_selector(
            [{"id": "entrance", "name": "玄関"}, {"id": "parking", "name": "駐車場"}]
        )
        assert html.count("camera-btn") == 2
        assert "玄関" in html
        assert "駐車場" in html


class TestRenderWatchPage:
    def test_renders_without_error_when_data_is_empty(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [{"id": "entrance", "name": "玄関", "enabled": True}])
        html = dashboard_page_service.render_watch_page(
            pd.DataFrame(),
            dashboard_path="/dashboard",
            snapshot_url_prefix="/dashboard/snapshot",
        )
        assert "見守り" in html
        assert "玄関" in html

    def test_disabled_cameras_are_excluded(self, monkeypatch):
        monkeypatch.setattr(
            config,
            "CAMERAS",
            [
                {"id": "entrance", "name": "玄関", "enabled": True},
                {"id": "garden", "name": "庭", "enabled": False},
            ],
        )
        html = dashboard_page_service.render_watch_page(
            pd.DataFrame(),
            dashboard_path="/dashboard",
            snapshot_url_prefix="/dashboard/snapshot",
        )
        assert "玄関" in html
        assert "庭" not in html

    def test_sensor_rows_are_split_into_takasago_and_itami_sections(self, monkeypatch):
        """不具合修正: 以前は伊丹のセンサーログがどこにも表示されず、高砂だけの
        表だった。今は高砂/伊丹それぞれ専用のセクション(`#takasago-log`/
        `#itami-log`)に分けて表示し、見守りカードのタップ先として使う。"""
        monkeypatch.setattr(config, "CAMERAS", [])
        df_sensor = pd.DataFrame({
            "location": ["高砂", "伊丹"],
            "timestamp": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-01")],
            "friendly_name": ["センサーA", "センサーB"],
            "contact_state": ["OPEN", "CLOSE"],
        })
        html = dashboard_page_service.render_watch_page(
            df_sensor,
            dashboard_path="/dashboard",
            snapshot_url_prefix="/dashboard/snapshot",
        )

        takasago_section = html.split('id="takasago-log"', 1)[1].split('id="itami-log"', 1)[0]
        itami_section = html.split('id="itami-log"', 1)[1]

        assert "センサーA" in takasago_section
        assert "センサーB" not in takasago_section
        assert "センサーB" in itami_section
        assert "センサーA" not in itami_section

    def test_security_log_shows_camera_motion_detections(self, monkeypatch):
        """不具合修正: 防犯ログは以前`security_logs`テーブル(書き込むコードが無く
        常に空)を読んでおり、常に「表示できるデータがありません」だった。
        カメラの動体検知(device_records)を正のデータとして表示する。"""
        monkeypatch.setattr(config, "CAMERAS", [])
        df_sensor = pd.DataFrame({
            "device_type": ["ONVIF_CAMERA"],
            "movement_state": ["ON"],
            "timestamp": [pd.Timestamp("2026-01-01 10:00:00")],
            "friendly_name": ["駐車場カメラ"],
        })
        html = dashboard_page_service.render_watch_page(
            df_sensor,
            dashboard_path="/dashboard",
            snapshot_url_prefix="/dashboard/snapshot",
        )

        security_section = html.split("防犯ログ", 1)[1].split("高砂実家のセンサーログ", 1)[0]
        assert "駐車場カメラ" in security_section
        assert "表示できるデータがありません" not in security_section


class TestBuildFreshnessRows:
    def test_missing_data_is_flagged_red_when_threshold_set(self):
        rows = dashboard_page_service.build_freshness_rows(
            pd.DataFrame(), None, None, _jst(2026, 1, 1)
        )
        electric_row = next(r for r in rows if r["label"] == "⚡ 電気・環境の見守り")
        assert electric_row["state"] == "red"
        server_row = next(r for r in rows if r["label"] == "🖥️ サーバー本体")
        assert server_row["state"] == "red"

    def test_no_matching_device_type_is_treated_as_missing(self):
        rows = dashboard_page_service.build_freshness_rows(
            pd.DataFrame({"device_type": ["Camera"], "timestamp": [_jst(2026, 1, 1)]}),
            None,
            None,
            _jst(2026, 1, 1),
        )
        electric_row = next(r for r in rows if r["label"] == "⚡ 電気・環境の見守り")
        assert electric_row["state"] == "red"

    def test_stale_update_beyond_threshold_is_red(self):
        now = _jst(2026, 1, 1, 12, 0, 0)
        df_sensor = pd.DataFrame({
            "device_type": ["Plug"],
            "timestamp": [now - timedelta(hours=2)],
        })
        rows = dashboard_page_service.build_freshness_rows(df_sensor, None, {"percent": 10}, now)
        electric_row = next(r for r in rows if r["label"] == "⚡ 電気・環境の見守り")
        assert electric_row["state"] == "red"
        assert "更新が止まっています" in electric_row["text"]
        server_row = next(r for r in rows if r["label"] == "🖥️ サーバー本体")
        assert server_row["state"] == "ok"

    def test_recent_update_within_threshold_is_ok(self):
        now = _jst(2026, 1, 1, 12, 0, 0)
        df_sensor = pd.DataFrame({
            "device_type": ["Plug"],
            "timestamp": [now - timedelta(minutes=5)],
        })
        rows = dashboard_page_service.build_freshness_rows(df_sensor, None, {"percent": 10}, now)
        electric_row = next(r for r in rows if r["label"] == "⚡ 電気・環境の見守り")
        assert electric_row["state"] == "ok"

    def test_nas_timestamp_is_parsed_from_series(self):
        now = _jst(2026, 1, 1, 12, 0, 0)
        nas_data = pd.Series({"timestamp": now - timedelta(minutes=1)})
        rows = dashboard_page_service.build_freshness_rows(pd.DataFrame(), nas_data, None, now)
        nas_row = next(r for r in rows if r["label"] == "🗄️ 保存装置(NAS)")
        assert nas_row["state"] == "ok"

    def test_unparseable_nas_timestamp_is_treated_as_missing(self):
        now = _jst(2026, 1, 1, 12, 0, 0)
        nas_data = pd.Series({"timestamp": "not-a-timestamp"})
        rows = dashboard_page_service.build_freshness_rows(pd.DataFrame(), nas_data, None, now)
        nas_row = next(r for r in rows if r["label"] == "🗄️ 保存装置(NAS)")
        assert nas_row["state"] == "red"

    def test_missing_timestamp_key_does_not_raise(self):
        now = _jst(2026, 1, 1, 12, 0, 0)
        nas_data = pd.Series({"other": 1})
        rows = dashboard_page_service.build_freshness_rows(pd.DataFrame(), nas_data, None, now)
        nas_row = next(r for r in rows if r["label"] == "🗄️ 保存装置(NAS)")
        assert nas_row["state"] == "red"


class TestRenderOverallSummary:
    def test_all_ok_shows_positive_message(self):
        html = dashboard_page_service._render_overall_summary(
            [{"label": "a", "text": "ok", "state": "ok"}]
        )
        assert "すべて正常です" in html

    def test_red_rows_are_counted(self):
        html = dashboard_page_service._render_overall_summary(
            [{"label": "a", "text": "x", "state": "red"}, {"label": "b", "text": "y", "state": "ok"}]
        )
        assert "1件、確認が必要です" in html


class TestRenderLifePage:
    def test_renders_only_life_group_cards(self):
        cards = [
            dashboard_page_service.home_status_service.StatusCard(
                title="⚡ 電気", value="1,200円", theme="ok", group="life"
            ),
            dashboard_page_service.home_status_service.StatusCard(
                title="🚗 駐車場", value="動きあり", theme="info", group="watch"
            ),
        ]
        html = dashboard_page_service.render_life_page(cards, dashboard_path="/dashboard")
        assert "電気" in html
        assert "駐車場" not in html

    def test_includes_daily_cost_history_when_provided(self):
        """不具合修正: 電気代カードのタップ先に、日別推移という意味のある詳細を出す。"""
        rows = [(date(2026, 9, 15), 300), (date(2026, 9, 14), 150)]
        html = dashboard_page_service.render_life_page(
            [], dashboard_path="/dashboard", daily_cost_rows=rows
        )
        assert "日別の電気代" in html
        assert "300円" in html
        assert "150円" in html

    def test_omitting_daily_cost_rows_shows_the_empty_placeholder(self):
        html = dashboard_page_service.render_life_page([], dashboard_path="/dashboard")
        assert "表示できるデータがありません" in html


class TestRenderDailyCostHistory:
    def test_empty_rows_shows_placeholder(self):
        html = dashboard_page_service._render_daily_cost_history([])
        assert "表示できるデータがありません" in html

    def test_first_row_is_labeled_today(self):
        html = dashboard_page_service._render_daily_cost_history([(date(2026, 9, 15), 300)])
        assert "今日" in html
        assert "09/15" not in html

    def test_other_rows_are_labeled_by_date(self):
        html = dashboard_page_service._render_daily_cost_history(
            [(date(2026, 9, 15), 300), (date(2026, 9, 14), 150)]
        )
        assert "09/14" in html

    def test_bar_width_is_relative_to_the_maximum(self):
        html = dashboard_page_service._render_daily_cost_history(
            [(date(2026, 9, 15), 100), (date(2026, 9, 14), 50)]
        )
        assert "width:100%" in html
        assert "width:50%" in html

    def test_zero_cost_day_still_shows_a_visible_sliver(self):
        """幅0%だとバー自体が見えなくなり「データが無い」ように見えてしまうため、
        最小幅を確保する。"""
        html = dashboard_page_service._render_daily_cost_history(
            [(date(2026, 9, 15), 0), (date(2026, 9, 14), 100)]
        )
        assert "width:2%" in html


class TestRenderNasHistoryChart:
    """NASカードのタップ先(容量推移グラフ)。不具合修正で新設。"""

    def test_empty_dataframe_shows_placeholder(self):
        html = dashboard_page_service._render_nas_history_chart(pd.DataFrame())
        assert "表示できるデータがありません" in html

    def test_single_row_shows_placeholder(self):
        """折れ線を引くには最低2点必要。"""
        df = pd.DataFrame({"timestamp": [pd.Timestamp("2026-09-15")], "percent": [42.0], "free_gb": [100]})
        html = dashboard_page_service._render_nas_history_chart(df)
        assert "表示できるデータがありません" in html

    def test_renders_svg_line_and_current_value(self):
        df = pd.DataFrame({
            "timestamp": [pd.Timestamp("2026-09-14"), pd.Timestamp("2026-09-15")],
            "percent": [30.0, 42.0],
            "free_gb": [120, 100],
        })
        html = dashboard_page_service._render_nas_history_chart(df)
        assert "<svg" in html
        assert "<polyline" in html
        assert "現在 42%" in html

    def test_includes_a_collapsible_detail_table(self):
        """グラフだけでは正確な値が読み取れないため、詳細テーブルを併設する。"""
        df = pd.DataFrame({
            "timestamp": [pd.Timestamp("2026-09-14"), pd.Timestamp("2026-09-15")],
            "percent": [30.0, 42.0],
            "free_gb": [120, 100],
        })
        html = dashboard_page_service._render_nas_history_chart(df)
        assert "<details>" in html
        assert "詳細データを見る" in html
        assert "<table" in html
