# MY_HOME_SYSTEM/tests/test_dashboard_ux_improvements.py
"""ダッシュボードのUX改善(ホームのサマリー・カメラの録画状態/タップ再生・ライブ配信の自動停止・
定期チェック結果の表示・ログの日本語化 ほか)の回帰テスト。

いずれも「以前はこう見えていて困った」を固定するためのテスト。各テストのdocstringに、
直す前の挙動を書いてある。
"""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import pytz
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core import state_file
from routers import camera_router, dashboard_router
from services import (
    camera_service,
    dashboard_page_service,
    home_status_service,
    recording_status_service,
)
from services.home_status_service import StatusCard

JST = pytz.timezone("Asia/Tokyo")
NOW = JST.localize(datetime(2026, 11, 12, 20, 0, 0))
CAMERAS = [
    {"id": "cam_entrance", "name": "玄関カメラ", "enabled": True},
    {"id": "cam_garden", "name": "庭カメラ", "enabled": True},
    {"id": "cam_off", "name": "物置カメラ", "enabled": False},
]


@pytest.fixture(autouse=True)
def _clear_cache():
    home_status_service.clear_status_cache()
    yield
    home_status_service.clear_status_cache()


# === ホーム最上部の「いまの状態」 ===

class TestHomeSummary:
    def _card(self, title, value, theme, **kw):
        return StatusCard(title, value, theme, tab="watch", **kw)

    def test_all_normal_when_no_card_is_red(self):
        """以前はホームに結論が無く、10枚のカードを全部読まないと「大丈夫か」が分からなかった。"""
        cards = [self._card("👵 高砂 (実家)", "🟢 元気 (1h以内)", "theme-green"),
                 self._card("🏠 伊丹 (自宅)", "🟡 静か (2h前)", "theme-yellow")]
        alerts = home_status_service.build_home_alerts(cards, dashboard_path="/dashboard/")
        assert alerts == []
        assert "すべて正常です" in home_status_service.render_home_alerts_html(alerts)

    def test_red_cards_are_listed_with_links_and_without_emoji(self):
        cards = [self._card("👵 高砂 (実家)", "🔴 5時間 動きなし", "theme-red", anchor="#takasago-log"),
                 self._card("🎥 カメラ", "🔴 庭カメラ 録画停止", "theme-red", anchor="#camera-section")]
        alerts = home_status_service.build_home_alerts(cards, dashboard_path="/dashboard/")
        assert [a.text for a in alerts] == ["高砂 (実家): 5時間 動きなし", "カメラ: 庭カメラ 録画停止"]
        html = home_status_service.render_home_alerts_html(alerts)
        assert "2件、確認が必要です" in html
        assert 'href="/dashboard/watch#takasago-log"' in html and 'href="/dashboard/watch#camera-section"' in html

    def test_yellow_and_gray_do_not_raise_the_summary(self):
        """留守のたびに付く「静か」(黄)まで数えると、サマリーが毎回埋まって意味を失う。"""
        cards = [self._card("a", "🟡 静か", "theme-yellow"), self._card("b", "⚪ データなし", "theme-gray")]
        assert home_status_service.build_home_alerts(cards, dashboard_path="/dashboard/") == []

    def test_health_anomalies_about_the_house_are_included_but_admin_ones_are_not(self):
        health = {"stale": False, "anomalies": [
            {"key": "recording", "label": "カメラの録画", "text": "x"},
            {"key": "repo_behind", "label": "更新の取り込み遅れ", "text": "y"},
        ], "history": []}
        alerts = home_status_service.build_home_alerts([], dashboard_path="/dashboard/", health=health)
        assert [a.text for a in alerts] == ["定期チェック: カメラの録画"]
        assert alerts[0].href == "/dashboard/sys#health-check"

    def test_only_admin_anomalies_keep_the_home_normal(self):
        health = {"stale": False, "anomalies": [{"key": "deploy_config", "label": "実機の設定の差分", "text": "y"}],
                  "history": []}
        assert home_status_service.build_home_alerts([], dashboard_path="/dashboard/", health=health) == []

    def test_text_is_escaped(self):
        alerts = [home_status_service.HomeAlert("<script>x</script>", "/a?b=1&c=2")]
        html = home_status_service.render_home_alerts_html(alerts)
        assert "<script>" not in html and "&amp;c=2" in html


