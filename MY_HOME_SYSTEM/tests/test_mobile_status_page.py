# MY_HOME_SYSTEM/tests/test_mobile_status_page.py
"""ダッシュボードのホームページ(`/dashboard`)と、その土台である
`services/home_status_service.py` / `services/dashboard_page_service.py` のテスト。

#829: 以前はStreamlit版の「詳細表示」(`dashboard.py`)と、Streamlitを介さない
「かんたん表示」(`/dashboard/m`。ステータスカードのみの単一ページ)が別々に存在した。
詳細表示・表示モード切替UIを廃止し、かんたん表示を唯一のダッシュボードとする方針変更に
伴い、`unified_server`自身がホーム/見守り/くらし/システムの4ページをHTMLで返す構成に
なった。カードの判定ロジックは`home_status_service.py`に一本化されている
("2つの画面が同じ内容を別々に計算し始めると、どちらかを直した時にもう片方が
古いままになる"という以前からの方針は、ページが増えても変わらない)。
"""
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from routers import dashboard_router
from services import dashboard_page_service, home_status_service

NOW = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

# サマリーに並ぶカードの枚数。`home_status_service.build_status_cards` の
# 戻り値と対になっているので、カードを増減させるときは一緒に直す。
EXPECTED_CARD_COUNT = 11
# ホームに並ぶカードの枚数: 見守り4枚 + 室温4枚 + システム・サービスのタイル6枚
# (サーバー・NAS・録画・ファミクエ・あさノート・よるノート)。電気代・システムページ用のカードは出さない。
HOME_CARD_COUNT = 14


@pytest.fixture(autouse=True)
def _clear_status_cache():
    home_status_service.clear_status_cache()
    yield
    home_status_service.clear_status_cache()


# 外部リンクの稼働チェック(あさノートへの実HTTP・実DBアクセス)の本物。個別のテストで使う。
_REAL_CHECK_QUEST = home_status_service.check_quest_app_health
_REAL_CHECK_ASA_NOTE = home_status_service.check_asa_note_health


@pytest.fixture(autouse=True)
def _stub_link_health_checks(monkeypatch):
    """ホームを開くテストが実際に外部(あさノート)へHTTP接続しないよう、既定で「正常」に差し替える。"""
    monkeypatch.setattr(home_status_service, "check_quest_app_health", lambda: True)
    monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: True)


@pytest.fixture(autouse=True)
def _stub_release_history_remote(monkeypatch):
    """アップデートページが実際に外部(あさノート・よるノート)へHTTP接続しないよう、取得を差し替える。"""
    from services import release_history_service
    release_history_service.clear_cache()
    monkeypatch.setattr(release_history_service, "_fetch_remote", lambda url, app: [])
    yield
    release_history_service.clear_cache()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router.router)
    return TestClient(app)


def _dashboard_css() -> str:
    """共通CSS(`static/dashboard/dashboard.css`。#949で外部ファイル化)の本文。"""
    return (Path(dashboard_page_service._STATIC_DIR) / "dashboard.css").read_text(encoding="utf-8")


def _stub_loaders(**overrides):
    """DBの読み取りを差し替える(テストで実際に読みに行かせない)。"""
    defaults = {
        "load_sensor_data": pd.DataFrame(),
        "load_generic_data": pd.DataFrame(),
        "load_nas_status": None,
        "load_nas_history": pd.DataFrame(),
        "get_memory_usage": {"percent": 42.0},
        "calculate_monthly_cost_cumulative": 1234,
        "calculate_last_month_cost_same_point": 1000,
        "get_disk_usage": {"percent": 55.0},
        "calculate_daily_cost_series": [(date(2026, 9, 19), 123)],
    }
    defaults.update(overrides)
    return [
        patch.object(home_status_service.analysis_service, name, return_value=value)
        for name, value in defaults.items()
    ]


