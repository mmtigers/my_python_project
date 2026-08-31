# MY_HOME_SYSTEM/tests/test_timelapse_generator.py
"""
monitors/timelapse_generator.py の main() のテスト (Issue #240の回帰テスト)。

姉妹システム(smart_timelapse_generator.py/daily_timelapse_job.py)は
timelapse_job_lockで多重実行を排他制御しているが、本ファイルはこれを一切
使用しておらず、二重起動時にcleanup_tmp_video_dir()が一方の処理中クリップを
巻き込んで全消去しうる不具合があった。main()がtimelapse_job_lockを取得し、
取得できなかった場合は処理本体(_main_locked)をスキップすることを検証する。
"""
import contextlib
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import timelapse_generator as tg


@contextlib.contextmanager
def _fake_lock(acquired: bool):
    yield acquired


class TestMainAcquiresTimelapseJobLock:
    def test_locked_runs_main_body(self, monkeypatch):
        monkeypatch.setattr(tg, "timelapse_job_lock", lambda: _fake_lock(True))
        called = []
        monkeypatch.setattr(tg, "_main_locked", lambda: called.append(True))

        tg.main()

        assert called == [True], "ロック取得成功時は処理本体(_main_locked)を実行すべき"

    def test_lock_busy_skips_main_body(self, monkeypatch):
        """別プロセスが既にロックを保持している(二重起動)場合、処理本体を
        スキップし、cleanup_tmp_video_dir()による共有一時ディレクトリの
        全消去が実行されないこと。"""
        monkeypatch.setattr(tg, "timelapse_job_lock", lambda: _fake_lock(False))
        called = []
        monkeypatch.setattr(tg, "_main_locked", lambda: called.append(True))

        tg.main()

        assert called == [], "ロック取得失敗時は処理本体(_main_locked)を実行してはならない"