# === カードの値 ===

class TestCardValues:
    def test_cost_fetch_failure_is_not_shown_as_zero_yen(self):
        """以前は取得失敗を 0 に丸めており、「⚡ 0 円」と本当の0円を区別できなかった。"""
        assert home_status_service.get_cost_status(None) == ("⚪ 取得できません", "theme-gray")
        assert home_status_service.get_cost_status(0) == ("0 円", "theme-blue")
        assert home_status_service.get_cost_status(12345) == ("12,345 円", "theme-blue")
        assert home_status_service.describe_cost(None, 1000) is None

    def test_materials_keep_none_when_cost_loader_fails(self):
        with patch.object(home_status_service.analysis_service, "calculate_monthly_cost_cumulative",
                          side_effect=RuntimeError("db")), \
             patch.object(home_status_service.analysis_service, "load_sensor_data", return_value=pd.DataFrame()):
            materials = home_status_service.get_cached_materials()
        assert materials.monthly_cost is None

    def test_server_card_speaks_plainly_and_puts_numbers_in_the_sub(self):
        """以前の値は「💻 RAM: 3%」で、家族には正常かどうかが伝わらなかった。"""
        assert home_status_service.get_server_status({"percent": 42}) == ("🟢 正常", "theme-green")
        assert home_status_service.get_server_status({"percent": 91}) == ("🔴 メモリ不足", "theme-red")
        assert home_status_service.describe_server({"percent": 55}, {"percent": 42}) == "メモリ 42%・ディスク 55%"
        assert home_status_service.describe_server(None, None) is None

    def test_values_do_not_repeat_the_title_emoji(self):
        cards = home_status_service.build_status_cards(NOW, pd.DataFrame(), None, {"percent": 10}, 100)
        for card in cards:
            for emoji in ("🚗", "🎥", "💻", "🗄️", "⚡"):
                assert emoji not in card.value, f"{card.title} の値に {emoji} が残っている"


class TestCameraCardWithRecording:
    def _states(self, *states):
        return [{"id": f"c{i}", "name": name, "state": state, "latest": latest}
                for i, (name, state, latest) in enumerate(states)]

    def test_all_recording_is_green(self):
        """以前のカードは最後の動体検知だけで、カメラが止まっていても「24時間 検知なし」(灰色)にしかならなかった。"""
        rec = self._states(("玄関カメラ", "ok", NOW), ("庭カメラ", "ok", NOW), ("駐車場カメラ", "ok", NOW))
        assert home_status_service.get_camera_status(pd.DataFrame(), NOW, rec) == ("🟢 3台とも録画中", "theme-green")

    def test_single_camera_wording(self):
        rec = self._states(("玄関カメラ", "ok", NOW))
        assert home_status_service.get_camera_status(pd.DataFrame(), NOW, rec) == ("🟢 録画中", "theme-green")

    def test_one_stopped_camera_is_red_and_named(self):
        latest = NOW - timedelta(minutes=45)
        rec = self._states(("玄関カメラ", "ok", NOW), ("庭カメラ", "stalled", latest))
        assert home_status_service.get_camera_status(pd.DataFrame(), NOW, rec) == ("🔴 庭カメラ 録画停止", "theme-red")
        assert home_status_service.describe_camera(pd.DataFrame(), NOW, rec) == "最新の録画 19:15"

    def test_several_stopped_cameras_are_counted(self):
        rec = self._states(("玄関カメラ", "missing", None), ("庭カメラ", "stalled", NOW - timedelta(hours=2)))
        assert home_status_service.get_camera_status(pd.DataFrame(), NOW, rec) == ("🔴 2台 録画停止", "theme-red")
        assert home_status_service.describe_camera(pd.DataFrame(), NOW, rec) == "今日・昨日の録画がありません"

    def test_unknown_recording_falls_back_to_last_motion_in_info_color(self):
        """NAS未マウント等で録画の状態が分からないときは、従来どおり最後の動体検知(警告色にしない)。"""
        assert home_status_service.get_camera_status(pd.DataFrame(), NOW, None) == ("⚪ データなし", "theme-gray")


