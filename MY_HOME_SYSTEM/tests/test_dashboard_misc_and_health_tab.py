# MY_HOME_SYSTEM/tests/test_dashboard_misc_and_health_tab.py
"""
views/dashboard/misc_tab.py の写真・駐輪場セクションと
views/dashboard/health_tab.py の回帰テスト(Issue #754)。

HTMLエスケープ(Issue #378)は tests/test_misc_tab_html_escaping.py が既に
検証しているので、本ファイルはそれ以外の分岐(データ有無・時間帯によるルート
切り替え・列の出し分け)を対象にする。`.coveragerc` の omit から
`views/dashboard/*` を外すのに合わせて追加した。
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pandas as pd
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard import health_tab, misc_tab


def _mock_st():
    mock = MagicMock()
    mock.columns.side_effect = lambda spec, **kwargs: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    return mock


class TestRenderTrafficRouteSelection:
    """Issue #451: 出勤/帰宅ルートは時刻で切り替わる。境界(4時・12時・24時)の判定。"""

    def _run_at(self, hour):
        # Issue #658: 実時刻に任せず freezegun で固定する。判定は JST の
        # 「時」なので、JST のオフセット付きで凍結する。
        mock_st = _mock_st()
        with freeze_time(f"2026-09-19 {hour:02d}:00:00+09:00"), \
             patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.train_service, "get_jr_traffic_status",
                          return_value={"宝塚線": {"status": "平常運転", "detail": ""},
                                        "神戸線": {"status": "平常運転", "detail": ""}}), \
             patch.object(misc_tab, "_render_route_search") as mock_route:
            misc_tab.render_traffic()
        return mock_st, mock_route

    def test_morning_shows_the_commute_route(self):
        _, mock_route = self._run_at(8)
        assert mock_route.call_args.args[1:] == ("伊丹(兵庫県)", "長岡京", "📤 出勤ルート")

    def test_boundary_hour_four_is_already_the_commute_route(self):
        _, mock_route = self._run_at(misc_tab.COMMUTE_ROUTE_START_HOUR)
        assert mock_route.call_args.args[3] == "📤 出勤ルート"

    def test_noon_switches_to_the_return_route(self):
        _, mock_route = self._run_at(misc_tab.COMMUTE_ROUTE_END_HOUR)
        assert mock_route.call_args.args[1:] == ("長岡京", "伊丹(兵庫県)", "📥 帰宅ルート")

    def test_late_night_shows_the_return_route_with_a_caption(self):
        mock_st, mock_route = self._run_at(2)
        assert mock_route.call_args.args[3] == "📥 帰宅ルート"
        captions = [str(c.args[0]) for c in mock_st.caption.call_args_list if c.args]
        assert any("深夜帯" in c for c in captions), captions

    def test_delayed_line_is_colored_red(self):
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.train_service, "get_jr_traffic_status",
                          return_value={"宝塚線": {"status": "遅延", "detail": "人身事故", "is_delay": True},
                                        "神戸線": {"status": "平常運転", "detail": ""}}), \
             patch.object(misc_tab, "_render_route_search"):
            misc_tab.render_traffic()

        html_out = "\n".join(str(c.args[0]) for c in mock_st.markdown.call_args_list if c.args)
        assert "#d32f2f" in html_out

    def test_unavailable_line_is_not_colored_like_normal_operation(self):
        """Low修正: 取得不可を平常運転と同じ緑で出さない(遅延見逃し防止)。"""
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.train_service, "get_jr_traffic_status",
                          return_value={"宝塚線": {"status": "取得不可", "detail": "", "is_unavailable": True},
                                        "神戸線": {"status": "取得不可", "detail": "", "is_unavailable": True}}), \
             patch.object(misc_tab, "_render_route_search"):
            misc_tab.render_traffic()

        html_out = "\n".join(str(c.args[0]) for c in mock_st.markdown.call_args_list if c.args)
        assert "#757575" in html_out
        assert "#2e7d32" not in html_out


class TestRenderRouteSearchFailure:
    def test_failed_lookup_shows_a_warning_instead_of_an_empty_card(self):
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.train_service, "get_route_info",
                          return_value={"summary": "取得失敗"}):
            misc_tab._render_route_search(MagicMock(), "A", "B", "icon")

        mock_st.warning.assert_called_once()

    def test_yahoo_link_is_shown_only_when_a_url_is_returned(self):
        route = {"summary": "取得成功", "departure": "08:00", "arrival": "08:30",
                 "duration": "30分", "cost": "400円", "transfer": "1回",
                 "details": [], "url": "https://example.invalid/route"}
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.train_service, "get_route_info", return_value=route):
            misc_tab._render_route_search(MagicMock(), "A", "B", "icon")
        mock_st.link_button.assert_called_once()

        route_without_url = dict(route, url="")
        mock_st2 = _mock_st()
        with patch.object(misc_tab, "st", mock_st2), \
             patch.object(misc_tab.train_service, "get_route_info", return_value=route_without_url):
            misc_tab._render_route_search(MagicMock(), "A", "B", "icon")
        mock_st2.link_button.assert_not_called()


