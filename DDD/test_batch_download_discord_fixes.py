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


class TestCollectTasksListFileReadFailureIsProtected:
    """Issue #184の回帰テスト: list/*.txt側の読み込みはtry/exceptで保護され
    エラーログを出したうえで処理を継続するが、list.txt側にはこの保護が無かった。
    list.txtの読み込みで例外が発生すると_collect_tasks全体が未処理例外で中断し、
    後続で処理されるはずのlist/*.txtのタスクまで巻き添えで処理されなくなっていた。"""

    def test_list_txt_read_failure_does_not_abort_list_dir_processing(
        self, tmp_path, monkeypatch, caplog
    ):
        list_dir = tmp_path / "list"
        list_dir.mkdir()
        (list_dir / "other_source.txt").write_text(
            "https://example.com/other-video\n", encoding="utf-8"
        )

        list_file = tmp_path / "list.txt"
        list_file.write_text("dummy", encoding="utf-8")

        monkeypatch.setattr(
            module,
            "CONFIG",
            dataclasses.replace(
                module.CONFIG,
                LIST_FILE_PATH=list_file,
                LIST_DIR_PATH=list_dir,
                HISTORY_FILE_PATH=tmp_path / "history.txt",
            ),
        )

        real_open = open

        def _open_with_simulated_failure(path, *args, **kwargs):
            if Path(path) == list_file:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "simulated decode failure")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(module, "open", _open_with_simulated_failure, raising=False)

        downloader = module.BatchDownloader.__new__(module.BatchDownloader)
        downloader.history = set()

        with caplog.at_level(logging.ERROR, logger="Downloader"):
            # 例外を送出せずに完走すること自体が回帰確認の対象
            tasks = downloader._collect_tasks()

        # list.txtは読めなかったが、list/*.txt側のタスクは巻き添えにならず処理される
        assert len(tasks) == 1
        assert tasks[0].url == "https://example.com/other-video"
        assert any("リスト読み込みエラー" in r.message for r in caplog.records)


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


