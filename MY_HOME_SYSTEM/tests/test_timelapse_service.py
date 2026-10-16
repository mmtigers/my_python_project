# MY_HOME_SYSTEM/tests/test_timelapse_service.py
"""見守りページのタイムラプス一覧(`services/timelapse_service.py` と `_render_timelapse_section`)のテスト。

動画ファイルは tmp_path に置いたダミーで、実際の生成・Discord送信・NASには一切触れない。
"""
import os
import sys
import time
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from routers import dashboard_router
from services import dashboard_page_service, home_status_service, timelapse_service

OLD = time.time() - 3600  # 生成が終わっている(書き込み中でない)古さ


@pytest.fixture
def tl_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    d = tmp_path / "assets" / "timelapse"
    d.mkdir(parents=True)
    return d


def _make(d, name, size=2048, mtime=OLD):
    p = d / name
    p.write_bytes(b"x" * size)
    os.utime(p, (mtime, mtime))
    return p


class TestListTimelapses:
    def test_missing_directory_gives_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
        assert timelapse_service.list_timelapses() == []

    def test_newest_first_by_date_then_start_time(self, tl_dir):
        _make(tl_dir, "entrance_2026-10-15_1300-1500_summary.mp4")
        _make(tl_dir, "entrance_2026-10-16_0730-0900_summary.mp4")
        _make(tl_dir, "entrance_2026-10-16_1300-1500_summary.mp4")
        names = [t.filename for t in timelapse_service.list_timelapses()]
        assert names == [
            "entrance_2026-10-16_1300-1500_summary.mp4",
            "entrance_2026-10-16_0730-0900_summary.mp4",
            "entrance_2026-10-15_1300-1500_summary.mp4",
        ]

    def test_fields_and_thumbnail_detection(self, tl_dir):
        _make(tl_dir, "entrance_2026-10-16_0730-0900_summary.mp4", size=5000)
        _make(tl_dir, "entrance_2026-10-16_0730-0900_summary.jpg", size=10)
        _make(tl_dir, "entrance_2026-10-15_summary.mp4")  # 時間指定なし・サムネイルなし
        by_name = {t.filename: t for t in timelapse_service.list_timelapses()}
        a = by_name["entrance_2026-10-16_0730-0900_summary.mp4"]
        assert (a.camera, a.day, a.start, a.end, a.size_bytes, a.has_thumbnail) == (
            "entrance", date(2026, 10, 16), "0730", "0900", 5000, True)
        b = by_name["entrance_2026-10-15_summary.mp4"]
        assert (b.start, b.end, b.has_thumbnail) == (None, None, False)

    @pytest.mark.parametrize("name", [
        "notes.txt", "entrance_2026-10-16_summary.jpg", "x_part_001.mp4", "entrance_2026-13-40_summary.mp4",
        "../escape_2026-10-16_summary.mp4", "entrance_2026-10-16_0730-0900_summary.mp4.tmp",
        "entrance 2026-10-16_summary.mp4",
    ])
    def test_non_matching_names_are_ignored(self, tl_dir, name):
        try:
            _make(tl_dir, name)
        except OSError:
            pass
        assert timelapse_service.list_timelapses() == []

    def test_empty_and_in_progress_files_are_skipped(self, tl_dir):
        _make(tl_dir, "entrance_2026-10-16_0730-0900_summary.mp4", size=0)
        _make(tl_dir, "entrance_2026-10-16_1300-1500_summary.mp4", mtime=time.time())  # 書き込み中
        assert timelapse_service.list_timelapses() == []

    def test_symlink_is_ignored(self, tl_dir, tmp_path):
        target = tmp_path / "secret.mp4"
        target.write_bytes(b"x" * 100)
        os.utime(target, (OLD, OLD))
        (tl_dir / "entrance_2026-10-16_0730-0900_summary.mp4").symlink_to(target)
        assert timelapse_service.list_timelapses() == []

    def test_limit(self, tl_dir):
        for d in range(10, 15):
            _make(tl_dir, f"entrance_2026-10-{d}_summary.mp4")
        assert len(timelapse_service.list_timelapses(limit=2)) == 2

    def test_unreadable_directory_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
        with patch.object(timelapse_service.os, "scandir", side_effect=PermissionError("denied")):
            assert timelapse_service.list_timelapses() == []


def _items(n, thumb=True):
    return [
        timelapse_service.Timelapse(
            filename=f"entrance_2026-10-{20 - i:02d}_0730-0900_summary.mp4", camera="entrance",
            day=date(2026, 10, 20 - i), start="0730", end="0900", size_bytes=12 * 1024 * 1024, has_thumbnail=thumb)
        for i in range(n)
    ]


