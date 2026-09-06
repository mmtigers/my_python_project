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
を検証する。

（#413 D-L11で削除）以前ここには、削除された SubscriptionManager
(process_subscriptions)が got_result=True でも内部失敗があればサーキット
ブレーカーの連続失敗カウントを増加させることを検証する3点目の項目があったが、
SubscriptionManager自体の削除に伴い該当テストクラスも削除した。

同じディレクトリの test_extract_youtube_urls_paths.py は importlib.reload() で
extract_youtube_urls モジュールを再ロードするテストを含む。reload()は同一の
モジュール名前空間を書き換えるため、モジュール属性(AppConfig等)を本ファイルの
import時に一度だけローカル変数へエイリアスしてしまうと、他ファイルのreload後に
テストスイート全体を実行した際、そのエイリアスが古いクラスオブジェクトを指したまま
になる。そのため本ファイルでは常に `module.AppConfig` 等、モジュール属性への
動的参照でアクセスする。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_extract_youtube_urls_rate_limit.py` のように直接指定して
実行する(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sys
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


# #413 (D-L11): 以前ここにあった TestProcessSubscriptionsCircuitBreakerDetectsInternalFailures
# は、削除された SubscriptionManager(定期巡回/サブスクリプション機能。事実上の
# デッド機能だったためオーナー判断で撤去)専用のテストだったため削除した。
# extract_iter自体のジッター待機・内部失敗カウントのテスト(上記2クラス)は
# SubscriptionManagerに依存しないため引き続き対象。


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
