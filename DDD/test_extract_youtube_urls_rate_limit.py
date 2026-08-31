# DDD/test_extract_youtube_urls_rate_limit.py
"""
Issue #227の回帰テスト。

extract_youtube_urls.py のレート制限対策は、サブスクリプションのチャンネルURL
「間」にのみジッター待機とサーキットブレーカー(連続失敗閾値)を持っていた。
しかし1チャンネルの処理内部では、YouTubeExtractor.extract_iter が
/videos -> /playlists -> 検出した各プレイリスト、という複数のyt-dlpリクエストを
sleep無しで連続発行しており、レート制限/Bot検知を誘発しやすい構造だった。
さらに、あるチャンネルの一部リクエスト(例: /videos)さえ成功すれば
process_subscriptions側のgot_resultがTrueになり、内部の大量プレイリストが
軒並み失敗してもサーキットブレーカーの連続失敗カウントが常に0にリセットされ、
実質的に機能していなかった。

本テストは、
    1. extract_iter内部のリクエスト間にもジッター待機が挟まること
    2. 内部のプレイリスト取得失敗がlast_extract_internal_failuresとして
       記録されること
    3. process_subscriptionsが、got_result=Trueでも内部失敗があれば
       サーキットブレーカーの連続失敗カウントを増加させること
を検証する。

同じディレクトリの test_extract_youtube_urls_paths.py は importlib.reload() で
extract_youtube_urls モジュールを再ロードするテストを含む。reload()は同一の
モジュール名前空間を書き換えるため、モジュール属性(AppConfig等)を本ファイルの
import時に一度だけローカル変数へエイリアスしてしまうと、他ファイルのreload後に
テストスイート全体を実行した際、そのエイリアスが古いクラスオブジェクトを指したまま
になり、patchが実際にSubscriptionManager等が参照する（reload後の）新しいクラス
オブジェクトに反映されない。そのため本ファイルでは常に `module.AppConfig` 等、
モジュール属性への動的参照でアクセスする。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_extract_youtube_urls_rate_limit.py` のように直接指定して
実行する(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import extract_youtube_urls as module  # noqa: E402

CHANNEL_URL = "https://www.youtube.com/@test_channel"


def _make_result(title="video"):
    return module.ExtractionResult(
        title=title, urls=["https://www.youtube.com/watch?v=1"], source_url=CHANNEL_URL
    )


def _mock_playlists_tab(entries):
    """yt_dlp.YoutubeDL(...).extract_info(f"{base_url}/playlists", ...) の戻り値を模す。"""
    ydl_instance = MagicMock()
    ydl_instance.extract_info.return_value = {"entries": entries}
    ydl_cm = MagicMock()
    ydl_cm.__enter__.return_value = ydl_instance
    ydl_cm.__exit__.return_value = False
    return ydl_cm


class TestExtractIterSleepsBetweenInternalRequests:
    """#227: /videos, /playlists, 各プレイリストという内部の複数リクエスト間にも
    ジッター待機を挟むこと。"""

    def test_sleeps_between_videos_and_playlists_phase(self):
        extractor = module.YouTubeExtractor()
        playlists = [{"url": "https://www.youtube.com/playlist?list=1", "title": "PL1"}]

        with patch.object(extractor, "_extract_single_list", return_value=_make_result()), \
                patch("extract_youtube_urls.yt_dlp.YoutubeDL", return_value=_mock_playlists_tab(playlists)), \
                patch("extract_youtube_urls.time.sleep") as mock_sleep:
            list(extractor.extract_iter(CHANNEL_URL))

        # /videos->/playlists間で1回、プレイリスト1件処理前には(最初の1件なので)
        # スリープは発生しない(初回リクエストの直前にまでは挟まない設計)ため
        # 合計1回のスリープが発生するはず。
        assert mock_sleep.call_count == 1
        slept_seconds = mock_sleep.call_args.args[0]
        assert (
            module.AppConfig.INTRA_CHANNEL_SLEEP_RANGE[0]
            <= slept_seconds
            <= module.AppConfig.INTRA_CHANNEL_SLEEP_RANGE[1]
        )

    def test_sleeps_between_each_playlist_request(self):
        extractor = module.YouTubeExtractor()
        playlists = [
            {"url": "https://www.youtube.com/playlist?list=1", "title": "PL1"},
            {"url": "https://www.youtube.com/playlist?list=2", "title": "PL2"},
            {"url": "https://www.youtube.com/playlist?list=3", "title": "PL3"},
        ]

        with patch.object(extractor, "_extract_single_list", return_value=_make_result()), \
                patch("extract_youtube_urls.yt_dlp.YoutubeDL", return_value=_mock_playlists_tab(playlists)), \
                patch("extract_youtube_urls.time.sleep") as mock_sleep:
            list(extractor.extract_iter(CHANNEL_URL))

        # 1回(/videos -> /playlists) + 2回(3件のプレイリスト間、最初の1件を除く) = 3回
        assert mock_sleep.call_count == 3


class TestExtractIterTracksInternalFailures:
    """#227: extract_iter内部の失敗(個々のプレイリスト取得失敗、プレイリスト一覧
    取得自体の失敗)がlast_extract_internal_failuresに記録されること。"""

    def test_resets_to_zero_at_start_of_each_call(self):
        extractor = module.YouTubeExtractor()
        extractor.last_extract_internal_failures = 5

        with patch.object(extractor, "_extract_single_list", return_value=None), \
                patch("extract_youtube_urls.yt_dlp.YoutubeDL", return_value=_mock_playlists_tab([])), \
                patch("extract_youtube_urls.time.sleep"):
            list(extractor.extract_iter(CHANNEL_URL))

        assert extractor.last_extract_internal_failures == 0

    def test_records_failure_for_each_failed_playlist(self):
        extractor = module.YouTubeExtractor()
        playlists = [
            {"url": "https://www.youtube.com/playlist?list=1", "title": "PL1"},
            {"url": "https://www.youtube.com/playlist?list=2", "title": "PL2"},
            {"url": "https://www.youtube.com/playlist?list=3", "title": "PL3"},
        ]
        # 最初の/videos呼び出しは成功、プレイリストは3件中2件が失敗(None)する想定
        single_list_results = [_make_result("videos"), None, _make_result("pl2"), None]

        with patch.object(extractor, "_extract_single_list", side_effect=single_list_results), \
                patch("extract_youtube_urls.yt_dlp.YoutubeDL", return_value=_mock_playlists_tab(playlists)), \
                patch("extract_youtube_urls.time.sleep"):
            results = list(extractor.extract_iter(CHANNEL_URL))

        # /videos分1件 + 成功したプレイリスト1件 = 2件がyieldされる
        assert len(results) == 2
        # 失敗した2件のプレイリストが記録されている
        assert extractor.last_extract_internal_failures == 2

    def test_records_failure_when_playlists_tab_fetch_raises(self):
        extractor = module.YouTubeExtractor()

        with patch.object(extractor, "_extract_single_list", return_value=_make_result()), \
                patch("extract_youtube_urls.yt_dlp.YoutubeDL", side_effect=RuntimeError("boom")), \
                patch("extract_youtube_urls.time.sleep"):
            list(extractor.extract_iter(CHANNEL_URL))

        assert extractor.last_extract_internal_failures == 1


class TestProcessSubscriptionsCircuitBreakerDetectsInternalFailures:
    """#227: got_result=True(1件でも結果を取得できた)であっても、
    extract_iter内部で失敗が発生していればサーキットブレーカーの連続失敗
    カウントに反映されること。"""

    def _make_db_with_urls(self, tmp_path, urls):
        # process_subscriptions() は db_path = current_base.parent / "home_system.db"
        # としてDBパスを導出するため、get_output_base_dir()が返す値の一段上に置く。
        db_path = tmp_path / "home_system.db"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE youtube_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_url TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            for url in urls:
                conn.execute(
                    "INSERT INTO youtube_subscriptions (channel_url, is_active) VALUES (?, 1)", (url,)
                )
            conn.commit()
        return db_path

    def test_internal_failures_increment_consecutive_failures_despite_got_result(self, tmp_path):
        """3チャンネル全てでgot_result=Trueだが内部失敗が閾値回連続したため、
        以前は決して中断しなかった巡回が、修正後はサーキットブレーカーで中断すること。"""
        urls = [
            "https://www.youtube.com/@ch1",
            "https://www.youtube.com/@ch2",
            "https://www.youtube.com/@ch3",
        ]
        self._make_db_with_urls(tmp_path, urls)

        extractor = MagicMock()
        # 1件は結果をyieldしつつ、常に内部失敗が1件発生している状態を模す
        extractor.extract_iter.return_value = iter([_make_result()])
        extractor.last_extract_internal_failures = 1
        file_manager = MagicMock()

        manager = module.SubscriptionManager(extractor, file_manager)

        original_propagate = module.logger.propagate
        module.logger.propagate = True
        try:
            with patch.object(module.AppConfig, "get_output_base_dir", return_value=tmp_path / "data"), \
                    patch.object(module.AppConfig, "CONSECUTIVE_FAILURE_THRESHOLD", 2), \
                    patch("extract_youtube_urls.time.sleep"):
                manager.process_subscriptions()
        finally:
            module.logger.propagate = original_propagate

        # 3件登録されているが、2件目の処理で連続失敗閾値(2)に達し中断するため、
        # 3件目は処理されない
        assert extractor.extract_iter.call_count == 2

    def test_no_internal_failures_keeps_resetting_consecutive_failures(self, tmp_path):
        """回帰防止: 内部失敗が無い場合は従来通りgot_result=Trueで
        連続失敗カウントがリセットされ、全件処理されること。"""
        urls = [
            "https://www.youtube.com/@ch1",
            "https://www.youtube.com/@ch2",
            "https://www.youtube.com/@ch3",
        ]
        self._make_db_with_urls(tmp_path, urls)

        extractor = MagicMock()
        extractor.extract_iter.return_value = iter([_make_result()])
        extractor.last_extract_internal_failures = 0
        file_manager = MagicMock()

        manager = module.SubscriptionManager(extractor, file_manager)

        with patch.object(module.AppConfig, "get_output_base_dir", return_value=tmp_path / "data"), \
                patch.object(module.AppConfig, "CONSECUTIVE_FAILURE_THRESHOLD", 2), \
                patch("extract_youtube_urls.time.sleep"):
            manager.process_subscriptions()

        assert extractor.extract_iter.call_count == 3


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