class TestDashboardHomePage:
    def _get(self, **overrides):
        patches = _stub_loaders(**overrides)
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(f"{config.DASHBOARD_BASE_PATH}/")
        finally:
            for p in patches:
                p.stop()

    def test_page_renders_all_status_cards(self):
        res = self._get()

        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/html")
        assert res.text.count('class="status-card') == HOME_CARD_COUNT
        assert "status-grid" in res.text

    def test_page_refreshes_itself(self):
        """置きっぱなしでも古い値を見せ続けないこと。"""
        res = self._get()
        assert f'var intervalMs = {home_status_service.MOBILE_PAGE_REFRESH_SEC * 1000};' in res.text

    def test_page_links_to_the_sub_pages_and_external_links(self):
        res = self._get()
        assert f'href="{config.DASHBOARD_BASE_PATH}/watch"' in res.text
        assert f'href="{config.DASHBOARD_BASE_PATH}/life"' in res.text
        assert f'href="{config.DASHBOARD_BASE_PATH}/sys"' in res.text
        assert 'href="/quest"' in res.text
        assert f'href="{config.ASA_NOTE_URL}"' in res.text
        assert f'href="{config.YORU_NOTE_URL}"' in res.text

    def test_page_can_be_added_to_the_home_screen(self):
        res = self._get()
        assert f'href="{config.DASHBOARD_BASE_PATH}/app.webmanifest"' in res.text
        assert 'crossorigin="use-credentials"' in res.text

    def test_a_failing_loader_does_not_take_down_the_page(self):
        """1枚のカードの取得失敗でページ全体を落とさないこと。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "get_memory_usage",
                              side_effect=RuntimeError("psutil failed")), _client() as client:
                res = client.get(f"{config.DASHBOARD_BASE_PATH}/")
        finally:
            for p in patches:
                p.stop()

        assert res.status_code == 200
        assert res.text.count('class="status-card') == HOME_CARD_COUNT
        # 取れなかったものは値を偽らずに「取得できません」と出す
        assert "取得できません" in res.text


class TestHomeScreenInstall:
    def test_manifest_and_icon_are_served(self):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/app.webmanifest")
            assert res.status_code == 200
            manifest = res.json()
            assert manifest["start_url"] == f"{config.DASHBOARD_BASE_PATH}/"

            icon = client.get(f"{config.DASHBOARD_BASE_PATH}/icon-180.png")
            assert icon.status_code == 200
            assert icon.headers["content-type"] == "image/png"

    def test_an_unsupported_icon_size_is_rejected(self):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/icon-999.png")
            assert res.status_code == 404


class TestDashboardSnapshot:
    """見守りページのスナップショット画像1枚を返すエンドポイント(パストラバーサル対策込み)。"""

    def test_existing_snapshot_is_served(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ASSETS_DIR", str(tmp_path / "assets"))
        snap_dir = tmp_path / "assets" / "snapshots"
        snap_dir.mkdir(parents=True)
        (snap_dir / "photo.jpg").write_bytes(b"fake-jpeg")
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/snapshot/photo.jpg")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"

    def test_missing_snapshot_returns_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ASSETS_DIR", str(tmp_path / "assets"))
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/snapshot/missing.jpg")
        assert res.status_code == 404

    def test_path_traversal_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ASSETS_DIR", str(tmp_path / "assets"))
        (tmp_path / "assets").mkdir(parents=True)
        (tmp_path / "assets" / "secret.txt").write_text("x")
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/snapshot/..%2Fsecret.txt")
        assert res.status_code == 404


class TestLegacyUrlRedirects:
    """#829でStreamlit版のURL体系(`?tab=`)・旧「かんたん表示」(`/dashboard/m`)を
    廃止したが、スマートフォンのホーム画面に古いURLを追加している可能性があるため、
    どちらも新しいページへリダイレクトする。"""

    def test_old_mobile_path_redirects_to_the_home_page(self):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/m", follow_redirects=False)

        assert res.status_code == 301
        assert res.headers["location"] == f"{config.DASHBOARD_BASE_PATH}/"

    @pytest.mark.parametrize("tab,expected_suffix", [
        ("watch", "/watch"), ("life", "/life"), ("sys", "/sys"), ("home", ""),
    ])
    def test_old_tab_query_param_redirects_to_the_matching_page(self, tab, expected_suffix):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}?tab={tab}", follow_redirects=False)

        assert res.status_code == 302
        assert res.headers["location"] == f"{config.DASHBOARD_BASE_PATH}{expected_suffix}"

    def test_an_unknown_tab_value_redirects_home(self):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}?tab=nonexistent", follow_redirects=False)

        assert res.status_code == 302
        assert res.headers["location"] == config.DASHBOARD_BASE_PATH


class TestSubPages:
    """👀見守り・💡くらし・🔧システム・📷カメラのページと、見守り・システムの詳細画面。"""

    def _get(self, path, **overrides):
        patches = _stub_loaders(**overrides)
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(f"{config.DASHBOARD_BASE_PATH}/{path}")
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.parametrize("path", ["watch", "watch/live", "watch/itami", "life", "sys", "sys/nas", "updates"])
    def test_each_page_can_go_home_from_the_bottom_nav(self, path):
        """ホームへの導線は下部ナビ(全ページ共通)。ページ上部の「ホームへ戻る」は重複のため廃止した。"""
        res = self._get(path)
        assert res.status_code == 200
        nav = res.text[res.text.index('<nav class="bottom-nav"'):]
        assert f'href="{config.DASHBOARD_BASE_PATH}/"' in nav and "ホーム" in nav

    def test_watch_pages_show_cameras_gallery_and_logs(self):
        """見守りは一覧(カード・カメラの写真・今日の動き)・カメラ(ライブ・写真・タイムラプス)・
        詳細画面(防犯/高砂/伊丹のログ)に分かれている。"""
        overview = self._get("watch").text
        assert "👀 見守り" in overview and "📊 今日の動き" in overview and "🛡️ 防犯ログ" in overview
        live = self._get("watch/live").text
        assert 'id="camera-section"' in live and "最近の写真" in live
        assert "防犯ログ" in self._get("watch/security").text
        assert "👵 高砂(実家)" in self._get("watch/takasago").text
        assert "🏠 伊丹(自宅)" in self._get("watch/itami").text

    def test_unknown_watch_detail_redirects_to_the_overview(self):
        with patch.object(home_status_service, "get_sensor_data_today", return_value=pd.DataFrame()):
            patches = _stub_loaders()
            for p in patches:
                p.start()
            try:
                with _client() as client:
                    res = client.get(f"{config.DASHBOARD_BASE_PATH}/watch/nope", follow_redirects=False)
            finally:
                for p in patches:
                    p.stop()
        assert res.status_code == 302 and res.headers["location"] == f"{config.DASHBOARD_BASE_PATH}/watch"

    def test_old_hour_detail_url_is_sent_to_the_detail_page(self):
        """ヒートマップのマスの古いURL(`/watch?date=&hour=&src=`)は詳細画面の同じ時間へ送る。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with _client() as client:
                res = client.get(
                    f"{config.DASHBOARD_BASE_PATH}/watch?date=2026-10-02&hour=7&src=takasago&hdev=pir",
                    follow_redirects=False,
                )
        finally:
            for p in patches:
                p.stop()
        assert res.status_code == 302
        assert res.headers["location"] == (
            f"{config.DASHBOARD_BASE_PATH}/watch/takasago?date=2026-10-02&hour=7&hdev=pir#hour-log"
        )

    def test_life_page_shows_the_life_group_cards_only(self):
        res = self._get("life")
        assert "💰 今月の電気代" in res.text
        assert "👵 高砂 (実家)" not in res.text

    def test_power_page_shows_this_month_summary_and_daily_cost(self):
        """不具合修正: 電気代カードをタップしても、以前は同じカードの再掲だけで
        詳細と呼べる情報が無かった。カードのタップ先(「⚡ 電気」ページ)の先頭に今月のまとめ
        (今月の累計・先月の同じ日までとの比較・今月の日別)が表示されること。"""
        from services import power_analysis_service
        report = {"days": 30, "unit_price": 31.0, "generated_at": datetime(2026, 9, 19, 12), "meter_has_data": False,
                  "monthly": [], "comparison": None, "daily": [], "temp_bands": [],
                  "weather": {"has_data": False, "latest_date": None, "location": "伊丹"},
                  "tv": {"configured": False, "has_data": False}}
        with patch.object(power_analysis_service, "build_power_report", return_value=report):
            res = self._get("power")
        text = res.text
        assert text.index("🧾 今月のまとめ") < text.index("📈 月ごとの推移")
        assert "¥1,234" in text and "¥1,000" in text and "+234円(+23%)" in text
        assert "今月の日別を見る(1日)" in text and "cost-history" in text and "123円" in text

    def test_sys_page_shows_overall_summary_and_maintenance_actions(self):
        res = self._get("sys")
        assert "すべて正常" in res.text or "確認が必要です" in res.text
        assert "システム再起動" in self._get("sys/admin").text
        assert "今すぐバックアップ" in self._get("sys/backup").text

    def test_sys_page_shows_nas_capacity_history(self):
        """不具合修正: NASカードをタップしても容量の履歴を見る手段が無かった。"""
        res = self._get(
            "sys/nas",
            load_nas_history=pd.DataFrame({
                "timestamp": [pd.Timestamp("2026-09-14"), pd.Timestamp("2026-09-15")],
                "percent": [30.0, 42.0],
                "free_gb": [120, 100],
            }),
        )
        assert 'id="nas-history"' in res.text
        assert "<svg" in res.text
        assert "現在 42%" in res.text

    @pytest.mark.parametrize("query,expected_days", [
        ("", 30), ("?nas_days=7", 7), ("?nas_days=90", 90), ("?nas_days=5", 30), ("?nas_days=abc", 30), ("?nas_days=", 30),
    ])
    def test_sys_page_nas_period_selects_the_history_range(self, query, expected_days):
        """?nas_days=7|30|90で期間を切り替える。範囲外・数値でない値は既定の30日(422にしない)。
        一覧(`/sys?nas_days=`)の古いURLもNASの詳細画面へ同じ指定のまま送る。"""
        for page in ("sys/nas", "sys"):
            with patch.object(home_status_service, "get_nas_history", return_value=pd.DataFrame()) as mock_load:
                res = self._get(f"{page}{query}")
            if page == "sys" and not query:
                assert mock_load.call_count == 0  # 一覧ではグラフを描かない
                continue
            assert res.status_code == 200
            mock_load.assert_called_once_with(expected_days)
            assert "表示期間" in res.text

    def test_watch_page_device_filter_param_is_accepted(self):
        res = self._get("watch/takasago?device=no-such-device")
        assert res.status_code == 200
        assert 'name="device"' not in res.text or "すべての機器" in res.text

    def test_updates_page_is_reached_from_the_system_page(self):
        """「アップデート」は下部ナビ・ホームには常設せず、システムの「🛠️ 管理者向け」から開く。"""
        res = self._get("sys/admin")
        assert f'href="{config.DASHBOARD_BASE_PATH}/updates"' in res.text
        assert "アップデート" in res.text

    def test_updates_page_lists_local_apps_and_filters_by_app(self):
        res = self._get("updates")
        assert res.status_code == 200
        assert "ダッシュボード" in res.text and "ファミクエ" in res.text
        quest_only = self._get("updates?app=quest")
        assert "release-badge-quest" in quest_only.text
        assert "release-badge-dashboard" not in quest_only.text

    def test_updates_page_ignores_an_unknown_app_filter(self):
        res = self._get("updates?app=nonsense")
        assert res.status_code == 200
        assert "release-badge-dashboard" in res.text and "release-badge-quest" in res.text

    def test_life_page_cost_card_links_to_the_power_page(self):
        res = self._get("life")
        card = res.text.split("💰 今月の電気代", 1)[0].rsplit("<a ", 1)[1]
        assert f'href="{config.DASHBOARD_BASE_PATH}/power"' in card

    @pytest.mark.parametrize("query,expected_days", [
        ("", 30), ("?days=60", 60), ("?days=90", 90), ("?days=7", 30), ("?days=abc", 30), ("?days=", 30),
    ])
    def test_power_page_selects_the_period(self, query, expected_days):
        """?days=30|60|90で期間を切り替える。範囲外・数値でない値は既定の30日(422にしない)。"""
        from services import power_analysis_service
        seen = []
        with patch.object(power_analysis_service, "build_power_report",
                          side_effect=lambda days, now=None: seen.append(days) or {
                              "days": days, "unit_price": 31.0, "generated_at": None, "meter_has_data": False,
                              "monthly": [], "comparison": None, "daily": [], "temp_bands": [],
                              "weather": {"has_data": False, "latest_date": None, "location": "伊丹"},
                              "tv": {"configured": False, "has_data": False}}):
            res = self._get(f"power{query}")
        assert res.status_code == 200 and seen == [expected_days]
        assert "⚡ 電気" in res.text and "くらしへ戻る" in res.text

    def test_sys_page_restart_button_is_disabled_until_confirmed(self):
        res = self._get("sys/admin")
        assert 'id="restartBtn" disabled' in res.text

    def test_sys_page_deploy_button_is_disabled_until_confirmed(self):
        res = self._get("sys/admin")
        assert 'id="deployBtn" disabled' in res.text
        assert 'id="deployConfirm"' in res.text
        # API呼び出しは外部JS(#949)にある。ページはそれを参照し、JS側にエンドポイントがある。
        assert "maintenance.js" in res.text
        js = (Path(dashboard_page_service._STATIC_DIR) / "maintenance.js").read_text(encoding="utf-8")
        assert "/api/system/deploy" in js
        assert "/api/system/deploy/status" in js


