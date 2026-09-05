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