# === 録画の状態(recording_status_service) ===

class TestRecordingStatusService:
    def _touch(self, base: Path, folder: str, stamp: datetime):
        d = base / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stamp.strftime('%Y%m%d_%H%M%S')}.mp4").write_bytes(b"")

    def _setup(self, monkeypatch, tmp_path, mounted=True):
        monkeypatch.setattr(config, "CAMERAS", [dict(c) for c in CAMERAS], raising=False)
        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(recording_status_service.os.path, "ismount", lambda p: mounted)

    def test_states_per_enabled_camera(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        self._touch(tmp_path, "玄関カメラ", NOW - timedelta(minutes=5))
        self._touch(tmp_path, "庭カメラ", NOW - timedelta(minutes=50))
        states = recording_status_service.camera_recording_states(NOW)
        assert [(s["name"], s["state"]) for s in states] == [("玄関カメラ", "ok"), ("庭カメラ", "stalled")]
        assert states[0]["latest"].strftime("%H:%M") == "19:55"

    def test_no_files_is_missing(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        states = recording_status_service.camera_recording_states(NOW)
        assert {s["state"] for s in states} == {"missing"} and all(s["latest"] is None for s in states)

    def test_yesterday_file_is_found_right_after_midnight(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        just_after_midnight = JST.localize(datetime(2026, 11, 13, 0, 3, 0))
        self._touch(tmp_path, "玄関カメラ", just_after_midnight - timedelta(minutes=8))
        states = recording_status_service.camera_recording_states(just_after_midnight)
        assert states[0]["state"] == "ok"

    def test_unmounted_nas_is_unknown_not_stopped(self, monkeypatch, tmp_path):
        """NASが外れているだけなのに「全カメラ録画停止」と出すと、原因を取り違える。"""
        self._setup(monkeypatch, tmp_path, mounted=False)
        assert recording_status_service.camera_recording_states(NOW) is None

    def test_nas_folder_setting_is_respected(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        config.CAMERAS[0]["nas_folder"] = "entrance"
        self._touch(tmp_path, "entrance", NOW - timedelta(minutes=1))
        assert recording_status_service.camera_recording_states(NOW)[0]["state"] == "ok"

    def test_health_watch_uses_the_same_reader(self):
        import monitors.health_watch as health_watch
        with patch.object(recording_status_service, "recording_times", return_value=["sentinel"]) as spy:
            assert health_watch._recording_times("/x", NOW) == ["sentinel"]
        spy.assert_called_once_with("/x", NOW)
        assert health_watch.RECORDING_STALE_SEC == recording_status_service.RECORDING_STALE_SEC


class TestNasReadsDoNotBlockThePage:
    def test_slow_loader_times_out_as_unknown(self):
        """NAS(CIFS)が固まると一覧の取得が返ってこない。そのまま待つとホームごと開けなくなる。"""
        started = time.monotonic()
        value = home_status_service.cached_with_timeout("slow-test", lambda: time.sleep(1.0) or "late", timeout=0.05)
        assert value is None and time.monotonic() - started < 0.8

    def test_fast_loader_is_cached(self):
        calls = []
        loader = lambda: calls.append(1) or "v"  # noqa: E731
        assert home_status_service.cached_with_timeout("fast-test", loader) == "v"
        assert home_status_service.cached_with_timeout("fast-test", loader) == "v"
        assert len(calls) == 1


# === 定期チェック(health_watch)の結果 ===

class TestHealthResult:
    def _write(self, tmp_path, monkeypatch, data):
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path), raising=False)
        state_file.write_json_atomic(home_status_service.health_result_path(), data)

    def test_missing_file_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path), raising=False)
        assert home_status_service.read_health_result(NOW) is None

    def test_reads_anomalies_with_labels_and_history(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, {
            "checked_at": (NOW - timedelta(minutes=20)).isoformat(),
            "anomalies": [{"key": "recording", "text": "庭カメラ: 最新の録画が 19:00"}, {"key": "zzz", "text": "?"}],
            "history": [{"at": (NOW - timedelta(hours=3)).isoformat(), "keys": ["nas", "recording"]},
                        {"at": "broken", "keys": []}],
        })
        result = home_status_service.read_health_result(NOW)
        assert result["stale"] is False
        assert [a["label"] for a in result["anomalies"]] == ["カメラの録画", "zzz"]
        assert [h["labels"] for h in result["history"]] == [["保存装置(NAS)の接続", "カメラの録画"]]

    def test_old_result_is_stale(self, tmp_path, monkeypatch):
        """チェックが動いていないのに「異常なし」と出すと、止まっていることに気づけない。"""
        self._write(tmp_path, monkeypatch, {"checked_at": (NOW - timedelta(hours=5)).isoformat(), "anomalies": []})
        assert home_status_service.read_health_result(NOW)["stale"] is True

    def test_broken_file_is_none(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, {"anomalies": []})
        assert home_status_service.read_health_result(NOW) is None

    def test_health_watch_writes_what_the_dashboard_reads(self, tmp_path, monkeypatch):
        import monitors.health_watch as health_watch
        assert health_watch.RESULT_STATE_FILENAME == home_status_service.HEALTH_RESULT_FILENAME
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path), raising=False)

        health_watch._write_result_state(["recording"], ["常時録画が止まっている可能性があります:\n" + "x" * 500])
        health_watch._write_result_state(["recording"], ["同じ異常が継続"])   # 同じ組は履歴を増やさない
        health_watch._write_result_state([], [])                              # 解消

        raw = state_file.read_json(home_status_service.health_result_path())
        assert [h["keys"] for h in raw["history"]] == [[], ["recording"]]
        result = home_status_service.read_health_result()
        assert result["stale"] is False and result["anomalies"] == []
        assert [h["labels"] for h in result["history"]] == [[], ["カメラの録画"]]

    def test_long_text_is_truncated(self, tmp_path, monkeypatch):
        import monitors.health_watch as health_watch
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path), raising=False)
        health_watch._write_result_state(["app_logs"], ["y" * 5000])
        raw = state_file.read_json(home_status_service.health_result_path())
        assert len(raw["anomalies"][0]["text"]) == health_watch.RESULT_TEXT_MAX_CHARS

    def test_write_failure_does_not_break_the_check(self, monkeypatch):
        import monitors.health_watch as health_watch
        monkeypatch.setattr(health_watch.state_file, "read_json", MagicMock(side_effect=OSError("disk")))
        health_watch._write_result_state(["nas"], ["x"])  # 例外を出さない

    def test_sys_page_box(self):
        render = dashboard_page_service._render_health_check_box
        assert "まだ記録がありません" in render(None)
        ok = {"checked_at": NOW, "stale": False, "anomalies": [], "history": []}
        assert "異常は見つかっていません" in render(ok)
        bad = {"checked_at": NOW, "stale": False, "history": [{"at": NOW, "labels": []}],
               "anomalies": [{"key": "nas", "label": "保存装置(NAS)の接続", "text": "<b>NAS</b>"}]}
        html = render(bad)
        assert "1件の異常を検知しています" in html and "&lt;b&gt;NAS&lt;/b&gt;" in html and "異常なしに戻りました" in html
        assert "定期チェックが止まっています" in render({**ok, "stale": True})

    def test_sys_summary_counts_health_anomalies(self):
        health = {"checked_at": NOW, "stale": False, "history": [],
                  "anomalies": [{"key": "nas", "label": "x", "text": "y"}, {"key": "disk", "label": "x", "text": "y"}]}
        html = dashboard_page_service.render_sys_page(
            pd.DataFrame(), None, pd.DataFrame(), {"percent": 10}, None, NOW, dashboard_path="/dashboard/", health=health)
        # 鮮度の行(電気・NAS がデータなし=2件)+ 定期チェック2件
        assert "4件、確認が必要です" in html