class TestStatusCacheOnTheServerSide:
    """`unified_server` には `st.cache_data` が無いため、同じTTLのメモを自前で持つ。"""

    def test_repeated_requests_do_not_read_the_db_again(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "load_sensor_data",
                              return_value=pd.DataFrame()) as mock_load, \
                 _client() as client:
                client.get(f"{config.DASHBOARD_BASE_PATH}/")
                client.get(f"{config.DASHBOARD_BASE_PATH}/")
        finally:
            for p in patches:
                p.stop()

        # 共通キャッシュ(直近N件)を1回。2回目は増えない。
        assert mock_load.call_count == 1

    def test_sub_pages_share_the_same_cache_as_the_home_page(self):
        """見守り/くらし/システムページを開いても、DB読み取り回数が増えないこと。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "load_sensor_data",
                              return_value=pd.DataFrame()) as mock_load, \
                 _client() as client:
                client.get(f"{config.DASHBOARD_BASE_PATH}/")
                client.get(f"{config.DASHBOARD_BASE_PATH}/watch")
                client.get(f"{config.DASHBOARD_BASE_PATH}/sys")
                client.get(f"{config.DASHBOARD_BASE_PATH}/watch")
        finally:
            for p in patches:
                p.stop()

        # 共通キャッシュ(直近N件)は1回だけ。見守りページのヒートマップ用に「今日の全件」を
        # 1回だけ別に取る(これもTTLキャッシュ済みで、見守りを再度開いても増えない)。
        limit_calls = [c for c in mock_load.call_args_list if "limit" in c.kwargs]
        day_calls = [c for c in mock_load.call_args_list if "day" in c.kwargs]
        assert len(limit_calls) == 1
        assert len(day_calls) == 1
        assert len(mock_load.call_args_list) == 2

    def test_daily_cost_is_computed_only_on_the_power_page(self):
        """Issue #1178: 日別の電気代(最大31回の集計)は電気ページを開いたときだけ取る。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "calculate_daily_cost_series",
                              return_value=[(date(2026, 9, 19), 123)]) as mock_daily, \
                 patch("services.power_analysis_service.build_power_report", return_value=None), \
                 patch("services.power_page_service.render_power_page", return_value="<html></html>") as mock_render, \
                 _client() as client:
                for path in ("/", "/watch", "/sys", "/life", "/status"):
                    client.get(f"{config.DASHBOARD_BASE_PATH}{path}")
                assert mock_daily.call_count == 0
                client.get(f"{config.DASHBOARD_BASE_PATH}/power")
                client.get(f"{config.DASHBOARD_BASE_PATH}/power")
        finally:
            for p in patches:
                p.stop()

        assert mock_daily.call_count == 1  # 2回目は10分キャッシュから
        assert mock_render.call_args.kwargs["daily_cost_rows"] == [(date(2026, 9, 19), 123)]

    def test_watch_page_with_date_loads_that_day_from_the_db(self):
        """`?date=`指定時は共通キャッシュ(直近3000件)ではなく、指定日をDBから取る。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "load_sensor_data",
                              return_value=pd.DataFrame()) as mock_load, \
                 _client() as client:
                res = client.get(f"{config.DASHBOARD_BASE_PATH}/watch/takasago?date=2026-10-01")
        finally:
            for p in patches:
                p.stop()

        assert res.status_code == 200
        # ログは指定日をDBから取る(画面先頭のカード用に、共通キャッシュも1回だけ読む)
        mock_load.assert_any_call(day=date(2026, 10, 1))
        assert [c for c in mock_load.call_args_list if "day" in c.kwargs] == [((), {"day": date(2026, 10, 1)})]
        assert 'value="2026-10-01"' in res.text

    def test_watch_page_with_invalid_date_falls_back_to_normal_view(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "load_sensor_data",
                              return_value=pd.DataFrame()) as mock_load, \
                 _client() as client:
                res = client.get(f"{config.DASHBOARD_BASE_PATH}/watch/itami?date=not-a-date")
        finally:
            for p in patches:
                p.stop()

        assert res.status_code == 200
        # 日付指定が無効なら通常表示: 共通キャッシュ(直近N件)+ヒートマップ用の今日の全件
        mock_load.assert_any_call(limit=home_status_service.MOBILE_SENSOR_ROW_LIMIT)
        assert len(mock_load.call_args_list) == 2

    def test_failures_are_not_cached(self):
        """失敗をキャッシュすると、復旧しても60秒間は壊れたままになる。"""
        with patch.object(home_status_service.analysis_service, "get_memory_usage",
                          side_effect=RuntimeError("psutil failed")) as failing:
            assert home_status_service._cached("memory", failing) is None
            assert home_status_service._cached("memory", failing) is None

        assert failing.call_count == 2


class TestOneSourceOfTruth:
    def test_the_service_does_not_depend_on_streamlit(self):
        """`unified_server.py` から使うため、Streamlit を持ち込まないこと。"""
        import ast
        import inspect

        for module in (home_status_service, dashboard_page_service):
            tree = ast.parse(inspect.getsource(module))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])

            assert "streamlit" not in imported, module.__name__


class TestMobilePageHtmlSafety:
    def test_card_titles_and_values_are_escaped(self):
        """Issue #378 と同じ理由。DB・スクレイピング由来の文字列が混ざりうる。"""
        cards = [home_status_service.StatusCard("<script>alert(1)</script>", "<img src=x>", "theme-gray", group="watch")]
        page = dashboard_page_service.render_home_page(
            cards, NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            refresh_sec=60,
        )

        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page

    def test_intentional_html_is_kept_when_the_card_asks_for_it(self):
        """`value_is_html=True` を指定した呼び出し元だけがHTML断片を埋め込めること。"""
        cards = [home_status_service.StatusCard("🔧 テスト", "<b>3</b>台", "theme-green",
                                                value_is_html=True, group="watch")]
        page = dashboard_page_service.render_home_page(
            cards, NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            refresh_sec=60,
        )

        assert "<b>3</b>台" in page

    def test_links_are_large_enough_to_tap(self):
        page = dashboard_page_service.render_home_page(
            [], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            refresh_sec=60,
        )

        assert "dashboard.css" in page  # 共通CSSは外部ファイル(#949)
        assert "min-height: 44px" in _dashboard_css()


