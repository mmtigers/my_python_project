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

    def test_renders_a_video_tile_per_camera_all_at_once(self):
        """全台を初期表示から同時に並べる(切替ボタンは無い)。"""
        html = dashboard_page_service._render_camera_selector(
            [{"id": "entrance", "name": "玄関"}, {"id": "garden", "name": "庭"}, {"id": "parking", "name": "駐車場"}]
        )
        assert html.count('class="camera-video"') == 3
        for cid in ("entrance", "garden", "parking"):
            assert f'data-camera-id="{cid}"' in html
        assert "camera-btn" not in html
        assert "玄関" in html and "庭" in html and "駐車場" in html


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


class TestWatchPageLogDateFilter:
    def _sensor_rows(self, count: int) -> pd.DataFrame:
        return pd.DataFrame({
            "location": ["高砂"] * count,
            "device_id": [f"dev{i}" for i in range(count)],
            "timestamp": [pd.Timestamp("2026-10-01 10:00:00") + pd.Timedelta(minutes=i) for i in range(count)],
            "friendly_name": [f"センサー{i}" for i in range(count)],
            "contact_state": ["OPEN"] * count,
        })

    def _render(self, df, selected_date=None):
        return dashboard_page_service.render_watch_page(
            df,
            dashboard_path="/dashboard/",
            snapshot_url_prefix="/dashboard/snapshot",
            selected_date=selected_date,
        )

    def test_filter_form_is_shown_without_selection(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [])
        html = self._render(pd.DataFrame())
        assert 'type="date"' in html
        assert 'action="/dashboard/watch#log-filter"' in html
        assert 'class="log-filter-clear"' not in html

    def test_selected_date_is_prefilled_and_clear_link_shown(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [])
        html = self._render(pd.DataFrame(), date(2026, 10, 1))
        assert 'value="2026-10-01"' in html
        assert 'href="/dashboard/watch#log-filter"' in html
        assert "2026/10/01 のログを全件表示" in html

    def test_without_selection_logs_are_capped_at_50_rows(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [])
        html = self._render(self._sensor_rows(60))
        assert "さらに45件を表示" in html

    def test_with_selection_all_rows_of_the_day_are_shown(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [])
        html = self._render(self._sensor_rows(60), date(2026, 10, 1))
        assert "さらに55件を表示" in html
        assert "センサー0" in html and "センサー59" in html


class TestParseLogDate:
    def test_valid_date(self):
        assert dashboard_page_service.parse_log_date("2026-10-01") == date(2026, 10, 1)

    @pytest.mark.parametrize("value", [None, "", "abc", "2026-13-01", "20261001'; DROP TABLE x;--"])
    def test_invalid_or_missing_is_none(self, value):
        assert dashboard_page_service.parse_log_date(value) is None


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


