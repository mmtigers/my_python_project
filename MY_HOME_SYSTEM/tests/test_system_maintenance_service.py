# MY_HOME_SYSTEM/tests/test_system_maintenance_service.py
"""services/system_maintenance_service.py の restart_home_system のテスト。

subprocess.run(sudo systemctl restart ...)は実際には実行せずモックし、
成功・タイムアウト・その他の失敗(CalledProcessError/OSError)の3経路が
それぞれ正しい (成功したか, メッセージ) を返すことを検証する。
"""
import os
import subprocess
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import system_maintenance_service


def test_success_returns_true_with_message(monkeypatch):
    monkeypatch.setattr(
        system_maintenance_service.subprocess, "run", lambda *a, **k: None
    )
    success, msg = system_maintenance_service.restart_home_system()
    assert success is True
    assert "再起動コマンドを送信しました" == msg


def test_timeout_returns_false_without_raising(monkeypatch):
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=system_maintenance_service.RESTART_TIMEOUT_SEC)

    monkeypatch.setattr(system_maintenance_service.subprocess, "run", _raise_timeout)
    success, msg = system_maintenance_service.restart_home_system()
    assert success is False
    assert f"{system_maintenance_service.RESTART_TIMEOUT_SEC} 秒以内に完了しませんでした" in msg


def test_called_process_error_returns_false_with_message(monkeypatch):
    def _raise(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd="systemctl")

    monkeypatch.setattr(system_maintenance_service.subprocess, "run", _raise)
    success, msg = system_maintenance_service.restart_home_system()
    assert success is False
    assert "エラー" in msg


def test_missing_sudo_binary_returns_false_with_message(monkeypatch):
    """sudo/systemctlがPATHに無い実行環境(OSError系)も呼び出し元を壊さない。"""
    def _raise(*args, **kwargs):
        raise FileNotFoundError("sudo が見つかりません")

    monkeypatch.setattr(system_maintenance_service.subprocess, "run", _raise)
    success, msg = system_maintenance_service.restart_home_system()
    assert success is False
    assert "エラー" in msg