class TestRenderPhotos:
    def test_no_snapshots_shows_placeholder(self, tmp_path):
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", str(tmp_path)):
            misc_tab.render_photos(pd.DataFrame())

        infos = [str(c.args[0]) for c in mock_st.info.call_args_list if c.args]
        assert "写真なし" in infos

    def test_newest_four_snapshots_are_shown_first(self, tmp_path):
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        # ファイル名の降順 = 新しい順(タイムスタンプ命名)
        for i in range(6):
            (snap_dir / f"2026-09-19_{i:02d}0000.jpg").write_bytes(b"")

        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", str(tmp_path)):
            misc_tab.render_photos(pd.DataFrame())

        # 直近4枚 + 「過去の写真」エクスパンダ内に残り2枚
        assert mock_st.expander.called
        assert mock_st.columns.call_count == 2

    def test_security_log_columns_are_renamed_to_japanese(self):
        df = pd.DataFrame([{
            "timestamp": "2026-09-19 10:00", "friendly_name": "玄関カメラ",
            "classification": "person", "image_path": "/tmp/a.jpg",
        }])
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", "/nonexistent"):
            misc_tab.render_photos(df)

        shown = mock_st.dataframe.call_args.args[0]
        assert list(shown.columns) == ["検知時刻", "デバイス", "検知種別", "画像"]

    def test_optional_columns_are_omitted_when_absent(self):
        df = pd.DataFrame([{"timestamp": "2026-09-19 10:00", "friendly_name": "玄関カメラ"}])
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", "/nonexistent"):
            misc_tab.render_photos(df)

        shown = mock_st.dataframe.call_args.args[0]
        assert list(shown.columns) == ["検知時刻", "デバイス"]

    def test_empty_security_log_shows_reassuring_message(self):
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", "/nonexistent"):
            misc_tab.render_photos(pd.DataFrame())

        infos = [str(c.args[0]) for c in mock_st.info.call_args_list if c.args]
        assert "不審な検知はありません" in infos


class TestRenderBicycle:
    AREA = "JR伊丹駅前(第1)自転車駐車場 (A)"

    def test_empty_dataframe_shows_info_and_returns_early(self):
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st):
            misc_tab.render_bicycle(pd.DataFrame())

        mock_st.info.assert_called_once()
        mock_st.plotly_chart.assert_not_called()

    def test_data_for_untracked_areas_only_warns(self):
        df = pd.DataFrame([{"timestamp": "2026-09-19 08:00", "area_name": "別の駐輪場",
                            "waiting_count": 3, "status_text": "混雑"}])
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st):
            misc_tab.render_bicycle(df)

        mock_st.warning.assert_called_once()
        mock_st.plotly_chart.assert_not_called()

    def test_tracked_area_is_charted_and_latest_row_is_tabled(self):
        df = pd.DataFrame([
            {"timestamp": "2026-09-19 08:00", "area_name": self.AREA, "waiting_count": 3, "status_text": "混雑"},
            {"timestamp": "2026-09-19 09:00", "area_name": self.AREA, "waiting_count": 1, "status_text": "空き"},
        ])
        mock_st = _mock_st()
        with patch.object(misc_tab, "st", mock_st):
            misc_tab.render_bicycle(df)

        mock_st.plotly_chart.assert_called_once()
        latest = mock_st.dataframe.call_args.args[0]
        assert len(latest) == 1
        assert latest.iloc[0]["waiting_count"] == 1


class TestHealthTab:
    def test_all_sections_render_when_data_exists(self):
        df_child = pd.DataFrame([{"timestamp": "t", "child_name": "太郎", "condition": "元気"}])
        df_poop = pd.DataFrame([{"timestamp": "t", "user_name": "太郎", "condition": "普通"}])
        df_food = pd.DataFrame([{"timestamp": "t", "menu_category": "和食"}])

        mock_st = _mock_st()
        with patch.object(health_tab, "st", mock_st):
            health_tab.render(df_child, df_poop, df_food)

        assert mock_st.dataframe.call_count == 3

    def test_empty_sections_are_skipped_but_headings_remain(self):
        mock_st = _mock_st()
        with patch.object(health_tab, "st", mock_st):
            health_tab.render(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

        mock_st.dataframe.assert_not_called()
        headings = [str(c.args[0]) for c in mock_st.markdown.call_args_list if c.args]
        assert any("子供" in h for h in headings)
        assert any("食事" in h for h in headings)

    def test_only_the_expected_columns_are_shown(self):
        df_child = pd.DataFrame([{"timestamp": "t", "child_name": "太郎",
                                  "condition": "元気", "internal_note": "秘密"}])
        mock_st = _mock_st()
        with patch.object(health_tab, "st", mock_st):
            health_tab.render(df_child, pd.DataFrame(), pd.DataFrame())

        shown = mock_st.dataframe.call_args.args[0]
        assert list(shown.columns) == ["timestamp", "child_name", "condition"]
