# DDD/test_batch_download_discord_fixes.py
"""
M-7: batch_download_discord.py の回帰テスト。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_batch_download_discord_fixes.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import dataclasses
import logging
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import batch_download_discord as module  # noqa: E402


class TestIsBotDetectionError:
    """M-7-2: "403"/"429"/"503" の部分文字列マッチが動画ID等に誤爆する問題の回帰テスト。"""

    @pytest.mark.parametrize("message", [
        "HTTP Error 403: Forbidden",
        "urllib.error.HTTPError: HTTP Error 429: Too Many Requests",
        "requests.exceptions.RetryError: too many 503 error responses",
        "ERROR: Sign in to confirm you're not a bot",
    ])
    def test_detects_genuine_bot_detection_messages(self, message):
        assert module._is_bot_detection_error(Exception(message)) is True

    @pytest.mark.parametrize("message", [
        "ERROR: [youtube] AbC403XyZ: Video unavailable",
        "ERROR: [youtube] xyz429abc123: This video is private",
        "ERROR: [generic] id_503_video: Unsupported URL",
    ])
    def test_does_not_misfire_on_status_code_substrings_inside_video_ids(self, message):
        """H-7-2回帰防止: 動画IDの中に偶然'403'等の数字列が含まれていても
        誤ってボット検知と判定しないこと。"""
        assert module._is_bot_detection_error(Exception(message)) is False


class TestHistoryManagerLogsFailures:
    """M-7-1: 履歴ファイルI/O失敗が except: pass で握りつぶされ、
    ログにすら残らなかった問題の回帰テスト。
    AppConfigはfrozenなdataclassのため、フィールドの直接書き換えではなく
    dataclasses.replace()で差し替えたインスタンスをmodule.CONFIGごと入れ替える。"""

    def test_load_history_logs_error_on_read_failure(self, tmp_path, monkeypatch, caplog):
        broken_path = tmp_path / "history.txt"
        broken_path.write_text("dummy", encoding="utf-8")
        monkeypatch.setattr(module, "CONFIG", dataclasses.replace(module.CONFIG, HISTORY_FILE_PATH=broken_path))

        def _raise_open(*args, **kwargs):
            raise OSError("simulated read failure")

        monkeypatch.setattr(module, "open", _raise_open, raising=False)

        with caplog.at_level(logging.ERROR, logger=module.logger.name):
            result = module.HistoryManager.load_history()

        assert result == set()
        assert any("読み込みに失敗" in rec.message for rec in caplog.records)

    def test_add_history_logs_error_on_write_failure(self, tmp_path, monkeypatch, caplog):
        unwritable_dir = tmp_path / "no_such_dir" / "history.txt"
        monkeypatch.setattr(module, "CONFIG", dataclasses.replace(module.CONFIG, HISTORY_FILE_PATH=unwritable_dir))

        with caplog.at_level(logging.ERROR, logger=module.logger.name):
            module.HistoryManager.add_history("https://example.com/video")

        assert any("書き込みに失敗" in rec.message for rec in caplog.records)

    def test_add_history_still_writes_successfully_in_the_normal_case(self, tmp_path, monkeypatch):
        history_path = tmp_path / "history.txt"
        monkeypatch.setattr(module, "CONFIG", dataclasses.replace(module.CONFIG, HISTORY_FILE_PATH=history_path))

        module.HistoryManager.add_history("https://example.com/video1")

        assert "https://example.com/video1" in module.HistoryManager.load_history()


class TestUniversalYtDlpStrategyNoPlaylist:
    """M-7-3: リストの1行がプレイリスト/チャンネルURLだった場合に無制限DLされる
    問題の回帰テスト。noplaylistオプションが設定されていることを確認する。"""

    def test_ydl_opts_includes_noplaylist(self, tmp_path, monkeypatch):
        strategy = module.UniversalYtDlpStrategy.__new__(module.UniversalYtDlpStrategy)
        monkeypatch.setattr(strategy, "_determine_save_dir", lambda *a, **k: tmp_path)

        captured_opts = {}

        class _FakeYoutubeDL:
            def __init__(self, opts):
                captured_opts.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=False):
                raise RuntimeError("stop before actual network access")

            def prepare_filename(self, info):
                return str(tmp_path / "dummy.mp4")

        monkeypatch.setattr(module.yt_dlp, "YoutubeDL", _FakeYoutubeDL)

        task = module.DownloadTask(url="https://www.youtube.com/watch?v=dummy", source_name="test_list")
        strategy.download(task)

        assert captured_opts.get("noplaylist") is True


class TestNormalizeUrl:
    """MissAVの検索結果画面でコピーしたURL(#...検索セッションのハッシュ付き)が
    実際の動画URLと別物として扱われてしまう問題の回帰テスト。"""

    def test_strips_missav_search_fragment(self):
        search_url = "https://missav.live/dm18/ja/dvdms-079#fa517d7cc2ba4000f26c00d7ac352d33_search"
        assert module._normalize_url(search_url) == "https://missav.live/dm18/ja/dvdms-079"

    def test_leaves_fragment_less_url_unchanged(self):
        url = "https://tktube.com/ja/videos/336349/dvmm-259-sex-vol-03/"
        assert module._normalize_url(url) == url

    def test_keeps_query_string_but_drops_fragment(self):
        url = "https://example.com/watch?v=abc#frag"
        assert module._normalize_url(url) == "https://example.com/watch?v=abc"


class TestCollectTasksNormalizesMissavSearchUrls:
    """検索画面からコピーしたフラグメント付きURLをlist.txtに貼った場合でも、
    実際の動画URLと同一のタスクとして読み込まれることを確認する回帰テスト。"""

    def test_fragment_is_stripped_when_loading_list_file(self, tmp_path, monkeypatch):
        list_file = tmp_path / "list.txt"
        list_file.write_text(
            "https://missav.live/dm18/ja/dvdms-079#fa517d7cc2ba4000f26c00d7ac352d33_search\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            module,
            "CONFIG",
            dataclasses.replace(
                module.CONFIG,
                LIST_FILE_PATH=list_file,
                LIST_DIR_PATH=tmp_path / "list",
                HISTORY_FILE_PATH=tmp_path / "history.txt",
            ),
        )

        downloader = module.BatchDownloader.__new__(module.BatchDownloader)
        downloader.history = set()

        tasks = downloader._collect_tasks()

        assert len(tasks) == 1
        assert tasks[0].url == "https://missav.live/dm18/ja/dvdms-079"


class TestScrapingStrategyFragmentStaging:
    """missavのHLSフラグメント(数千個の小ファイル)を、NASの保存先ディレクトリ
    ではなくローカルディスク(CONFIG.LOCAL_TMP_DIR)へ一時保存することを確認する
    回帰テスト。NAS上に大量の小ファイルを書き込むと、autofsの再マウント遅延等
    により一部フラグメントがyt-dlpから"fragment not found"として欠落する問題が
    実機で発生したための対応。"""

    def test_fragments_are_staged_under_local_tmp_dir_not_save_dir(self, tmp_path, monkeypatch):
        nas_save_dir = tmp_path / "nas_save"
        nas_save_dir.mkdir()
        local_tmp_dir = tmp_path / "local_tmp"

        monkeypatch.setattr(module, "CONFIG", dataclasses.replace(module.CONFIG, LOCAL_TMP_DIR=local_tmp_dir))
        monkeypatch.setattr(module.DiscordNotifier, "send", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(module.FileSystemManager, "check_disk_space", staticmethod(lambda *a, **k: True))

        strategy = module.ScrapingStrategy.__new__(module.ScrapingStrategy)

        manifest = (
            "#EXTM3U\n"
            "#EXT-X-TARGETDURATION:10\n"
            "https://cdn.example.com/seg0.ts\n"
            "https://cdn.example.com/seg1.ts\n"
            "#EXT-X-ENDLIST\n"
        )
        monkeypatch.setattr(strategy, "_fetch_m3u8_manifest", lambda m3u8_url, page_url: manifest)
        monkeypatch.setattr(strategy, "_download_segment", lambda url, page_url: b"dummy-bytes")

        captured = {}

        class _FakeYoutubeDL:
            def __init__(self, opts):
                self._opts = opts
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def download(self, urls):
                captured["download_url"] = urls[0]
                # 実際のyt-dlpはこの時点でouttmplの位置にファイルを書き出す。
                Path(self._opts["outtmpl"]).write_bytes(b"dummy-merged-video")

        monkeypatch.setattr(module.yt_dlp, "YoutubeDL", _FakeYoutubeDL)

        final_path = nas_save_dir / "dvdms-079.mp4"
        result = strategy._download_with_ytdlp(
            "https://cdn.example.com/playlist.m3u8",
            final_path,
            "https://missav.live/dm18/ja/dvdms-079",
            nas_save_dir,
        )

        assert result is True
        download_url = captured["download_url"]
        local_manifest_path = str(Path(unquote(urlsplit(download_url).path)))
        assert str(local_tmp_dir) in local_manifest_path
        assert str(nas_save_dir) not in local_manifest_path

        # 結合(merge)先もNASではなくローカルディスクだったこと、かつ完成した
        # ファイルは最終的にNAS上のfinal_pathへ届いていることを確認する。
        assert str(local_tmp_dir) in captured["opts"]["outtmpl"]
        assert str(nas_save_dir) not in captured["opts"]["outtmpl"]
        assert final_path.exists()
        assert final_path.read_bytes() == b"dummy-merged-video"
        # NAS側の一時ファイル(.nastmp)やローカルの一時ディレクトリは残らないこと。
        assert not final_path.with_name(final_path.name + ".nastmp").exists()
        assert not (local_tmp_dir / (final_path.name + ".fragments.tmp")).exists()


class TestScrapingStrategyMergeStaysOffNasOnFailure:
    """結合(yt-dlp)またはNASへの転送が失敗した場合に、NAS上に中途半端な
    final_pathや`.nastmp`一時ファイルが残らないことを確認する回帰テスト。"""

    def test_final_path_not_left_behind_when_merge_raises(self, tmp_path, monkeypatch):
        nas_save_dir = tmp_path / "nas_save"
        nas_save_dir.mkdir()
        local_tmp_dir = tmp_path / "local_tmp"

        monkeypatch.setattr(module, "CONFIG", dataclasses.replace(module.CONFIG, LOCAL_TMP_DIR=local_tmp_dir))
        monkeypatch.setattr(module.DiscordNotifier, "send", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(module.FileSystemManager, "check_disk_space", staticmethod(lambda *a, **k: True))

        strategy = module.ScrapingStrategy.__new__(module.ScrapingStrategy)
        manifest = "#EXTM3U\nhttps://cdn.example.com/seg0.ts\n#EXT-X-ENDLIST\n"
        monkeypatch.setattr(strategy, "_fetch_m3u8_manifest", lambda m3u8_url, page_url: manifest)
        monkeypatch.setattr(strategy, "_download_segment", lambda url, page_url: b"dummy-bytes")

        class _FailingYoutubeDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def download(self, urls):
                raise RuntimeError("simulated merge failure")

        monkeypatch.setattr(module.yt_dlp, "YoutubeDL", _FailingYoutubeDL)

        final_path = nas_save_dir / "dvdms-079.mp4"
        result = strategy._download_with_ytdlp(
            "https://cdn.example.com/playlist.m3u8",
            final_path,
            "https://missav.live/dm18/ja/dvdms-079",
            nas_save_dir,
        )

        assert result is False
        assert not final_path.exists()
        assert not final_path.with_name(final_path.name + ".nastmp").exists()
        assert list(nas_save_dir.iterdir()) == []
        assert not local_tmp_dir.exists() or list(local_tmp_dir.iterdir()) == []


class TestScrapingStrategyStaleArtifactCleanup:
    """以前の実装がNAS上に残した`.part`/`.part-FragN.part`/`.ytdl`/旧版の
    `.fragments.tmp`ディレクトリ等の残骸を、次回試行前に一掃することを
    確認する回帰テスト。"""

    def test_removes_stale_siblings_but_keeps_final_path(self, tmp_path):
        save_dir = tmp_path / "nas_save"
        save_dir.mkdir()
        final_path = save_dir / "dvdms-079.mp4"
        final_path.write_bytes(b"already-downloaded")

        stale_part = save_dir / "dvdms-079.mp4.part-Frag30.part"
        stale_part.write_bytes(b"stale")
        stale_ytdl = save_dir / "dvdms-079.mp4.ytdl"
        stale_ytdl.write_text("stale")
        stale_dir = save_dir / "dvdms-079.mp4.fragments.tmp"
        stale_dir.mkdir()
        (stale_dir / "seg_000000.ts").write_bytes(b"stale")
        unrelated = save_dir / "other-video.mp4"
        unrelated.write_bytes(b"keep-me")

        module.ScrapingStrategy._cleanup_stale_ytdlp_artifacts(final_path)

        assert final_path.exists()
        assert final_path.read_bytes() == b"already-downloaded"
        assert not stale_part.exists()
        assert not stale_ytdl.exists()
        assert not stale_dir.exists()
        assert unrelated.exists()


class TestScrapingStrategyLocalTmpDiskSpaceGuard:
    """LOCAL_TMP_DIRの空き容量が不足している場合、フラグメント書き込みで
    ローカルディスクを圧迫する前にダウンロードを中断することを確認する回帰
    テスト。ローカルディスクを使い切ると、システム全体（他プロセスやSSH
    セッション等）に影響しかねないため。"""

    def test_aborts_without_downloading_when_local_disk_is_low(self, tmp_path, monkeypatch):
        nas_save_dir = tmp_path / "nas_save"
        nas_save_dir.mkdir()
        local_tmp_dir = tmp_path / "local_tmp"

        monkeypatch.setattr(module, "CONFIG", dataclasses.replace(module.CONFIG, LOCAL_TMP_DIR=local_tmp_dir))
        monkeypatch.setattr(module.DiscordNotifier, "send", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(module.FileSystemManager, "check_disk_space", staticmethod(lambda *a, **k: False))

        strategy = module.ScrapingStrategy.__new__(module.ScrapingStrategy)
        fetch_called = []
        monkeypatch.setattr(strategy, "_fetch_m3u8_manifest", lambda m3u8_url, page_url: "#EXTM3U\n")

        def _fail_if_called(*a, **k):
            fetch_called.append(True)
            raise AssertionError("空き容量不足時にセグメント取得が呼ばれてはならない")

        monkeypatch.setattr(strategy, "_download_segment", _fail_if_called)

        final_path = nas_save_dir / "dvdms-079.mp4"
        result = strategy._download_with_ytdlp(
            "https://cdn.example.com/playlist.m3u8",
            final_path,
            "https://missav.live/dm18/ja/dvdms-079",
            nas_save_dir,
        )

        assert result is False
        assert fetch_called == []
        assert not local_tmp_dir.exists() or list(local_tmp_dir.iterdir()) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