# === 見守りログの言葉 ===

class TestStateLabels:
    @pytest.mark.parametrize("raw,expected", [
        ("open", "開いた"), ("close", "閉じた"), ("OPEN", "開いた"), ("timeOutNotClose", "開けっぱなし"),
        ("detected", "動きを検知"), ("ON", "動きを検知"), ("not_detected", "検知なし"),
        ("開 → 閉（12秒）", "開 → 閉（12秒）"), ("something_new", "something_new"), (None, ""), (float("nan"), ""),
    ])
    def test_label(self, raw, expected):
        assert dashboard_page_service.state_label(raw) == expected

    def test_table_shows_japanese_states(self):
        """以前は `open`/`close`/`detected` がそのまま出て、まとめ行の「開 → 閉」だけ日本語だった。"""
        df = pd.DataFrame({
            "timestamp": [pd.Timestamp("2026-11-12 19:00")] * 2,
            "friendly_name": ["冷蔵庫", "リビング"],
            "contact_state": ["open", None],
            "movement_state": [None, "detected"],
        })
        html = dashboard_page_service._render_simple_table(
            df, {"timestamp": "時刻", "friendly_name": "センサー", "movement_state": "動体", "contact_state": "開閉"})
        assert "開いた" in html and "動きを検知" in html
        assert ">open<" not in html and ">detected<" not in html