class TestNasChartImprovements:
    """NAS容量グラフ: 実測レンジの縦軸・時刻軸・増加ペースと満杯予測・期間切替・間引き。"""

    @staticmethod
    def _history(days, per_day_gb, total=1000, start_used=500, step_hours=1):
        n = int(days * 24 / step_hours) + 1
        ts = [pd.Timestamp("2026-09-01") + pd.Timedelta(hours=i * step_hours) for i in range(n)]
        used = [start_used + per_day_gb * (i * step_hours / 24) for i in range(n)]
        return pd.DataFrame({
            "timestamp": ts,
            "used_gb": used,
            "free_gb": [total - u for u in used],
            "percent": [u / total * 100 for u in used],
        })

    def test_y_axis_follows_measured_range_not_0_to_100(self):
        html = dashboard_page_service._render_nas_history_chart(self._history(10, 1.0))
        # 使用率はおよそ50〜51%。0%や100%の目盛りは出ない。
        assert ">0%<" not in html and ">100%<" not in html
        assert "%</text>" in html

    def test_points_are_positioned_by_time_not_by_index(self):
        df = pd.DataFrame({
            "timestamp": [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02"), pd.Timestamp("2026-09-10")],
            "percent": [50.0, 51.0, 60.0],
            "free_gb": [500, 490, 400],
        })
        html = dashboard_page_service._render_nas_history_chart(df)
        xs = [float(p.split(",")[0]) for p in html.split('points="')[1].split('"')[0].split()]
        # 1日目→2日目の間隔は、2日目→10日目(8日)の1/8
        assert (xs[1] - xs[0]) * 8 == pytest.approx(xs[2] - xs[1], rel=0.01)

    def test_forecast_shows_growth_rate_and_days_to_full(self):
        df = self._history(10, 5.0, total=1000, start_used=500)  # 5GB/日、終点で空き450GB
        forecast = dashboard_page_service.compute_nas_forecast(df)
        assert forecast["per_day"] == pytest.approx(5.0)
        assert forecast["days_to_full"] == pytest.approx(90.0, rel=0.01)
        html = dashboard_page_service._render_nas_history_chart(df)
        assert "+5.0GB/日" in html and "約90日後" in html
        assert "空き 450GB" in html

    def test_no_growth_has_no_days_to_full(self):
        forecast = dashboard_page_service.compute_nas_forecast(self._history(10, -2.0))
        assert forecast["per_day"] < 0 and forecast["days_to_full"] is None
        assert "増えていません" in dashboard_page_service._render_nas_history_chart(self._history(10, -2.0))

    def test_forecast_needs_at_least_two_days(self):
        assert dashboard_page_service.compute_nas_forecast(self._history(1, 5.0)) is None
        assert "データ不足" in dashboard_page_service._render_nas_history_chart(self._history(1, 5.0))

    def test_forecast_is_skipped_without_used_gb(self):
        df = pd.DataFrame({
            "timestamp": [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-09")],
            "percent": [50.0, 52.0], "free_gb": [500, 480],
        })
        assert dashboard_page_service.compute_nas_forecast(df) is None

    def test_many_points_are_thinned(self):
        html = dashboard_page_service._render_nas_history_chart(self._history(90, 1.0))  # 2161点
        n_points = len(html.split('points="')[1].split('"')[0].split())
        assert n_points <= dashboard_page_service._NAS_CHART_MAX_POINTS

    def test_flat_usage_still_gets_a_minimum_span(self):
        df = pd.DataFrame({
            "timestamp": [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-05")],
            "percent": [100.0, 100.0], "free_gb": [0, 0],
        })
        html = dashboard_page_service._render_nas_history_chart(df)
        assert "<polyline" in html

    def test_period_links_mark_the_current_period(self):
        html = dashboard_page_service._render_nas_history_chart(
            self._history(10, 1.0), days=30, sys_path="/dashboard/sys"
        )
        assert 'href="/dashboard/sys?nas_days=7#nas-history"' in html
        assert 'href="/dashboard/sys?nas_days=90#nas-history"' in html
        assert 'href="/dashboard/sys?nas_days=30#nas-history"' not in html
        assert '<span class="nas-period-current">30日</span>' in html


class TestSensorDeviceFilter:
    """見守りページのセンサーログを機器ごとに絞る(?device=)。"""

    @staticmethod
    def _df():
        rows = []
        for i, (dev, name, loc) in enumerate([("A", "伊丹のリビング", "伊丹"), ("B", "伊丹の書斎", "伊丹"), ("C", "高砂の玄関", "高砂")]):
            rows.append({
                "device_id": dev, "friendly_name": name, "location": loc,
                "timestamp": pd.Timestamp("2026-10-01 10:00:00") + pd.Timedelta(minutes=i),
                "contact_state": "open", "movement_state": None,
            })
        return pd.DataFrame(rows)

    def _render(self, device=None, monkeypatch=None):
        return dashboard_page_service.render_watch_page(
            self._df(), dashboard_path="/dashboard/", snapshot_url_prefix="/dashboard/snapshot",
            selected_device=device,
        )

    def test_select_lists_all_devices(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [])
        html = self._render()
        assert 'name="device"' in html
        for name in ("伊丹のリビング", "伊丹の書斎", "高砂の玄関"):
            assert f">{name}</option>" in html
        assert "すべての機器" in html

    def test_selected_device_limits_the_sensor_logs(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [])
        html = self._render("A")
        log_part = html.split('id="takasago-log"', 1)[1]
        assert "伊丹のリビング" in log_part
        assert "伊丹の書斎" not in log_part and "高砂の玄関" not in log_part
        assert '<option value="A" selected>' in html
        assert 'class="log-filter-clear"' in html

    def test_unknown_device_is_ignored(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [])
        html = self._render("ZZZ")
        log_part = html.split('id="takasago-log"', 1)[1]
        assert "伊丹のリビング" in log_part and "高砂の玄関" in log_part
        assert 'class="log-filter-clear"' not in html

    def test_options_come_from_unfiltered_devices(self, monkeypatch):
        """絞り込み後も、他の機器へ選び直せること。"""
        monkeypatch.setattr(config, "CAMERAS", [])
        html = self._render("A")
        assert ">高砂の玄関</option>" in html


class TestMergeOpenCloseEvents:
    """開閉センサーの「open」の直後(60秒以内)の「close」を、開いた時刻の1行にまとめる。"""

    @staticmethod
    def _df(rows):
        return pd.DataFrame(
            rows, columns=["timestamp", "device_id", "contact_state", "movement_state"]
        )

    def _merge(self, rows):
        return dashboard_page_service.merge_open_close_events(self._df(rows))

    def test_open_then_close_within_a_minute_becomes_one_row_at_open_time(self):
        out = self._merge([
            (_jst(2026, 10, 2, 6, 24, 12), "door", "close", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "open", None),
        ])
        assert len(out) == 1
        row = out.iloc[0]
        assert row["timestamp"] == _jst(2026, 10, 2, 6, 24, 0)
        assert row["contact_state"] == "開 → 閉（12秒）"

    def test_exactly_60_seconds_is_merged(self):
        out = self._merge([
            (_jst(2026, 10, 2, 6, 25, 0), "door", "close", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "open", None),
        ])
        assert list(out["contact_state"]) == ["開 → 閉（60秒）"]

    def test_over_60_seconds_is_not_merged(self):
        out = self._merge([
            (_jst(2026, 10, 2, 6, 25, 1), "door", "close", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "open", None),
        ])
        assert list(out["contact_state"]) == ["close", "open"]

    def test_close_then_open_is_not_merged(self):
        out = self._merge([
            (_jst(2026, 10, 2, 6, 24, 10), "door", "open", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "close", None),
        ])
        assert list(out["contact_state"]) == ["open", "close"]

    def test_timeoutnotclose_and_detected_are_left_alone(self):
        out = self._merge([
            (_jst(2026, 10, 2, 6, 24, 30), "door", "close", None),
            (_jst(2026, 10, 2, 6, 24, 20), "door", "timeoutnotclose", None),
            (_jst(2026, 10, 2, 6, 24, 10), "pir", "detected", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "open", None),
        ])
        assert len(out) == 4

    def test_other_device_in_between_does_not_block_merge(self):
        out = self._merge([
            (_jst(2026, 10, 2, 6, 24, 20), "door", "close", None),
            (_jst(2026, 10, 2, 6, 24, 10), "window", "open", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "open", None),
        ])
        by_device = {r["device_id"]: r["contact_state"] for _, r in out.iterrows()}
        assert by_device == {"door": "開 → 閉（20秒）", "window": "open"}

    def test_pairs_of_different_devices_are_not_mixed(self):
        """door が open、window が close でも、別機器同士はまとめない。"""
        out = self._merge([
            (_jst(2026, 10, 2, 6, 24, 10), "window", "close", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "open", None),
        ])
        assert len(out) == 2

    def test_multiple_visits_are_merged_independently_and_sorted_newest_first(self):
        out = self._merge([
            (_jst(2026, 10, 2, 9, 0, 5), "door", "close", None),
            (_jst(2026, 10, 2, 9, 0, 0), "door", "open", None),
            (_jst(2026, 10, 2, 6, 24, 30), "door", "close", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "open", None),
        ])
        assert list(out["contact_state"]) == ["開 → 閉（5秒）", "開 → 閉（30秒）"]
        assert list(out["timestamp"]) == [_jst(2026, 10, 2, 9, 0, 0), _jst(2026, 10, 2, 6, 24, 0)]

    def test_rows_with_movement_state_are_not_merged(self):
        out = self._merge([
            (_jst(2026, 10, 2, 6, 24, 10), "door", "close", "detected"),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "open", None),
        ])
        assert len(out) == 2

    def test_case_and_whitespace_insensitive(self):
        out = self._merge([
            (_jst(2026, 10, 2, 6, 24, 10), "door", " CLOSE ", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "Open", None),
        ])
        assert list(out["contact_state"]) == ["開 → 閉（10秒）"]

    def test_non_datetime_timestamp_or_missing_columns_are_returned_unchanged(self):
        df = pd.DataFrame({"timestamp": [2, 1], "device_id": ["d", "d"], "contact_state": ["close", "open"]})
        assert dashboard_page_service.merge_open_close_events(df) is df
        assert dashboard_page_service.merge_open_close_events(pd.DataFrame()).empty
        no_device = pd.DataFrame({"timestamp": [_jst(2026, 10, 2, 6, 24, 10), _jst(2026, 10, 2, 6, 24, 0)],
                                  "contact_state": ["close", "open"]})
        assert dashboard_page_service.merge_open_close_events(no_device) is no_device

    def test_does_not_modify_the_input_dataframe(self):
        df = self._df([
            (_jst(2026, 10, 2, 6, 24, 12), "door", "close", None),
            (_jst(2026, 10, 2, 6, 24, 0), "door", "open", None),
        ])
        before = df.copy()
        dashboard_page_service.merge_open_close_events(df)
        pd.testing.assert_frame_equal(df, before)


class TestWatchPageMergesOpenClose:
    def test_watch_page_shows_one_row_for_open_close_pair(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [])
        df_sensor = pd.DataFrame({
            "location": ["高砂", "高砂"],
            "device_id": ["door", "door"],
            "timestamp": [_jst(2026, 10, 2, 6, 24, 12), _jst(2026, 10, 2, 6, 24, 0)],
            "friendly_name": ["玄関ドア", "玄関ドア"],
            "contact_state": ["close", "open"],
        })
        html = dashboard_page_service.render_watch_page(
            df_sensor, dashboard_path="/dashboard/", snapshot_url_prefix="/dashboard/snapshot"
        )
        takasago = html.split('id="takasago-log"', 1)[1].split('id="itami-log"', 1)[0]
        assert takasago.count("玄関ドア") == 1
        assert "開 → 閉（12秒）" in takasago


class TestSensorStateChangeLog:
    """見守りページのセンサーログを「開閉/動体の状態変化」だけに絞る(ノイズ軽減)。"""

    @staticmethod
    def _df(rows):
        return pd.DataFrame(
            rows, columns=["timestamp", "device_id", "contact_state", "movement_state"]
        )

    def test_drops_meter_and_power_rows_without_contact_or_movement_state(self):
        df = self._df([
            (_jst(2026, 10, 2, 10, 5), "meter", None, None),
            (_jst(2026, 10, 2, 10, 0), "door", "open", None),
            (_jst(2026, 10, 2, 9, 55), "plug", None, None),
        ])
        out = dashboard_page_service.sensor_state_change_log(df)
        assert list(out["device_id"]) == ["door"]

    def test_collapses_consecutive_identical_states_per_device(self):
        df = self._df([
            (_jst(2026, 10, 2, 10, 20), "door", "closed", None),
            (_jst(2026, 10, 2, 10, 15), "door", "open", None),
            (_jst(2026, 10, 2, 10, 10), "door", "open", None),
            (_jst(2026, 10, 2, 10, 5), "door", "open", None),
        ])
        out = dashboard_page_service.sensor_state_change_log(df)
        # 最初のopen(10:05)と、open->closedの変化(10:20)だけが残り、新しい順に並ぶ
        assert list(out["timestamp"]) == [_jst(2026, 10, 2, 10, 20), _jst(2026, 10, 2, 10, 5)]

    def test_presence_sensor_keeps_only_detection_starts(self):
        """人感センサーは「検知した」行だけ残す(not_detectedは出さない)。"""
        df = self._df([
            (_jst(2026, 10, 2, 10, 4), "pir", "not_detected", None),
            (_jst(2026, 10, 2, 10, 3), "pir", "detected", None),
            (_jst(2026, 10, 2, 10, 2), "pir", "not_detected", None),
            (_jst(2026, 10, 2, 10, 1), "pir", "detected", None),
        ])
        out = dashboard_page_service.sensor_state_change_log(df)
        assert list(out["contact_state"]) == ["detected", "detected"]
        assert list(out["timestamp"]) == [_jst(2026, 10, 2, 10, 3), _jst(2026, 10, 2, 10, 1)]

    def test_repeated_detections_are_still_collapsed(self):
        """連続する同じ`detected`は従来どおり最初の1行だけ(間に not_detected が無い場合)。"""
        df = self._df([
            (_jst(2026, 10, 2, 10, 3), "pir", "detected", None),
            (_jst(2026, 10, 2, 10, 2), "pir", "detected", None),
            (_jst(2026, 10, 2, 10, 1), "pir", "detected", None),
        ])
        out = dashboard_page_service.sensor_state_change_log(df)
        assert list(out["timestamp"]) == [_jst(2026, 10, 2, 10, 1)]

    def test_not_detected_matching_is_case_and_space_insensitive(self):
        df = self._df([
            (_jst(2026, 10, 2, 10, 2), "pir", " NOT_DETECTED ", None),
            (_jst(2026, 10, 2, 10, 1), "pir", "detected", None),
        ])
        out = dashboard_page_service.sensor_state_change_log(df)
        assert list(out["contact_state"]) == ["detected"]

    def test_door_and_camera_states_are_unaffected_by_the_presence_rule(self):
        df = self._df([
            (_jst(2026, 10, 2, 10, 3), "door", "close", None),
            (_jst(2026, 10, 2, 10, 2), "door", "open", None),
            (_jst(2026, 10, 2, 10, 1), "cam", None, "OFF"),
        ])
        out = dashboard_page_service.sensor_state_change_log(df)
        assert set(out["device_id"]) == {"door", "cam"} and len(out) == 3

    def test_presence_rule_applies_even_without_a_device_column(self):
        df = pd.DataFrame({"timestamp": [2, 1], "contact_state": ["not_detected", "detected"]})
        out = dashboard_page_service.sensor_state_change_log(df)
        assert list(out["contact_state"]) == ["detected"]

    def test_devices_are_compared_independently(self):
        df = self._df([
            (_jst(2026, 10, 2, 10, 10), "a", "open", None),
            (_jst(2026, 10, 2, 10, 5), "b", "open", None),
        ])
        out = dashboard_page_service.sensor_state_change_log(df)
        assert set(out["device_id"]) == {"a", "b"}

    def test_empty_or_missing_columns_returns_empty(self):
        assert dashboard_page_service.sensor_state_change_log(pd.DataFrame()).empty
        assert dashboard_page_service.sensor_state_change_log(
            pd.DataFrame({"timestamp": [1], "device_id": ["x"]})
        ).empty
        # device_id/friendly_nameが無くても状態のある行は残る(重複除去だけ行わない)
        no_device = pd.DataFrame({"timestamp": [2, 1], "contact_state": ["open", "open"]})
        assert len(dashboard_page_service.sensor_state_change_log(no_device)) == 2

    def test_watch_page_hides_periodic_meter_rows(self):
        df = pd.DataFrame({
            "timestamp": [_jst(2026, 10, 2, 10, 5), _jst(2026, 10, 2, 10, 0)],
            "device_id": ["meter", "door"],
            "friendly_name": ["洗面所温湿度計", "玄関ドア"],
            "location": ["高砂", "高砂"],
            "contact_state": [None, "open"],
            "movement_state": [None, None],
        })
        html = dashboard_page_service.render_watch_page(
            df, dashboard_path="/dashboard/", snapshot_url_prefix="/dashboard/snapshot"
        )
        assert "玄関ドア" in html
        assert "洗面所温湿度計" not in html
