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


# --- play() のガード節(音が鳴らない条件)と Fail-Soft ---------------------------------
#
# play() は「鳴らないこと」自体は障害ではないため、どの失敗も例外にせずログだけ残す。
# その代わり、条件を取り違えて subprocess を起動してしまうと、存在しないファイルや
# コマンドを毎回叩き続けることになる。呼ばないことを固定する。


def _spy_popen(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sound_manager.subprocess, "Popen", lambda *a, **k: calls.append(a) or MagicMock(pid=1)
    )
    return calls


def test_play_ignores_unknown_event_key(tmp_path, monkeypatch):
    _setup_playable_sound(tmp_path, monkeypatch)
    calls = _spy_popen(monkeypatch)
    sound_manager.play("存在しないキー")
    assert calls == []


def test_play_skips_when_sound_file_is_missing(tmp_path, monkeypatch):
    _setup_playable_sound(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SOUND_MAP", {"ok": "not_there.mp3"})
    calls = _spy_popen(monkeypatch)
    sound_manager.play("ok")
    assert calls == []


def test_play_skips_when_player_command_is_not_installed(tmp_path, monkeypatch):
    _setup_playable_sound(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SOUND_PLAYER_CMD", "no_such_player_command")
    calls = _spy_popen(monkeypatch)
    sound_manager.play("ok")
    assert calls == []


def test_play_passes_configured_player_args(tmp_path, monkeypatch):
    _setup_playable_sound(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SOUND_PLAYER_ARGS", ["-q", "--no-show-progress"], raising=False)
    captured = {}
    monkeypatch.setattr(
        sound_manager.subprocess,
        "Popen",
        lambda cmd, **k: captured.setdefault("cmd", cmd) or MagicMock(pid=1),
    )
    sound_manager.play("ok")
    assert captured["cmd"][:3] == ["true", "-q", "--no-show-progress"]
    assert captured["cmd"][-1].endswith("ok.mp3")


def test_play_does_not_raise_when_popen_fails(tmp_path, monkeypatch):
    """Fail-Soft: 再生に失敗してもクエスト完了などの呼び出し元を巻き込まない。"""
    _setup_playable_sound(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise OSError("Permission denied")

    monkeypatch.setattr(sound_manager.subprocess, "Popen", boom)
    sound_manager.play("ok")  # 例外が出ないこと自体が期待値


# --- check_and_restore_sounds() ------------------------------------------------------
#
# 監査(AUDIT-029)で「壊れたときに気づくためのコード」の一つとして未検証が指摘されていた。
# 音が鳴らないのは子どもから見える不具合なので、欠損を復旧する経路を固定する。


def _setup_restore(tmp_path, monkeypatch, *, with_default_source=True):
    sound_dir = tmp_path / "sounds"
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    if with_default_source:
        (defaults / "ok.mp3").write_bytes(b"\x01\x02")
    monkeypatch.setattr(config, "SOUND_DIR", str(sound_dir))
    monkeypatch.setattr(config, "DEFAULT_SOUND_SOURCE", str(defaults))
    monkeypatch.setattr(config, "SOUND_MAP", {"ok": "ok.mp3"})
    return sound_dir, defaults


def test_check_and_restore_creates_missing_sound_directory(tmp_path, monkeypatch):
    sound_dir, _ = _setup_restore(tmp_path, monkeypatch)
    assert not sound_dir.exists()
    sound_manager.check_and_restore_sounds()
    assert sound_dir.is_dir()


def test_check_and_restore_copies_missing_file_from_defaults(tmp_path, monkeypatch):
    sound_dir, defaults = _setup_restore(tmp_path, monkeypatch)
    sound_manager.check_and_restore_sounds()
    restored = sound_dir / "ok.mp3"
    assert restored.exists()
    assert restored.read_bytes() == (defaults / "ok.mp3").read_bytes()


def test_check_and_restore_leaves_existing_file_untouched(tmp_path, monkeypatch):
    sound_dir, _ = _setup_restore(tmp_path, monkeypatch)
    sound_dir.mkdir()
    (sound_dir / "ok.mp3").write_bytes(b"user recorded")
    sound_manager.check_and_restore_sounds()
    assert (sound_dir / "ok.mp3").read_bytes() == b"user recorded"


def test_check_and_restore_does_not_raise_when_default_source_is_missing(tmp_path, monkeypatch):
    """復旧元も無い場合は「まだ足りない」と警告するだけで、起動を止めない。"""
    sound_dir, _ = _setup_restore(tmp_path, monkeypatch, with_default_source=False)
    sound_manager.check_and_restore_sounds()
    assert sound_dir.is_dir()
    assert not (sound_dir / "ok.mp3").exists()