class TestRender:
    def _render(self, items, **kw):
        return dashboard_page_service._render_timelapse_section(
            items, dashboard_path="/dashboard/", camera_names={"entrance": "玄関"}, **kw)

    def test_empty_shows_placeholder(self):
        html = self._render([])
        assert "タイムラプスはまだありません" in html and "<video" not in html

    def test_card_has_label_camera_size_and_lazy_video(self):
        html = self._render(_items(1))
        assert "10/20(火) 朝 07:30〜09:00" in html
        assert "玄関 ・ 12.0MB" in html
        assert 'preload="none"' in html and "playsinline" in html and "controls" in html
        assert 'src="/assets/timelapse/entrance_2026-10-20_0730-0900_summary.mp4"' in html
        assert 'poster="/assets/timelapse/entrance_2026-10-20_0730-0900_summary.jpg"' in html
        assert 'loading="lazy"' in html

    def test_afternoon_label_and_no_time_label(self):
        pm = timelapse_service.Timelapse("entrance_2026-10-16_1300-1500_summary.mp4", "entrance",
                                         date(2026, 10, 16), "1300", "1500", 10, False)
        whole = timelapse_service.Timelapse("entrance_2026-10-15_summary.mp4", "entrance",
                                            date(2026, 10, 15), None, None, 10, False)
        html = self._render([pm, whole])
        assert "10/16(金) 午後 13:00〜15:00" in html
        assert "10/15(木)<" in html                       # 時間指定なしは日付だけ
        assert 'class="tl-thumb tl-thumb-none"' in html and "poster=" not in html

    def test_only_ten_shown_with_link_to_show_all(self):
        html = self._render(_items(12))
        assert html.count('class="tl-card"') == 10
        assert 'href="/dashboard/watch?tl_all=1#timelapse-section"' in html and "さらに2件を表示" in html

    def test_show_all(self):
        html = self._render(_items(12), show_all=True)
        assert html.count('class="tl-card"') == 12 and "最新10件だけにする" in html

    def test_unknown_camera_falls_back_to_its_id_and_is_escaped(self):
        item = timelapse_service.Timelapse("x-y_2026-10-16_summary.mp4", "x-y", date(2026, 10, 16), None, None, 10, False)
        assert "x-y ・" in self._render([item])


class TestWatchPageIntegration:
    @pytest.fixture(autouse=True)
    def _stubs(self, monkeypatch):
        home_status_service.clear_status_cache()
        monkeypatch.setattr(home_status_service, "check_quest_app_health", lambda: True)
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: True)
        yield
        home_status_service.clear_status_cache()

    def _get(self, path):
        stubs = {"load_sensor_data": pd.DataFrame(), "load_generic_data": pd.DataFrame(), "load_nas_status": None,
                 "load_nas_history": pd.DataFrame(), "get_memory_usage": {"percent": 1.0},
                 "calculate_monthly_cost_cumulative": 0, "calculate_last_month_cost_same_point": 0,
                 "get_disk_usage": {"percent": 1.0}, "calculate_daily_cost_series": []}
        patches = [patch.object(home_status_service.analysis_service, k, return_value=v) for k, v in stubs.items()]
        for p in patches:
            p.start()
        try:
            app = FastAPI()
            app.include_router(dashboard_router.router)
            with TestClient(app) as client:
                return client.get(path)
        finally:
            for p in patches:
                p.stop()

    def test_section_sits_between_photos_and_logs(self, tl_dir):
        _make(tl_dir, "entrance_2026-10-16_0730-0900_summary.mp4")
        text = self._get(f"{config.DASHBOARD_BASE_PATH}/watch").text
        assert text.index("🖼️ 最近の写真") < text.index('id="timelapse-section"') < text.index('id="security-log"')
        assert "10/16(金) 朝 07:30〜09:00" in text

    def test_no_files_still_opens(self, tl_dir):
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/watch")
        assert res.status_code == 200 and "タイムラプスはまだありません" in res.text

    def test_tl_all_query(self, tl_dir):
        for d in range(1, 13):
            _make(tl_dir, f"entrance_2026-10-{d:02d}_summary.mp4")
        assert self._get(f"{config.DASHBOARD_BASE_PATH}/watch").text.count('class="tl-card"') == 10
        assert self._get(f"{config.DASHBOARD_BASE_PATH}/watch?tl_all=1").text.count('class="tl-card"') == 12
