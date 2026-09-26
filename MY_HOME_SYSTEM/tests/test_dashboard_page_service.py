# MY_HOME_SYSTEM/tests/test_dashboard_page_service.py
"""services/dashboard_page_service.py の見守り/くらしページ組み立てのテスト。

NAS(config.ASSETS_DIR)には一切触れず、tmp_pathへ差し替える。
"""
import os
import sys
from datetime import datetime, timedelta

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
            pd.DataFrame(),
            dashboard_path="/dashboard",
            snapshot_url_prefix="/dashboard/snapshot",
        )
        assert "玄関" in html
        assert "庭" not in html

    def test_takasago_rows_are_filtered_by_location(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [])
        df_sensor = pd.DataFrame({
            "location": ["高砂", "伊丹"],
            "timestamp": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-01")],
            "friendly_name": ["センサーA", "センサーB"],
            "contact_state": ["OPEN", "CLOSE"],
        })
        html = dashboard_page_service.render_watch_page(
            df_sensor,
            pd.DataFrame(),
            dashboard_path="/dashboard",
            snapshot_url_prefix="/dashboard/snapshot",
        )
        assert "センサーA" in html
        assert "センサーB" not in html


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