class TestScrapingStrategyNasCopyIntegrityCheck:
    """NAS(CIFS)への転送が静かに欠損した場合に、成功扱いにせず失敗として
    扱うことを確認する回帰テスト。実機のdmesgで観測されたNAS接続不安定
    ("stuck for 15 seconds"、"No writable handle in writepages")により、
    shutil.copy2自体は例外を出さないまま末尾が欠損した"moov atom not found"
    (=再生不能)のmp4がNAS上に生成される実害が発生したための対応。"""

    def test_size_mismatch_after_copy_is_treated_as_failure(self, tmp_path, monkeypatch):
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

        class _FakeYoutubeDL:
            def __init__(self, opts):
                self._opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def download(self, urls):
                Path(self._opts["outtmpl"]).write_bytes(b"complete-merged-video-bytes")

        monkeypatch.setattr(module.yt_dlp, "YoutubeDL", _FakeYoutubeDL)

        # NASへのコピーが末尾で欠損した状況を再現する: 実際より短いバイト列
        # だけを書き込むが、shutil.copy2としては例外を出さず正常終了する。
        def _truncated_copy2(src, dst):
            with open(src, "rb") as f:
                data = f.read()
            with open(dst, "wb") as f:
                f.write(data[: len(data) // 2])

        monkeypatch.setattr(module.shutil, "copy2", _truncated_copy2)

        final_path = nas_save_dir / "sdmm-018.mp4"
        result = strategy._download_with_ytdlp(
            "https://cdn.example.com/playlist.m3u8",
            final_path,
            "https://missav.live/dm31/ja/sdmm-018",
            nas_save_dir,
        )

        assert result is False
        assert not final_path.exists()
        assert not final_path.with_name(final_path.name + ".nastmp").exists()
        assert list(nas_save_dir.iterdir()) == []


class TestScrapingStrategyPreMergeDiskSpaceGuard:
    """セグメント取得完了後、重い結合(yt-dlp)/後処理(FixupM3u8)を始める前に
    もう一度ローカルディスクの空き容量を確認し、不足していれば無駄な処理を
    せず早期に中断することを確認する回帰テスト。実機では、この確認が無いと
    数十分かけてセグメントを取得した後、結合〜後処理の終盤でディスクフルに
    より要領を得ない"Conversion failed!"エラーで失敗し、それまでの時間と
    帯域が丸ごと無駄になる事象が発生した。"""

    def test_aborts_before_merge_when_local_disk_headroom_is_insufficient(self, tmp_path, monkeypatch):
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

        # 実際の空き容量に依存せず判定を再現できるよう、disk_usage()の戻り値を
        # 「空きほぼ0」に固定する。
        monkeypatch.setattr(module.shutil, "disk_usage", lambda path: (10**12, 10**12, 0))

        merge_invoked = []

        class _ShouldNotBeCalledYoutubeDL:
            def __init__(self, opts):
                merge_invoked.append(True)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def download(self, urls):
                raise AssertionError("空き容量不足時に結合(yt-dlp)が呼ばれてはならない")

        monkeypatch.setattr(module.yt_dlp, "YoutubeDL", _ShouldNotBeCalledYoutubeDL)

        final_path = nas_save_dir / "dvdms-079.mp4"
        result = strategy._download_with_ytdlp(
            "https://cdn.example.com/playlist.m3u8",
            final_path,
            "https://missav.live/dm18/ja/dvdms-079",
            nas_save_dir,
        )

        assert result is False
        assert merge_invoked == []
        assert not final_path.exists()
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


class TestVerifyNasMount:
    """NASを持たない単独環境(外付けHDD等への直接保存)向けのNASマウント確認バイパス。"""

    def test_skips_check_when_nas_not_required(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            module,
            "CONFIG",
            dataclasses.replace(
                module.CONFIG,
                REQUIRE_NAS_MOUNT=False,
                NAS_MOUNT_POINT=tmp_path / "does-not-exist",
            ),
        )
        assert module.SystemHealthChecker.verify_nas_mount() is True

    def test_still_enforced_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            module,
            "CONFIG",
            dataclasses.replace(
                module.CONFIG,
                REQUIRE_NAS_MOUNT=True,
                NAS_MOUNT_POINT=tmp_path / "does-not-exist",
            ),
        )
        monkeypatch.setattr(module.DiscordNotifier, "send", staticmethod(lambda *a, **k: None))
        assert module.SystemHealthChecker.verify_nas_mount() is False


class TestStandaloneDiscordWebhookFallback:
    """MY_HOME_SYSTEM(LINE Bot SDK/config.py/DB)を持たない単独環境向けの
    簡易Discord通知フォールバック(_standalone_send_discord_webhook)の回帰テスト。"""

    def test_returns_false_without_error_when_no_webhook_configured(self, monkeypatch):
        for name in ("DISCORD_WEBHOOK_ERROR", "DISCORD_WEBHOOK_NOTIFY", "DISCORD_WEBHOOK_URL"):
            monkeypatch.delenv(name, raising=False)
        assert module._standalone_send_discord_webhook([{"type": "text", "text": "hi"}]) is False

    def test_posts_to_notify_webhook_for_default_channel(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_NOTIFY", "https://discord.example.com/notify")
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        calls = []

        class _FakeResponse:
            def raise_for_status(self):
                pass

        def _fake_post(url, json, timeout):
            calls.append((url, json))
            return _FakeResponse()

        monkeypatch.setattr(module.requests, "post", _fake_post)
        result = module._standalone_send_discord_webhook([{"type": "text", "text": "hello"}], channel="notify")

        assert result is True
        assert calls == [("https://discord.example.com/notify", {"content": "hello"})]

    def test_posts_to_error_webhook_for_error_channel(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_ERROR", "https://discord.example.com/error")
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        calls = []

        class _FakeResponse:
            def raise_for_status(self):
                pass

        def _fake_post(url, json, timeout):
            calls.append(url)
            return _FakeResponse()

        monkeypatch.setattr(module.requests, "post", _fake_post)
        result = module._standalone_send_discord_webhook([{"type": "text", "text": "boom"}], channel="error")

        assert result is True
        assert calls == ["https://discord.example.com/error"]


class TestConfigurableRequestTimeout:
    """単身赴任先PC等、自宅回線より低速な環境向けにREQUEST_TIMEOUTを
    DDD_REQUEST_TIMEOUT環境変数で調整可能にする変更の回帰テスト。"""

    def _request_timeout_with_env(self, monkeypatch, value):
        # importlib.reload()はmoduleオブジェクトをin-placeで書き換えるため、
        # reload直後に必要な値だけをコピーして取り出す(moduleオブジェクト自体を
        # 返すと、後始末の再reloadで値が上書きされてテストが壊れるため)。
        if value is None:
            monkeypatch.delenv("DDD_REQUEST_TIMEOUT", raising=False)
        else:
            monkeypatch.setenv("DDD_REQUEST_TIMEOUT", value)
        import importlib

        try:
            importlib.reload(module)
            return module.CONFIG.REQUEST_TIMEOUT
        finally:
            # 他のテストに影響しないよう、環境変数を戻した上で再度reloadしておく。
            monkeypatch.undo()
            importlib.reload(module)

    def test_defaults_to_20_when_unset(self, monkeypatch):
        assert self._request_timeout_with_env(monkeypatch, None) == 20

    def test_uses_env_override_when_set(self, monkeypatch):
        assert self._request_timeout_with_env(monkeypatch, "90") == 90


class TestFileSystemManagerEnsureDirCatchesGenericOSError:
    """Issue #236の回帰テスト: ensure_dirがPermissionError以外のOSError
    (読み取り専用マウントのErrno 30、NAS切断時のErrno 5、ディスクフル時の
    Errno 28等)を捕捉せず、専用のDiscord通知を経由しないまま呼び出し元へ
    伝播していた不具合。extract_youtube_urls.pyのprocess_subscriptions(#185)と
    同様にOSError全般を捕捉するよう修正した。"""

    def test_permission_error_still_sends_dedicated_notification_and_returns_false(
        self, monkeypatch, tmp_path
    ):
        calls = []
        monkeypatch.setattr(module.DiscordNotifier, "send", staticmethod(lambda text, is_error=False: calls.append((text, is_error))))

        def _raise_permission_error(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(module.Path, "mkdir", _raise_permission_error)

        result = module.FileSystemManager.ensure_dir(tmp_path / "sub")

        assert result is False
        assert len(calls) == 1
        assert "権限エラー" in calls[0][0]
        assert calls[0][1] is True

    def test_read_only_filesystem_oserror_sends_notification_and_returns_false(
        self, monkeypatch, tmp_path
    ):
        """読み取り専用マウント(Errno 30)のような、PermissionError以外のOSErrorの回帰テスト。"""
        calls = []
        monkeypatch.setattr(module.DiscordNotifier, "send", staticmethod(lambda text, is_error=False: calls.append((text, is_error))))

        def _raise_read_only_error(*args, **kwargs):
            raise OSError(30, "Read-only file system")

        monkeypatch.setattr(module.Path, "mkdir", _raise_read_only_error)

        result = module.FileSystemManager.ensure_dir(tmp_path / "sub")

        assert result is False, "PermissionError以外のOSErrorも呼び出し元へ伝播させず捕捉すべき"
        assert len(calls) == 1, "OSError発生時も専用のDiscord通知を送るべき"
        assert calls[0][1] is True

    def test_disk_full_oserror_sends_notification_and_returns_false(self, monkeypatch, tmp_path):
        """ディスクフル(Errno 28)のような、PermissionError以外のOSErrorの回帰テスト。"""
        calls = []
        monkeypatch.setattr(module.DiscordNotifier, "send", staticmethod(lambda text, is_error=False: calls.append((text, is_error))))

        def _raise_disk_full_error(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(module.Path, "mkdir", _raise_disk_full_error)

        result = module.FileSystemManager.ensure_dir(tmp_path / "sub")

        assert result is False
        assert len(calls) == 1
        assert calls[0][1] is True

    def test_success_returns_true_and_sends_no_notification(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(module.DiscordNotifier, "send", staticmethod(lambda text, is_error=False: calls.append((text, is_error))))

        result = module.FileSystemManager.ensure_dir(tmp_path / "new_sub_dir")

        assert result is True
        assert calls == []
        assert (tmp_path / "new_sub_dir").is_dir()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
