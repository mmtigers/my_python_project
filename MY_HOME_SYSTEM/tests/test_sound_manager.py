# MY_HOME_SYSTEM/tests/test_sound_manager.py
"""core/sound_manager.play の子プロセス回収(Issue #654)の回帰テスト。

呼び出し元(unified_server)は長期稼働で SIGCHLD を処理しないため、Popen したまま wait() しないと
再生プロセスが <defunct> として蓄積していた。再生をブロックせずに終了を回収することを固定する。
"""
import os
import threading
from unittest.mock import MagicMock

import config
from core import sound_manager


def _setup_playable_sound(tmp_path, monkeypatch):
    sound_dir = tmp_path / "sounds"
    sound_dir.mkdir()
    (sound_dir / "ok.mp3").write_bytes(b"\x00")
    monkeypatch.setattr(config, "SOUND_DIR", str(sound_dir))
    monkeypatch.setattr(config, "SOUND_MAP", {"ok": "ok.mp3"})
    monkeypatch.setattr(config, "SOUND_PLAYER_CMD", "true")  # /usr/bin/true は常に存在する
    monkeypatch.setattr(config, "SOUND_PLAYER_ARGS", [], raising=False)


def test_play_reaps_child_process_in_background_thread(tmp_path, monkeypatch):
    _setup_playable_sound(tmp_path, monkeypatch)
    fake_proc = MagicMock()
    fake_proc.pid = 4242
    waited = threading.Event()
    fake_proc.wait.side_effect = lambda *a, **k: waited.set()
    monkeypatch.setattr(sound_manager.subprocess, "Popen", lambda *a, **k: fake_proc)

    sound_manager.play("ok")

    # 再生自体は呼び出しをブロックせず、別スレッドで wait() が呼ばれる
    assert waited.wait(timeout=2.0), "子プロセスの wait() が呼ばれていない(ゾンビ化する)"


def test_play_with_real_short_lived_command_leaves_no_zombie(tmp_path, monkeypatch):
    _setup_playable_sound(tmp_path, monkeypatch)
    spawned = []
    real_popen = sound_manager.subprocess.Popen

    def spy_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(sound_manager.subprocess, "Popen", spy_popen)

    sound_manager.play("ok")

    assert len(spawned) == 1
    proc = spawned[0]
    # 回収スレッドが wait() を終えると returncode が確定する(ゾンビが残らない)
    for _ in range(50):
        if proc.returncode is not None:
            break
        threading.Event().wait(0.05)
    assert proc.returncode == 0
    # プロセステーブル上も defunct ではない(既に回収済みなら /proc に無い)
    assert not os.path.exists(f"/proc/{proc.pid}") or "Z" not in open(f"/proc/{proc.pid}/stat").read().split(")")[-1].split()[0]