# === カメラ: タップしたカメラだけ再生 ===

class TestCameraTiles:
    def _html(self, **kw):
        return dashboard_page_service._render_camera_selector(CAMERAS[:2], **kw)

    def test_live_does_not_start_until_tapped(self):
        """以前は `autoplay` 付きで、ページを開いた時点で全台のライブ(ffmpeg×台数)が始まっていた。"""
        html = self._html()
        assert "autoplay" not in html
        assert html.count('class="camera-overlay camera-play"') == 2 and "▶ ライブを見る" in html
        assert html.count('data-state="idle"') == 2

    def test_has_an_error_state_with_retry(self):
        """以前は接続できないと黒い枠で読み込み中が回り続けるだけだった。"""
        html = self._html()
        assert "接続できません" in html and html.count('class="camera-retry"') == 2
        script = (Path(dashboard_page_service._STATIC_DIR) / "camera.js").read_text(encoding="utf-8")
        assert "START_TIMEOUT_MS" in script and "data.fatal" in script and "visibilitychange" in script

    def test_poster_and_recording_badge(self):
        html = self._html(
            posters={"cam_entrance": "玄関カメラ_motion_20261112_200100.jpg"}, snapshot_url_prefix="/dashboard/snapshot",
            recording=[{"id": "cam_entrance", "name": "玄関カメラ", "state": "ok"},
                       {"id": "cam_garden", "name": "庭カメラ", "state": "stalled"}],
        )
        assert 'poster="/dashboard/snapshot/%E7%8E%84%E9%96%A2' in html
        assert "● 録画中" in html and "✕ 録画停止" in html

    def test_no_cameras(self):
        assert "カメラが登録されていません" in dashboard_page_service._render_camera_selector([])