class TestCardsLinkToTheirDetail:
    """A: 異常に気づいてから詳細を開くまでを1タップにする。"""

    def _collect(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            return home_status_service.collect_status_cards(NOW)
        finally:
            for p in patches:
                p.stop()

    def test_every_card_links_to_a_page_that_exists(self):
        page, _ = self._collect()
        html_out = home_status_service.render_status_grid_html(page, dashboard_path="/dashboard/")

        import re
        hrefs = re.findall(r'<a class="status-card [^"]*" href="([^"]+)"', html_out)
        assert len(hrefs) == EXPECTED_CARD_COUNT, "すべてのカードがリンクになっていること"
        for href in hrefs:
            assert href.startswith("/dashboard/")
            path = href.split("#", 1)[0].split("?", 1)[0]
            # 各ページ(見守り・くらし・電気・システム)か、その下の詳細画面
            assert path.removeprefix("/dashboard/") in (
                "watch/takasago", "watch/itami", "watch/live", "life", "power", "sys/server", "sys/nas",
            ), href

    def test_cards_point_at_the_page_that_actually_shows_them(self):
        """リンク先が「そのカードの詳細が載っているページ」であること。"""
        cards, _ = self._collect()
        by_title = {card.title: card.tab for card in cards}

        assert by_title["👵 高砂 (実家)"] == "watch"
        assert by_title["🎥 カメラ"] == "watch"
        assert by_title["💰 今月の電気代"] == "power"  # タップで「⚡ 電気」ページ(今月のまとめ)
        assert by_title["🗄️ NAS"] == "sys"

    def test_watch_group_cards_link_to_different_places_on_the_page(self):
        """不具合修正の回帰テスト: 以前は高砂・伊丹・駐車場・カメラの4枚が全部
        `tab="watch"`だけを持ち、`anchor`が無かったため、どれをタップしても
        同じURL(見守りページの先頭=カメラ映像)にしか飛べなかった。"""
        cards, _ = self._collect()
        watch_titles = ("👵 高砂 (実家)", "🏠 伊丹 (自宅)", "🚗 駐車場", "🎥 カメラ")
        hrefs = {
            card.title: home_status_service.card_detail_href(card, "/dashboard/")
            for card in cards if card.title in watch_titles
        }

        assert set(hrefs) == set(watch_titles)
        assert len(set(hrefs.values())) == len(watch_titles), "4枚のリンク先が全部違うこと"
        for href in hrefs.values():
            assert href.startswith("/dashboard/watch")

    def test_nas_card_links_to_the_history_page(self):
        """不具合修正: NASカードをタップしても容量履歴を見る手段が無かったため、
        容量履歴のあるシステムのNASの詳細画面へ飛ぶ`anchor`を付けた。"""
        cards, _ = self._collect()
        (nas_card,) = [card for card in cards if card.title == "🗄️ NAS"]

        href = home_status_service.card_detail_href(nas_card, "/dashboard/")

        assert href == "/dashboard/sys/nas"

    def test_the_link_is_relative_to_the_viewing_origin(self):
        """固定URLを埋めるとLAN内のIPと公開ドメインのどちらかで繋がらなくなる。"""
        card = home_status_service.StatusCard("t", "v", "theme-gray", tab="watch")

        assert home_status_service.card_detail_href(card, "/dashboard/") == "/dashboard/watch"
        assert home_status_service.card_detail_href(
            home_status_service.StatusCard("t", "v", "theme-gray"), "/dashboard/"
        ) is None

    def test_anchor_is_appended_after_the_tab_url(self):
        card = home_status_service.StatusCard("t", "v", "theme-gray", tab="watch", anchor="#takasago-log")

        assert home_status_service.card_detail_href(card, "/dashboard/") == "/dashboard/watch#takasago-log"


class TestStatusGridOrder:
    def test_the_grid_order_is_not_changed(self):
        """位置で覚えている画面を動かさない(カードの並びは判定結果に関わらず固定)。"""
        cards = [
            home_status_service.StatusCard("1番目", "v", "theme-green"),
            home_status_service.StatusCard("2番目", "v", "theme-red"),
        ]

        grid = home_status_service.render_status_grid_html(cards)

        assert grid.index("1番目") < grid.index("2番目")


class TestAlertSummaryRemoved:
    """不具合修正: ホームページの「気になること」要約行は不要という要望により削除した。
    カード自体が赤・黄の色で状態を示しているため、要約行が無くても情報は失われない。"""

    def test_the_home_page_no_longer_shows_the_alert_line(self):
        page = dashboard_page_service.render_home_page(
            [home_status_service.StatusCard("異常", "v", "theme-red")], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            refresh_sec=60,
        )

        assert "気になること" not in page

    def test_render_alerts_html_no_longer_exists(self):
        """機能を隠すのではなく削除したことの確認(使われないコードを残さない)。"""
        assert not hasattr(home_status_service, "render_alerts_html")
        assert not hasattr(home_status_service, "summarize_alerts")


class TestDarkMode:
    """B: 夜にスマホで見ると白背景が眩しい。"""

    def _page(self):
        return dashboard_page_service.render_home_page(
            [], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            refresh_sec=60,
        )

    def test_the_page_follows_the_device_setting(self):
        page = self._page()

        assert "dashboard.css" in page
        css = _dashboard_css()
        assert "prefers-color-scheme: dark" in css
        assert "color-scheme: light dark" in css


class TestPartialRefresh:
    """C: 自動更新でページ全体を読み込み直さない。"""

    def _get(self, path):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(path)
        finally:
            for p in patches:
                p.stop()

    def test_the_cards_can_be_fetched_on_their_own(self):
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/status")

        assert res.status_code == 200
        assert res.text.count('class="status-card') == HOME_CARD_COUNT
        assert res.text.startswith(f'<div id="{home_status_service.STATUS_SECTION_ID}"')
        # 断片なので、ページ全体の要素は含まない
        assert "<html" not in res.text
        assert "<style" not in res.text

    def test_the_fragment_replaces_itself(self):
        """差し替え後も同じidが残らないと、2回目以降の更新先を見失う。"""
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/status")

        assert res.text.count(f'id="{home_status_service.STATUS_SECTION_ID}"') == 1

    def test_the_fragment_carries_the_fetch_time_but_does_not_show_it(self):
        """取得時刻・自動更新の間隔は要望で画面から外した(見ても行動が変わらない)。
        不具合調査用に、差し替える範囲の属性としてだけ持つ。"""
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/status")

        assert "data-fetched-at=" in res.text
        assert "時点" not in res.text and "秒ごとに自動更新" not in res.text

    def test_the_script_sends_credentials(self):
        """Cloudflare Access の内側にあるため、Cookie を送らないと弾かれる。"""
        page = dashboard_page_service.render_home_page(
            [], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            refresh_sec=60,
        )

        assert 'credentials: "same-origin"' in page

    def test_a_failed_update_keeps_the_last_values(self):
        """圏外・サーバー再起動の最中に画面が空になると、かえって困る。"""
        page = dashboard_page_service.render_home_page(
            [], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            refresh_sec=60,
        )

        assert 'classList.add("stale")' in page
        assert "#status.stale" in _dashboard_css()

    def test_repeated_fragment_requests_do_not_read_the_db_again(self):
        """断片の取得経路もページ本体と同じTTLキャッシュを通ること。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(
                home_status_service.analysis_service, "load_sensor_data",
                return_value=pd.DataFrame(),
            ) as load, _client() as client:
                client.get(f"{config.DASHBOARD_BASE_PATH}/")
                client.get(f"{config.DASHBOARD_BASE_PATH}/status")
                client.get(f"{config.DASHBOARD_BASE_PATH}/status")
        finally:
            for p in patches:
                p.stop()

        # 直近N件を1回。断片を何度取っても増えない(「今日の動き」は見守りページへ移したので、
        # ホームでは今日の全件を読まない)。
        assert load.call_count == 1


class TestSupportingValues:
    """D: 「いまの値」だけでは判断できないカードに、比べる相手を添える。"""

    def test_the_card_shows_the_supporting_line(self):
        card = home_status_service.StatusCard("💰 今月の電気代", "⚡ 1,234 円", "theme-blue",
                                              sub="先月同日 1,000円 (+234)")

        card_html = home_status_service.render_status_card_html(
            card.title, card.value, card.theme, sub=card.sub
        )

        assert 'class="status-sub"' in card_html
        assert "先月同日 1,000円 (+234)" in card_html

    def test_a_card_without_one_stays_as_it_was(self):
        card_html = home_status_service.render_status_card_html("t", "v", "theme-gray")

        assert "status-sub" not in card_html

    def test_the_supporting_line_is_escaped(self):
        """DB・スクレイピング由来の文字列が混ざりうる経路と同じ扱いにする。"""
        card_html = home_status_service.render_status_card_html(
            "t", "v", "theme-gray", sub="<script>alert(1)</script>"
        )

        assert "<script>" not in card_html
        assert "&lt;script&gt;" in card_html

    def test_the_time_is_written_the_way_a_person_reads_it(self):
        now = datetime.fromisoformat("2026-09-19T12:00:00+09:00")
        fmt = home_status_service._format_moment

        assert fmt(pd.Timestamp("2026-09-19T09:40:00+09:00"), now) == "09:40"
        assert fmt(pd.Timestamp("2026-09-18T18:40:00+09:00"), now) == "昨日 18:40"
        assert fmt(pd.Timestamp("2026-09-15T18:40:00+09:00"), now) == "9/15 18:40"
        assert fmt(None, now) is None

    def test_the_colour_and_the_time_cannot_disagree(self):
        """判定と補足表示が同じ行を見ていること(別々に絞ると食い違う)。"""
        df = pd.DataFrame({
            "location": ["高砂", "高砂"],
            "contact_state": ["open", "closed"],
            "timestamp": [
                pd.Timestamp("2026-09-19T11:30:00+09:00"),
                pd.Timestamp("2026-09-19T11:55:00+09:00"),
            ],
        })
        now = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

        value, theme = home_status_service.get_takasago_status(df, now)
        sub = home_status_service.describe_takasago(df, now)

        # 判定は "open" の行(11:30)を見ているので、補足も同じ行でなければならない
        # ("closed" の 11:55 を拾うと、色は緑なのに時刻だけ新しいという食い違いになる)
        assert theme == "theme-green" and "元気" in value
        assert sub == "最終検知 11:30"

    def test_the_parking_camera_says_when_it_last_detected_motion(self):
        df = pd.DataFrame({
            "device_type": [home_status_service.CAMERA_DEVICE_TYPE],
            "movement_state": ["ON"],
            "device_id": [home_status_service.PARKING_CAMERA_ID],
            "timestamp": [pd.Timestamp("2026-09-19T08:15:00+09:00")],
        })
        now = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

        assert home_status_service.describe_parking(df, now) == "最終検知 08:15"

    def test_the_cost_card_compares_with_last_month(self):
        assert home_status_service.describe_cost(12345, 11000) == "先月同日 11,000円 (+1,345)"
        assert home_status_service.describe_cost(9000, 11000) == "先月同日 11,000円 (-2,000)"

    def test_the_cost_card_says_nothing_without_a_comparison(self):
        """初月・取得失敗のときに「先月同日 0円」と出すと、使っていないように見える。"""
        assert home_status_service.describe_cost(12345, 0) is None
        assert home_status_service.describe_cost(12345, None) is None

    def test_missing_material_only_drops_the_supporting_line(self):
        """補足の材料が取れなくても、カードの値と色は変わらないこと。"""
        empty = pd.DataFrame()
        cards = home_status_service.build_status_cards(
            datetime.fromisoformat("2026-09-19T12:00:00+09:00"),
            empty, None,
            {"percent": 42.0}, 1234,
        )

        assert len(cards) == EXPECTED_CARD_COUNT
        # サーバーだけは、値を「正常」にした代わりにメモリ使用率を補足へ出す
        assert all(card.sub is None for card in cards if card.title != "🖥️ サーバー")
        (server,) = [card for card in cards if card.title == "🖥️ サーバー"]
        assert (server.value, server.sub) == ("🟢 正常", "メモリ 42%")

    def test_the_page_shows_them_too(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with _client() as client:
                # 電気代はくらし、サーバーはシステムのカード(ホームのタイルは補足を出さない)
                life = client.get(f"{config.DASHBOARD_BASE_PATH}/life").text
                sys_page = client.get(f"{config.DASHBOARD_BASE_PATH}/sys").text
        finally:
            for p in patches:
                p.stop()

        assert 'class="status-sub"' in life and 'class="status-sub"' in sys_page
        assert "先月同日 1,000円" in life
        assert "ディスク 55%" in sys_page


class TestCardColoursSurviveLinking:
    """カードをリンクにしたときに、状態を表すテーマ色が消えないこと。"""

    def test_the_link_style_does_not_override_the_theme_colour(self):
        assert "color: inherit" not in home_status_service.STATUS_CARD_CSS

    def test_the_link_has_no_underline(self):
        """下線が付くと、テーマ色で示している状態が読み取りにくくなる。"""
        assert "text-decoration: none" in home_status_service.STATUS_CARD_CSS


class TestExternalLinkHealth:
    """ホームの「システム・サービス」のアプリのタイル(ファミクエ・あさノート・よるノート)の稼働状況による
    色分け(緑=稼働中・赤=停止中・灰=確認中)。以前はホームの最後の「よく使うリンク」だった。"""

    def _home(self, path=""):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(f"{config.DASHBOARD_BASE_PATH}{path}")
        finally:
            for p in patches:
                p.stop()

    @staticmethod
    def _tile(text, title):
        """タイトルを含むタイル(`<a class="status-card ...">`)の開始タグ〜タイトルの直前。"""
        return text.split("</style>", 1)[-1].split(title, 1)[0].rsplit("<a ", 1)[1]

    def test_healthy_apps_are_green_with_label(self):
        res = self._home("/")
        for title in ("⚔️ ファミクエ", "📝 あさノート", "🌙 よるノート"):
            assert "theme-green" in self._tile(res.text, title), title
        assert res.text.count("🟢 稼働中") == 3

    def test_down_app_is_red_and_other_stays_green(self, monkeypatch):
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: False)
        res = self._home("/")
        assert "theme-red" in self._tile(res.text, "📝 あさノート")
        assert "theme-green" in self._tile(res.text, "⚔️ ファミクエ")
        assert "🔴 停止中" in res.text

    def test_down_app_is_still_tappable(self, monkeypatch):
        monkeypatch.setattr(home_status_service, "check_quest_app_health", lambda: False)
        res = self._home("/")
        tile = self._tile(res.text, "⚔️ ファミクエ")
        assert 'class="status-card theme-red" href="/quest"' in tile

    def test_app_tiles_open_in_a_new_tab_but_the_others_do_not(self):
        res = self._home("/")
        for title in ("⚔️ ファミクエ", "📝 あさノート", "🌙 よるノート"):
            tile = self._tile(res.text, title)
            assert 'target="_blank"' in tile and 'rel="noopener"' in tile, title
        for title in ("🖥️ サーバー", "🗄️ NAS", "🎥 録画"):
            assert 'target="_blank"' not in self._tile(res.text, title), title

    def test_note_urls_point_to_the_deployed_apps(self):
        assert config.ASA_NOTE_URL == "https://asa-note.vercel.app/"
        assert config.YORU_NOTE_URL == "https://yorunote-mm.vercel.app/"

    def test_down_yoru_note_is_red_and_only_that_tile(self, monkeypatch):
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: "yorunote" not in url)
        text = self._home("/").text
        assert "theme-red" in self._tile(text, "🌙 よるノート")
        assert "theme-green" in self._tile(text, "📝 あさノート")

    def test_status_fragment_keeps_the_tiles_so_color_refreshes(self, monkeypatch):
        """自動更新はこのフラグメントで範囲ごと差し替わる。タイルが入っていないと更新で消える。"""
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: False)
        res = self._home("/status")
        assert "🖥️ システム・サービス" in res.text
        assert "theme-red" in self._tile(res.text, "📝 あさノート")
        assert 'href="/quest"' in res.text

    def test_tiles_are_inside_the_refreshed_section(self):
        res = self._home("/")
        section = res.text.split(f'id="{home_status_service.STATUS_SECTION_ID}"', 1)[1]
        assert "ファミクエ" in section and "あさノート" in section

    def test_apps_detail_page_lists_the_links_with_their_state(self, monkeypatch):
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: False)
        res = self._home("/sys/apps")
        assert res.text.count('class="external-card') == 3
        assert 'class="external-card external-down" href="https://asa-note.vercel.app/"' in res.text
        assert "🔴 2件停止" in res.text  # 一覧と同じカードを先頭に出す

    def test_render_without_health_keeps_plain_style(self):
        html = dashboard_page_service._render_external_links("/quest", "https://x.example/", None)
        assert "external-up" not in html and "external-down" not in html
        assert "稼働中" not in html and "停止中" not in html

    def test_slow_check_does_not_hold_the_home_page(self, monkeypatch):
        """外部サイトの応答待ちでホームの最初の描画を遅らせない(起動画面が長く出ていた原因)。
        間に合わないアプリは「停止中」ではなく「確認中」(灰色)で出し、ページが数秒後に取り直す。"""
        monkeypatch.setattr(home_status_service, "HOME_RENDER_BUDGET_SEC", 0.1)
        monkeypatch.setattr(
            home_status_service, "check_asa_note_health", lambda url: time.sleep(1.0) or "yorunote" not in url
        )
        started = time.monotonic()
        res = self._home("/")
        assert time.monotonic() - started < 0.9
        assert "theme-gray" in self._tile(res.text, "📝 あさノート")
        assert "⚪ 確認中" in res.text
        assert 'data-pending="1"' in res.text

        time.sleep(1.2)  # 裏での確認が終わったあとの取り直し
        res = self._home("/status")
        assert "theme-red" in self._tile(res.text, "🌙 よるノート") and 'data-pending="1"' not in res.text

    def test_health_is_cached_between_requests(self, monkeypatch):
        calls = []
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: calls.append(url) or True)
        home_status_service.collect_link_health("https://x.example/")
        home_status_service.collect_link_health("https://x.example/")
        assert calls == ["https://x.example/"]


class TestCheckQuestAppHealth:
    def _dist(self, tmp_path, monkeypatch, with_index=True):
        if with_index:
            (tmp_path / "index.html").write_text("<html></html>")
        monkeypatch.setattr(config, "QUEST_DIST_DIR", str(tmp_path))

    def test_ok_when_index_exists_and_db_readable(self, tmp_path, monkeypatch, isolated_db):
        self._dist(tmp_path, monkeypatch)
        assert _REAL_CHECK_QUEST() is True

    def test_down_when_index_html_missing(self, tmp_path, monkeypatch, isolated_db):
        self._dist(tmp_path, monkeypatch, with_index=False)
        assert _REAL_CHECK_QUEST() is False

    def test_down_when_db_unreachable(self, tmp_path, monkeypatch, isolated_db):
        self._dist(tmp_path, monkeypatch)
        monkeypatch.setattr(config, "SQLITE_DB_PATH", str(tmp_path / "missing" / "no.db"))
        assert _REAL_CHECK_QUEST() is False


class TestCheckAsaNoteHealth:
    class _Resp:
        def __init__(self, status):
            self.status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    @pytest.mark.parametrize("status,expected", [(200, True), (302, True), (404, False), (503, False)])
    def test_status_codes(self, monkeypatch, status, expected):
        monkeypatch.setattr(home_status_service.requests, "get", lambda url, **kw: self._Resp(status))
        assert _REAL_CHECK_ASA_NOTE("https://x.example/") is expected

    def test_connection_error_is_down(self, monkeypatch):
        def boom(url, **kw):
            raise home_status_service.requests.exceptions.ConnectionError("no route")

        monkeypatch.setattr(home_status_service.requests, "get", boom)
        assert _REAL_CHECK_ASA_NOTE("https://x.example/") is False

    def test_uses_short_timeout(self, monkeypatch):
        seen = {}

        def fake_get(url, **kw):
            seen.update(kw)
            return self._Resp(200)

        monkeypatch.setattr(home_status_service.requests, "get", fake_get)
        _REAL_CHECK_ASA_NOTE("https://x.example/")
        assert seen["timeout"] == home_status_service.ASA_NOTE_HEALTH_TIMEOUT_SEC
