# MY_HOME_SYSTEM/tests/test_nas_utils.py
"""
core/nas_utils.py の Issue #111 回帰テスト。

nas_utils.py はモジュールロード時に `import config` を含む try/except ImportError
フォールバックを持つ。以前はこのフォールバックが `get_logger`/`send_push` のみを
定義し `config` を定義していなかったため、`import config` 自体が失敗する状況
(依存欠如・循環import等)では `config` という名前が一度も束縛されないまま
モジュールロードが完了してしまい、`get_managed_target_directory` のNAS復旧失敗
経路で `getattr(config, "LINE_USER_ID", None)` を評価した瞬間に
`NameError: name 'config' is not defined` が送出されていた。本来この関数は
NAS障害時でもフォールバックディレクトリを返すフェイルソフト設計のため、
これは意図しない例外による処理停止だった。

同一プロセス内での importlib.reload + import mock では、「configが一度も
成功importされたことがない状態でのモジュール初回ロード」という状況を
正しく再現できない(reloadは既存のモジュール名前空間を使い回すため、以前の
成功importの残骸で偶然マスクされてしまう)。そのため本テストは、`config`の
importを常にブロックするインポートフックを最初から仕込んだ、まっさらな
サブプロセスでcore.nas_utilsを初めてimportすることで再現する。
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

MY_HOME_SYSTEM_DIR = Path(__file__).resolve().parent.parent

_PROBE_SCRIPT = textwrap.dedent(
    """\
    import sys
    from pathlib import Path
    from unittest.mock import patch

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    class _BlockConfigImport:
        \"\"\"config モジュールの import だけを常に失敗させるフック。
        nas_utils.py の `try: import config ...` を、configが一度も
        importに成功したことがない状態で確実に失敗させるために使う。\"\"\"
        def find_spec(self, name, path, target=None):
            if name == "config":
                raise ImportError("simulated: config import blocked")
            return None

    sys.meta_path.insert(0, _BlockConfigImport())

    from core import nas_utils

    assert nas_utils.config is None, f"config should be None, got {nas_utils.config!r}"

    fallback_dir = Path(sys.argv[1])
    with patch.object(nas_utils, "is_mounted_and_writable", return_value=False), \\
         patch.object(nas_utils, "attempt_remount", return_value=False):
        result = nas_utils.get_managed_target_directory(
            nas_dir_str="/mnt/nas/does-not-exist",
            fallback_dir_str=str(fallback_dir),
        )

    assert result == fallback_dir, f"expected {fallback_dir}, got {result}"
    assert fallback_dir.exists(), "fallback directory should have been created"
    print("PROBE_OK")
    """
)


def test_get_managed_target_directory_falls_back_without_nameerror_when_config_import_fails(tmp_path):
    """
    サブプロセス(まっさらなインタプリタ)でconfigのimportを常に失敗させた状態で
    core.nas_utils を初めてimportし、get_managed_target_directory がNAS復旧失敗
    経路まで到達しても NameError('config' is not defined) を送出せず、フェイル
    ソフトどおりフォールバックディレクトリを作成して返すことを確認する。
    """
    probe_path = MY_HOME_SYSTEM_DIR / "tests" / "_probe_nas_utils_config_import_failure.py"
    fallback_dir = tmp_path / "fallback"
    try:
        probe_path.write_text(_PROBE_SCRIPT, encoding="utf-8")
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, str(probe_path), str(fallback_dir)],
            cwd=str(MY_HOME_SYSTEM_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        probe_path.unlink(missing_ok=True)

    assert result.returncode == 0, (
        f"probe script failed (config importのブロック時にNameError等で"
        f"落ちた可能性がある):\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PROBE_OK" in result.stdout


class TestAttemptRemountTimeout:
    """#411 S-L9: attempt_remountのsudo mount呼出しにtimeoutが無く、
    autofsのデッドロックやネットワークマウントのハング時に無期限ブロックしうる
    問題の回帰テスト。"""

    def test_passes_a_timeout_to_subprocess_run(self, monkeypatch):
        sys.path.insert(0, str(MY_HOME_SYSTEM_DIR))
        from core import nas_utils

        captured_kwargs = {}

        def _fake_run(cmd, **kwargs):
            captured_kwargs.update(kwargs)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(nas_utils.subprocess, "run", _fake_run)

        assert nas_utils.attempt_remount("/mnt/nas") is True
        assert captured_kwargs.get("timeout") is not None
        assert captured_kwargs["timeout"] > 0

    def test_timeout_expired_is_treated_as_failure_not_a_crash(self, monkeypatch):
        sys.path.insert(0, str(MY_HOME_SYSTEM_DIR))
        from core import nas_utils

        def _fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

        monkeypatch.setattr(nas_utils.subprocess, "run", _fake_run)

        assert nas_utils.attempt_remount("/mnt/nas") is False


# ---------------------------------------------------------------------------
# Issue #758 (AUDIT-029): NAS のマウント判定・フォールバック・同期の各分岐。
#
# NAS は録画・HLS・バックアップの中心で、専用の障害調査レポート
# (docs/reports/MY_HOME_SYSTEM/NAS_TIMEOUT_INVESTIGATION_2026-08-24.md)まで
# 存在する既知の障害領域である。さらに DDD の3スクリプトがこのモジュールを
# 実依存で import している(#553)。subprocess.run / os.path.ismount を
# monkeypatch し、実際の NAS には一切触れずに分岐を通す。
# ---------------------------------------------------------------------------
import pytest

sys.path.insert(0, str(MY_HOME_SYSTEM_DIR))
from core import nas_utils


@pytest.fixture
def quiet_notifications(monkeypatch):
    """NAS 復旧失敗時の send_push を記録に差し替える(本物の通知を飛ばさない)。"""
    sent = []
    monkeypatch.setattr(nas_utils, "send_push", lambda messages, **kwargs: sent.append((messages, kwargs)))
    return sent


class TestAttemptRemount:
    def test_returns_true_on_successful_mount(self, monkeypatch):
        monkeypatch.setattr(
            nas_utils.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )
        assert nas_utils.attempt_remount("/mnt/nas") is True

    def test_returns_false_on_non_zero_exit(self, monkeypatch):
        monkeypatch.setattr(
            nas_utils.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 32, stdout="", stderr="mount: permission denied\n"),
        )
        assert nas_utils.attempt_remount("/mnt/nas") is False

    def test_unexpected_exception_is_treated_as_failure(self, monkeypatch):
        def _boom(cmd, **kwargs):
            raise OSError("sudo not found")

        monkeypatch.setattr(nas_utils.subprocess, "run", _boom)
        assert nas_utils.attempt_remount("/mnt/nas") is False


class TestIsMountedAndWritable:
    def test_false_when_mount_point_is_not_a_mount(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nas_utils.os.path, "ismount", lambda p: False)
        assert nas_utils.is_mounted_and_writable(tmp_path / "target", "/mnt/nas") is False

    def test_true_when_mounted_and_target_is_writable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nas_utils.os.path, "ismount", lambda p: True)
        target = tmp_path / "nas" / "assets"
        assert nas_utils.is_mounted_and_writable(target, "/mnt/nas") is True
        assert target.is_dir(), "初回起動時にターゲットディレクトリが作成されること"

    def test_false_when_target_cannot_be_created(self, tmp_path, monkeypatch):
        """mkdir が OSError(NAS が読み取り専用等)なら書き込み可能とみなさない。"""
        monkeypatch.setattr(nas_utils.os.path, "ismount", lambda p: True)

        def _boom(*args, **kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(nas_utils.Path, "mkdir", _boom)
        assert nas_utils.is_mounted_and_writable(tmp_path / "target", "/mnt/nas") is False

    def test_false_when_target_is_not_writable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nas_utils.os.path, "ismount", lambda p: True)
        monkeypatch.setattr(nas_utils.os, "access", lambda p, mode: False)
        assert nas_utils.is_mounted_and_writable(tmp_path / "target", "/mnt/nas") is False


class TestSyncFallbackToNas:
    def test_noop_when_fallback_is_missing_or_empty(self, tmp_path):
        nas_dir = tmp_path / "nas"
        nas_dir.mkdir()
        nas_utils.sync_fallback_to_nas(tmp_path / "does-not-exist", nas_dir)
        empty = tmp_path / "empty"
        empty.mkdir()
        nas_utils.sync_fallback_to_nas(empty, nas_dir)
        assert list(nas_dir.iterdir()) == []

    def test_files_and_directories_are_moved_to_nas(self, tmp_path):
        local = tmp_path / "fallback"
        (local / "sub").mkdir(parents=True)
        (local / "a.txt").write_text("a", encoding="utf-8")
        (local / "sub" / "b.txt").write_text("b", encoding="utf-8")
        nas_dir = tmp_path / "nas"
        nas_dir.mkdir()

        nas_utils.sync_fallback_to_nas(local, nas_dir)

        assert (nas_dir / "a.txt").read_text(encoding="utf-8") == "a"
        assert (nas_dir / "sub" / "b.txt").read_text(encoding="utf-8") == "b"
        # SSOT を NAS に戻すため、ローカル側は空になる
        assert list(local.iterdir()) == []

    def test_existing_nas_data_is_overwritten_by_the_newer_local_copy(self, tmp_path):
        """フォールバック中に書かれたローカルのほうが新しい前提の、意図した上書き。"""
        local = tmp_path / "fallback"
        local.mkdir()
        (local / "a.txt").write_text("new", encoding="utf-8")
        nas_dir = tmp_path / "nas"
        nas_dir.mkdir()
        (nas_dir / "a.txt").write_text("old", encoding="utf-8")

        nas_utils.sync_fallback_to_nas(local, nas_dir)

        assert (nas_dir / "a.txt").read_text(encoding="utf-8") == "new"

    def test_copy_failure_is_logged_and_does_not_raise(self, tmp_path, monkeypatch):
        local = tmp_path / "fallback"
        local.mkdir()
        (local / "a.txt").write_text("a", encoding="utf-8")
        nas_dir = tmp_path / "nas"
        nas_dir.mkdir()

        def _boom(src, dst):
            raise OSError("NAS disconnected mid-copy")

        monkeypatch.setattr(nas_utils.shutil, "copy2", _boom)

        nas_utils.sync_fallback_to_nas(local, nas_dir)  # 例外が漏れないこと

        # コピーに失敗したローカルのデータは消えていない(unlink まで到達しない)
        assert (local / "a.txt").exists()


class TestGetManagedTargetDirectory:
    def test_returns_nas_dir_and_syncs_when_mounted(self, tmp_path, monkeypatch, quiet_notifications):
        nas_dir = tmp_path / "nas" / "assets"
        nas_dir.mkdir(parents=True)
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        (fallback / "pending.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr(nas_utils, "is_mounted_and_writable", lambda t, m: True)

        result = nas_utils.get_managed_target_directory(str(nas_dir), str(fallback))

        assert result == nas_dir
        assert (nas_dir / "pending.txt").exists(), "復旧時に溜まったフォールバックを同期すること"
        assert quiet_notifications == []

    def test_remount_success_returns_nas_dir(self, tmp_path, monkeypatch, quiet_notifications):
        nas_dir = tmp_path / "nas" / "assets"
        nas_dir.mkdir(parents=True)
        fallback = tmp_path / "fallback"
        states = iter([False, True])
        monkeypatch.setattr(nas_utils, "is_mounted_and_writable", lambda t, m: next(states))
        monkeypatch.setattr(nas_utils, "attempt_remount", lambda m: True)

        result = nas_utils.get_managed_target_directory(str(nas_dir), str(fallback))

        assert result == nas_dir
        assert quiet_notifications == []

    def test_remount_succeeds_but_still_unwritable_falls_back(self, tmp_path, monkeypatch, quiet_notifications):
        """mount コマンドは成功しても、書き込み確認が通らなければフォールバックする。"""
        nas_dir = tmp_path / "nas" / "assets"
        fallback = tmp_path / "fallback"
        monkeypatch.setattr(nas_utils, "is_mounted_and_writable", lambda t, m: False)
        monkeypatch.setattr(nas_utils, "attempt_remount", lambda m: True)

        result = nas_utils.get_managed_target_directory(str(nas_dir), str(fallback))

        assert result == fallback
        assert fallback.is_dir()
        assert len(quiet_notifications) == 1

    def test_unrecoverable_failure_notifies_and_falls_back(self, tmp_path, monkeypatch, quiet_notifications):
        nas_dir = tmp_path / "nas" / "assets"
        fallback = tmp_path / "fallback" / "assets"
        monkeypatch.setattr(nas_utils, "is_mounted_and_writable", lambda t, m: False)
        monkeypatch.setattr(nas_utils, "attempt_remount", lambda m: False)

        result = nas_utils.get_managed_target_directory(str(nas_dir), str(fallback))

        assert result == fallback
        assert fallback.is_dir(), "Fail-Soft: フォールバック先は必ず作成される"
        # Issue #289: LINE 宛先(user_id)は不要で、discord/error へ1回だけ通知する
        messages, kwargs = quiet_notifications[0]
        assert kwargs == {"target": "discord", "channel": "error"}
        assert str(nas_dir) in messages[0]["text"]