class TestSnapshots:
    NAMES = [
        "玄関カメラ_motion_20261112_200100.jpg",
        "庭カメラ_motion_20261112_090000.jpg",
        "玄関カメラ_motion_20261111_120000.jpg",
        "庭カメラ_motion_20261112_203000.jpg",
        "形式の違う名前.jpg",
    ]

    def _patch_dir(self, monkeypatch, tmp_path):
        snap = tmp_path / "snapshots"
        snap.mkdir()
        for name in self.NAMES:
            (snap / name).write_bytes(b"x")
        monkeypatch.setattr(config, "ASSETS_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(config, "CAMERAS", [dict(c) for c in CAMERAS], raising=False)

    def test_sorted_by_capture_time_not_by_camera_name(self, monkeypatch, tmp_path):
        """ファイル名はカメラ名で始まるので、名前順だと「最近の写真」が1台のカメラの写真だけになっていた。"""
        self._patch_dir(monkeypatch, tmp_path)
        names = dashboard_page_service._snapshot_names_newest_first()
        assert names[:4] == [
            "庭カメラ_motion_20261112_203000.jpg", "玄関カメラ_motion_20261112_200100.jpg",
            "庭カメラ_motion_20261112_090000.jpg", "玄関カメラ_motion_20261111_120000.jpg",
        ]
        assert names[-1] == "形式の違う名前.jpg"

    def test_latest_per_camera_and_home_stills(self, monkeypatch, tmp_path):
        self._patch_dir(monkeypatch, tmp_path)
        cams = dashboard_page_service._enabled_cameras()
        assert [c["id"] for c in cams] == ["cam_entrance", "cam_garden"]  # 無効なカメラは出さない
        assert dashboard_page_service.latest_snapshot_by_camera(cams) == {
            "cam_entrance": "玄関カメラ_motion_20261112_200100.jpg",
            "cam_garden": "庭カメラ_motion_20261112_203000.jpg",
        }
        home_status_service.clear_status_cache()
        html = dashboard_page_service.render_camera_stills(
            dashboard_path="/dashboard/", snapshot_url_prefix="/dashboard/snapshot")
        assert html.count('class="cam-still"') == 2
        assert 'href="/dashboard/watch?camera=cam_entrance#camera-section"' in html
        assert "11/12 20:01" in html and "<video" not in html  # ホームではライブを流さない

    def test_camera_without_photo_still_links_to_live(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "ASSETS_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(config, "CAMERAS", [dict(c) for c in CAMERAS], raising=False)
        html = dashboard_page_service.render_camera_stills(dashboard_path="/dashboard/", snapshot_url_prefix="/s")
        assert html.count("写真なし") == 2 and html.count('class="cam-still"') == 2

    def test_no_cameras_renders_nothing(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [], raising=False)
        assert dashboard_page_service.render_camera_stills(dashboard_path="/dashboard/", snapshot_url_prefix="/s") == ""


# === ライブ配信の自動停止 ===

class _FakeProc:
    def __init__(self, alive=True):
        self.alive, self.terminated = alive, False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated, self.alive = True, False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.alive = False


class TestLiveIdleStop:
    @pytest.fixture(autouse=True)
    def _clean(self):
        saved = (dict(camera_service._active_processes), dict(camera_service._live_started_at),
                 dict(camera_service._live_last_access))
        for d in (camera_service._active_processes, camera_service._live_started_at, camera_service._live_last_access):
            d.clear()
        yield
        for d, s in zip((camera_service._active_processes, camera_service._live_started_at,
                         camera_service._live_last_access), saved):
            d.clear()
            d.update(s)

    def _register(self, cam_id, *, last_access_ago, alive=True):
        proc = _FakeProc(alive)
        camera_service._active_processes[cam_id] = proc
        camera_service._live_started_at[cam_id] = time.time() - 600
        camera_service._live_last_access[cam_id] = time.time() - last_access_ago
        return proc

    def test_unwatched_stream_is_stopped_and_watched_one_is_kept(self):
        """以前は一度始めたライブ用ffmpegが、ページを閉じてもサーバー再起動まで動き続けていた。"""
        idle = self._register("idle", last_access_ago=camera_service.HLS_LIVE_IDLE_SECONDS + 5)
        watched = self._register("watched", last_access_ago=3)
        assert camera_service.stop_idle_live_streams() == 1
        assert idle.terminated and not watched.terminated
        assert list(camera_service._active_processes) == ["watched"]
        assert "idle" not in camera_service._live_last_access

    def test_touch_keeps_the_stream_alive(self):
        proc = self._register("cam", last_access_ago=camera_service.HLS_LIVE_IDLE_SECONDS + 5)
        camera_service.touch_live("cam")
        assert camera_service.stop_idle_live_streams() == 0 and not proc.terminated

    def test_dead_process_is_forgotten_without_counting(self):
        self._register("dead", last_access_ago=1, alive=False)
        assert camera_service.stop_idle_live_streams() == 0
        assert camera_service._active_processes == {}

    def test_segment_request_counts_as_watching(self, tmp_path, monkeypatch):
        cam = {"id": "cam1", "name": "玄関"}
        monkeypatch.setattr(camera_router, "_require_camera", lambda camera_id: cam)
        monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", str(tmp_path))
        (tmp_path / "cam1").mkdir()
        (tmp_path / "cam1" / "seg1.ts").write_bytes(b"ts")
        app = FastAPI()
        app.include_router(camera_router.router, prefix="/api/cameras")
        before = time.time()
        with TestClient(app) as client:
            assert client.get("/api/cameras/live/cam1/seg1.ts").status_code == 200
        assert camera_service._live_last_access["cam1"] >= before

    def test_start_marks_watching_and_does_not_restart_a_running_stream(self, tmp_path, monkeypatch):
        monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", str(tmp_path))
        proc = self._register("cam1", last_access_ago=999)
        (tmp_path / "cam1").mkdir()
        (tmp_path / "cam1" / "stream.m3u8").write_text("#EXTM3U")
        with patch.object(camera_service.subprocess, "Popen") as popen:
            camera_service.start_hls_stream({"id": "cam1", "name": "玄関"})
        popen.assert_not_called()
        assert time.time() - camera_service._live_last_access["cam1"] < 5 and not proc.terminated


# === ページの並び・導線 ===

def _stub_loaders():
    defaults = {
        "load_sensor_data": pd.DataFrame(), "load_generic_data": pd.DataFrame(), "load_nas_status": None,
        "load_nas_history": pd.DataFrame(), "get_memory_usage": {"percent": 42.0},
        "calculate_monthly_cost_cumulative": 1234, "calculate_last_month_cost_same_point": 1000,
        "get_disk_usage": {"percent": 55.0}, "calculate_daily_cost_series": [],
        "load_hourly_climate": pd.DataFrame(), "load_activity_events": pd.DataFrame(),
    }
    return [patch.object(home_status_service.analysis_service, k, return_value=v) for k, v in defaults.items()]


def _get(path, **kw):
    patches = _stub_loaders() + [
        patch.object(home_status_service, "collect_link_health", return_value={"quest": True, "asa_note": True}),
        patch.object(home_status_service, "get_today_weather", return_value=kw.get("weather")),
        patch.object(home_status_service, "read_health_result", return_value=kw.get("health")),
    ]
    for p in patches:
        p.start()
    try:
        app = FastAPI()
        app.include_router(dashboard_router.router)
        with TestClient(app) as client:
            return client.get(f"{config.DASHBOARD_BASE_PATH}/{path}", follow_redirects=False)
    finally:
        for p in patches:
            p.stop()


class TestHomeLayout:
    def test_order_is_summary_then_watch_then_today_then_the_rest(self):
        """以前は 見守り → 室温 → 過去30日ヒートマップ(約1画面)→ … → 今日の動き の順で、
        今日の動きが3画面目にあった。"""
        text = _get("").text
        order = [text.index(marker) for marker in (
            'class="alerts alerts-ok home-summary"', "👀 見守り", "📊 今日の動き", "🌡️ 室温", "💡 くらし",
            "🔧 システム</h2>", "よく使うリンク",
        )]
        assert order == sorted(order)

    def test_home_has_no_duplicate_menu(self):
        text = _get("").text
        assert "<h2>メニュー</h2>" not in text and 'class="nav-card"' not in text

    def test_fragment_matches_the_page_section(self):
        """自動更新のたびに並びや内容が変わらないこと(ページ本体とフラグメントは同じ材料から作る)。"""
        page = _get("").text
        fragment = _get("status").text
        assert fragment.startswith('<div id="status">')
        for marker in ("home-summary", "📊 今日の動き", "よく使うリンク", "👀 見守り"):
            assert marker in fragment and marker in page

    def test_weather_line_is_shown_when_recorded(self):
        text = _get("", weather={"desc": "晴れ", "max_temp": 18.4, "min_temp": 9.2}).text
        assert "今日の天気: 晴れ・最高 18℃ / 最低 9℃" in text
        assert "今日の天気" not in _get("").text

    def test_health_anomaly_reaches_the_home_summary(self):
        health = {"checked_at": NOW, "stale": False, "history": [],
                  "anomalies": [{"key": "recording", "label": "カメラの録画", "text": "x"}]}
        text = _get("", health=health).text
        assert "1件、確認が必要です" in text and "定期チェック: カメラの録画" in text


class TestWeatherLine:
    def test_partial_data(self):
        render = home_status_service.render_today_weather_html
        assert render(None) == ""
        assert render({"desc": None, "max_temp": None, "min_temp": None}) == ""
        assert "最高 20℃" in render({"desc": None, "max_temp": 20.2, "min_temp": None})

    def test_reads_today_row(self, isolated_db):
        from core.database import get_db_cursor
        from core.utils import get_now_jst
        today = get_now_jst().date().isoformat()
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO weather_history (date, location, min_temp, max_temp, weather_desc) VALUES (?, ?, ?, ?, ?)",
                (today, config.WEATHER_LOCATION_NAME, 9.0, 18.0, "くもり"),
            )
        assert home_status_service.get_today_weather() == {"desc": "くもり", "max_temp": 18.0, "min_temp": 9.0}

    def test_no_row_is_none(self, isolated_db):
        assert home_status_service.get_today_weather() is None


class TestSysPageLayout:
    def test_admin_operations_are_folded_and_family_items_come_first(self):
        """以前は Git の状態・起動履歴が先頭にあり、再起動ボタンも最初から見えていた。"""
        text = _get("sys").text
        admin = text.index('<details class="admin-section" id="admin-section">')
        assert text.index("各機能の最終更新") < text.index('id="health-check"') < admin
        assert text.index("今すぐバックアップ") < admin  # 安全な操作は折りたたみの外
        for marker in ('id="git-status"', 'id="boot-history"', 'id="log-download"', 'id="restartBtn"', 'id="deployBtn"'):
            assert text.index(marker) > admin, f"{marker} が折りたたみの外にある"
        assert "<details open" not in text[admin:admin + 80]


class TestOfflineAndTheme:
    def test_service_worker_is_served_at_the_dashboard_root(self):
        """スコープを `/dashboard/` にするため、`static/` 配下ではなくベースパス直下で返す。"""
        res = _get("sw.js")
        assert res.status_code == 200 and "javascript" in res.headers["content-type"]
        assert res.headers["cache-control"] == "no-cache" and "navigate" in res.text

    def test_every_page_loads_app_js_and_has_the_theme_toggle(self):
        for path in ("", "watch", "life", "sys", "updates"):
            text = _get(path).text
            assert "/static/app.js?v=" in text and 'id="themeToggle"' in text, path
