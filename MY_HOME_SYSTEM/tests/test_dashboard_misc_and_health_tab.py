# MY_HOME_SYSTEM/tests/test_dashboard_misc_and_health_tab.py
"""
views/dashboard/misc_tab.py の写真セクションと
views/dashboard/health_tab.py の回帰テスト(Issue #754)。

データ有無・列の出し分けといった分岐を対象にする。`.coveragerc` の omit から
`views/dashboard/*` を外すのに合わせて追加した。
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard import common as view_common
from views.dashboard import health_tab, misc_tab


def _mock_st():
    mock = MagicMock()
    mock.columns.side_effect = lambda spec, **kwargs: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    return mock


def _patch_st(mock_st):
    """`misc_tab`/`health_tab` と、そこから呼ばれる `view_common` の st をまとめて差し替える。

    表とグラフの描画は `view_common.render_table` / `render_chart` 経由に
    なったため(スマホ向けの列絞り・モードバー無効化を1箇所に寄せた)、
    View モジュール側だけを差し替えても st.dataframe / st.plotly_chart は
    モックに届かない。
    """
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch.object(misc_tab, "st", mock_st))
    stack.enter_context(patch.object(health_tab, "st", mock_st))
    stack.enter_context(patch.object(view_common, "st", mock_st))
    return stack


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
        mock_st.toggle.return_value = True  # 「📂 過去の写真」を開いた状態
        with _patch_st(mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", str(tmp_path)):
            misc_tab.render_photos(pd.DataFrame())

        # 直近4枚 + 「過去の写真」を開いたときの残り2枚
        assert mock_st.toggle.called
        assert mock_st.columns.call_count == 2

    def test_past_photos_are_not_rendered_while_the_section_is_closed(self, tmp_path):
        """折りたたみは `st.expander` ではなく `lazy_section`(toggle)。
        expander は閉じていても中身を実行してしまう(画像16枚の読み込み)。"""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        for i in range(6):
            (snap_dir / f"2026-09-19_{i:02d}0000.jpg").write_bytes(b"")

        mock_st = _mock_st()
        mock_st.toggle.return_value = False
        with _patch_st(mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", str(tmp_path)):
            misc_tab.render_photos(pd.DataFrame())

        assert mock_st.columns.call_count == 1

    def test_security_log_columns_are_renamed_to_japanese(self):
        df = pd.DataFrame([{
            "timestamp": "2026-09-19 10:00", "friendly_name": "玄関カメラ",
            "classification": "person", "image_path": "/tmp/a.jpg",
        }])
        mock_st = _mock_st()
        with _patch_st(mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", "/nonexistent"):
            misc_tab.render_photos(df)

        shown = mock_st.dataframe.call_args.args[0]
        # スマホ対応: image_path(NAS上のフルパス)は列から落とした。画面幅を
        # 大きく超えて横スクロールしないと検知時刻すら読めなくなるため。
        assert list(shown.columns) == ["検知時刻", "デバイス", "検知種別"]

    def test_optional_columns_are_omitted_when_absent(self):
        df = pd.DataFrame([{"timestamp": "2026-09-19 10:00", "friendly_name": "玄関カメラ"}])
        mock_st = _mock_st()
        with _patch_st(mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", "/nonexistent"):
            misc_tab.render_photos(df)

        shown = mock_st.dataframe.call_args.args[0]
        assert list(shown.columns) == ["検知時刻", "デバイス"]

    def test_empty_security_log_shows_reassuring_message(self):
        mock_st = _mock_st()
        with _patch_st(mock_st), \
             patch.object(misc_tab.config, "ASSETS_DIR", "/nonexistent"):
            misc_tab.render_photos(pd.DataFrame())

        infos = [str(c.args[0]) for c in mock_st.info.call_args_list if c.args]
        assert "不審な検知はありません" in infos


class TestHealthTab:
    def test_all_sections_render_when_data_exists(self):
        df_child = pd.DataFrame([{"timestamp": "t", "child_name": "太郎", "condition": "元気"}])
        df_poop = pd.DataFrame([{"timestamp": "t", "user_name": "太郎", "condition": "普通"}])
        df_food = pd.DataFrame([{"timestamp": "t", "menu_category": "和食"}])

        mock_st = _mock_st()
        with _patch_st(mock_st):
            health_tab.render(df_child, df_poop, df_food)

        assert mock_st.dataframe.call_count == 3

    def test_empty_sections_are_skipped_but_headings_remain(self):
        mock_st = _mock_st()
        with _patch_st(mock_st):
            health_tab.render(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

        mock_st.dataframe.assert_not_called()
        headings = [str(c.args[0]) for c in mock_st.markdown.call_args_list if c.args]
        assert any("子供" in h for h in headings)
        assert any("食事" in h for h in headings)

    def test_only_the_expected_columns_are_shown(self):
        df_child = pd.DataFrame([{"timestamp": "t", "child_name": "太郎",
                                  "condition": "元気", "internal_note": "秘密"}])
        mock_st = _mock_st()
        with _patch_st(mock_st):
            health_tab.render(df_child, pd.DataFrame(), pd.DataFrame())

        shown = mock_st.dataframe.call_args.args[0]
        # 列は表示名に置き換わる(スマホでは英語の列名がそのまま幅を食う)。
        # internal_note のような無関係な列が混ざらないことが要点。
        assert list(shown.columns) == ["時刻", "名前", "様子"]
