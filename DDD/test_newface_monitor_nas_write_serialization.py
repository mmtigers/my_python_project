# DDD/test_newface_monitor_nas_write_serialization.py
"""
2026-09-19 運用障害(known_casts_*.jsonの書き込み検証失敗・別サイトのキャスト混入)の
回帰テスト。

実機NAS(CIFS、serverinoマウント)は同時に作成されたファイルへ同じinode番号を
払い出すことがあり、Linuxのcifsクライアントがそれらを1つのinode(ページキャッシュ)
として扱うため、一時ファイルの読み戻しが「空」や「別サイトのファイルの中身」を
返していた。#458のスレッド並列化で、別サイトの保存処理が同時にNAS上へファイルを
作成するようになったのが引き金。本テストは次を検証する。

    1. NAS上へのファイル作成を伴う区間(一時ファイルの書き込み+検証、.bak更新+
       置き換え)が、別サイト・別ファイルであってもスレッド間で直列化されること
    2. リトライ待機(sleep)はロック外で行い、他スレッドを巻き込まないこと
    3. 読み戻しが「別ファイルの正しいJSON」だった場合も検証失敗として検知し、
       本番ファイルへ置き換えずにリトライすること(以前のJSONパース検証は
       これをすり抜けていた)
"""
import io
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module

SiteConfig = module.SiteConfig
CastMember = module.CastMember
DataManager = module.DataManager


def _make_site(site_id: str) -> "SiteConfig":
    return SiteConfig(
        site_id=site_id,
        name=f"Test Site {site_id}",
        target_url="https://example.test/therapist.html",
        selector_container="div",
        selector_name="li",
        selector_link="a",
        selector_image="img",
    )


def _casts(prefix: str, n: int = 3) -> set:
    return {
        CastMember(id=f"{prefix}{i}", name=f"{prefix}-{i}", detail_url=f"u{i}", image_url=f"i{i}", age="20")
        for i in range(n)
    }


class _OverlapTracker:
    """クリティカル区間に同時に入っているスレッド数の最大値を記録する。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def wrap(self, func):
        def _wrapped(*args, **kwargs):
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                # 並列に走っていれば確実に重なるよう、区間内で少し待つ
                time.sleep(0.005)
                return func(*args, **kwargs)
            finally:
                with self._lock:
                    self.active -= 1

        return _wrapped


class TestNasFileCreationIsSerialized:
    def test_concurrent_saves_of_different_sites_never_create_files_at_the_same_time(
        self, tmp_path, monkeypatch
    ):
        """別サイトのsave_known_casts/record_daily_new_casts/record_site_failureが
        並列に呼ばれても、NAS上へファイルを作成する区間は同時に1スレッドだけであること。"""
        dm = DataManager(tmp_path)
        tracker = _OverlapTracker()
        monkeypatch.setattr(
            module.DataManager,
            "_write_bytes_and_compare",
            staticmethod(tracker.wrap(DataManager._write_bytes_and_compare)),
        )
        # .bak.tmpの作成とreplaceも同じ区間として数える
        original_replace = Path.replace
        monkeypatch.setattr(module.Path, "replace", tracker.wrap(original_replace))

        sites = [_make_site(f"site_{i}") for i in range(8)]

        def _worker(site):
            # 2回保存して、.bak更新(既存ファイルありの経路)も通す
            dm.save_known_casts(site, _casts(site.site_id))
            dm.save_known_casts(site, _casts(site.site_id, 4))
            # 全サイト共通ファイルは実運用と同じくrecord_*経由で更新する
            dm.record_daily_new_casts(site.site_id, 1)
            dm.record_site_failure(site.site_id)

        with ThreadPoolExecutor(max_workers=8) as executor:
            for f in [executor.submit(_worker, s) for s in sites]:
                f.result()

        assert tracker.max_active == 1
        # 各サイトのファイルには、そのサイト自身のキャストだけが保存されていること
        for site in sites:
            assert dm.load_known_casts(site) == _casts(site.site_id, 4)
            backup = tmp_path / (site.get_data_filename() + ".bak")
            assert DataManager._read_casts_file(backup) == _casts(site.site_id)

    def test_retry_sleep_is_outside_the_lock(self, tmp_path, monkeypatch):
        """検証失敗時のリトライ待機中は_nas_file_create_lockを保持していないこと
        (保持したまま待つと、他サイトの保存が最大4秒巻き添えで止まる)。"""
        dm = DataManager(tmp_path)
        site = _make_site("retry_site")

        original = DataManager._write_bytes_and_compare
        calls = {"n": 0}

        def _flaky(path, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("simulated read-back mismatch")
            return original(path, payload)

        monkeypatch.setattr(module.DataManager, "_write_bytes_and_compare", staticmethod(_flaky))
        lock_held_during_sleep = []
        monkeypatch.setattr(
            module.time,
            "sleep",
            lambda _s: lock_held_during_sleep.append(DataManager._nas_file_create_lock.locked()),
        )

        dm.save_known_casts(site, _casts("r"))

        assert lock_held_during_sleep == [False]
        assert dm.load_known_casts(site) == _casts("r")
        assert not DataManager._nas_file_create_lock.locked()


class TestReadBackOfAnotherFileIsDetected:
    def test_valid_json_of_another_site_is_rejected_and_retried(self, tmp_path, monkeypatch):
        """実機で観測された「一時ファイルの読み戻しが別サイトのファイルの中身を返す」
        事象を再現する。別サイトの内容は正しいJSONなので、以前のJSONパースだけの
        検証はすり抜け、その内容のまま次回以降の既知キャストになっていた。"""
        dm = DataManager(tmp_path)
        site = _make_site("victim")
        foreign = json.dumps(
            [c.to_dict() for c in _casts("other_site")], ensure_ascii=False, indent=2
        ).encode("utf-8")

        real_open = open
        state = {"aliased_reads": 0}

        def _aliasing_open(file, mode="r", *args, **kwargs):
            # 最初の1回だけ、一時ファイルのバイナリ読み戻しで別ファイルの中身を返す
            if (
                mode == "rb"
                and str(file).endswith(".json.tmp")
                and state["aliased_reads"] == 0
            ):
                state["aliased_reads"] += 1
                return io.BytesIO(foreign)
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(module, "open", _aliasing_open, raising=False)
        sleep_calls = []
        monkeypatch.setattr(module.time, "sleep", lambda s: sleep_calls.append(s))

        dm.save_known_casts(site, _casts("victim"))

        assert state["aliased_reads"] == 1
        # 検知してリトライし、2回目で自サイトの内容が保存されている
        assert sleep_calls == [DataManager._SAVE_VERIFY_RETRY_DELAY_SECONDS]
        assert dm.load_known_casts(site) == _casts("victim")

    def test_persistent_mismatch_never_replaces_existing_file(self, tmp_path, monkeypatch):
        """読み戻しが毎回食い違う場合は、既存の本番ファイルを置き換えないこと。"""
        dm = DataManager(tmp_path)
        site = _make_site("keep")
        dm.save_known_casts(site, _casts("old"))

        real_open = open

        def _always_empty_readback(file, mode="r", *args, **kwargs):
            if mode == "rb" and str(file).endswith(".json.tmp"):
                return io.BytesIO(b"")
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(module, "open", _always_empty_readback, raising=False)
        monkeypatch.setattr(module.time, "sleep", lambda _s: None)

        dm.save_known_casts(site, _casts("new"))

        assert dm.load_known_casts(site) == _casts("old")
        data_file = tmp_path / site.get_data_filename()
        assert not data_file.with_suffix(data_file.suffix + ".tmp").exists()
