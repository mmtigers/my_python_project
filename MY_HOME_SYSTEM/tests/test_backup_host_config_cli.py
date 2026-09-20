# MY_HOME_SYSTEM/tests/test_backup_host_config_cli.py
"""tools/backup_host_config.py の CLI の回帰テスト (Issue #774)。

とくに**終了コード**を固定する。バックアップは cron/systemd から回す想定で、
「対象が1件も見つからなかった」「パーミッションで読めなかった」を exit 0 で
返してしまうと、何も保全できていないのに成功として扱われる
(#753 が `backup_service.py` の `__main__` で踏んだのと同じ失敗モード)。
"""
import importlib
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import host_config_backup_service as svc

cli = importlib.import_module("tools.backup_host_config")


@pytest.fixture
def fake_host(tmp_path):
    etc = tmp_path / "host" / "etc"
    etc.mkdir(parents=True)
    (etc / "fstab").write_text("//nas/x /mnt/x cifs noserverino 0 0\n", encoding="utf-8")
    return tmp_path / "host"


def _patch_targets(monkeypatch, root):
    """対象宣言を擬似ルート前提に差し替える(CLI は root を受け取らないため)。"""
    monkeypatch.setattr(svc, "HOST_CONFIG_TARGETS", (
        svc.HostConfigTarget(os.path.join(str(root), "etc/fstab"), "マウント定義"),
    ))


class TestExitCodes:
    def test_success_returns_zero(self, tmp_path, fake_host, monkeypatch, capsys):
        _patch_targets(monkeypatch, fake_host)
        rc = cli.main(["--dest", str(tmp_path / "out")])
        assert rc == 0
        assert "コピー 1 件" in capsys.readouterr().out

    def test_nothing_found_returns_one(self, tmp_path, monkeypatch, capsys):
        """対象ゼロを成功として返さないこと。実機以外で回した・候補パスが
        実態と合っていない、のどちらかであり、黙って成功にしてはならない。"""
        monkeypatch.setattr(svc, "HOST_CONFIG_TARGETS", (
            svc.HostConfigTarget("/nonexistent/path/does-not-exist", "存在しない"),
        ))
        rc = cli.main(["--dest", str(tmp_path / "out")])
        assert rc == 1
        assert "対象が1件も見つかりませんでした" in capsys.readouterr().err

    def test_read_error_returns_one(self, tmp_path, fake_host, monkeypatch, capsys):
        _patch_targets(monkeypatch, fake_host)
        real_stat = os.stat

        def _deny(path, *a, **k):
            if str(path).endswith("fstab"):
                raise PermissionError(13, "Permission denied")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(svc.os, "stat", _deny)
        rc = cli.main(["--dest", str(tmp_path / "out")])
        assert rc == 1
        err = capsys.readouterr().err
        # 読めなかったファイルは copied/manifest_only に入らないため、判定順を
        # 誤ると「候補パスが実態と合っていません」と案内してしまう。
        # root で実行していないことに気づけるよう、パーミッションの側を出すこと。
        assert "件のファイルで失敗しました" in err
        assert "対象が1件も見つかりませんでした" not in err


class TestDryRun:
    def test_dry_run_writes_nothing_and_succeeds(self, tmp_path, fake_host, monkeypatch, capsys):
        _patch_targets(monkeypatch, fake_host)
        out_root = tmp_path / "out"
        rc = cli.main(["--dry-run", "--dest", str(out_root)])
        assert rc == 0
        assert not out_root.exists()
        assert "【ドライラン】" in capsys.readouterr().out

    def test_dry_run_with_no_targets_still_succeeds(self, tmp_path, monkeypatch):
        """--dry-run は「確認」なので、対象ゼロでも失敗にはしない
        (実機以外で構成を眺めるために使う)。"""
        monkeypatch.setattr(svc, "HOST_CONFIG_TARGETS", (
            svc.HostConfigTarget("/nonexistent/x", "存在しない"),
        ))
        assert cli.main(["--dry-run", "--dest", str(tmp_path / "out")]) == 0
