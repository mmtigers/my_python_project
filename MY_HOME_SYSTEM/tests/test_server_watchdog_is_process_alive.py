# MY_HOME_SYSTEM/tests/test_server_watchdog_is_process_alive.py
"""
monitors/server_watchdog.py の is_process_alive の回帰テスト (#411 S-L11)。

以前は `pgrep -f process_keyword` の単純な部分文字列マッチだったため、
`cat unified_server.py` のような無関係なコマンドの引数にキーワードが含まれる
だけでもヒットしてしまい、本来のサーバープロセスが落ちていても誤って
「生きている」と判定しうる誤検知の余地があった。
"""
import os
import re
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import server_watchdog


def _matches(cmdline: str) -> bool:
    """is_process_alive内部で組み立てる正規表現が、実際のpgrep(POSIX拡張正規表現互換の
    re.search)相当でcmdlineにマッチするかどうかを直接検証する(pgrep自体は起動しない)。"""
    pattern = rf"python[0-9.]*\s+\S*{re.escape('unified_server.py')}(\s|$)"
    return re.search(pattern, cmdline) is not None


def test_matches_real_server_invocation():
    assert _matches("/usr/bin/python3 /home/masahiro/develop/MY_HOME_SYSTEM/unified_server.py")
    assert _matches("python unified_server.py")


def test_does_not_match_unrelated_commands_referencing_the_filename():
    # 無関係なコマンドの引数にファイル名が含まれるだけではヒットしない
    assert not _matches("cat unified_server.py")
    assert not _matches("vim unified_server.py")
    assert not _matches("cp unified_server.py unified_server.py.bak")
    assert not _matches("git diff unified_server.py")


def test_is_process_alive_returns_false_when_pgrep_finds_nothing(monkeypatch):
    import subprocess

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(server_watchdog.subprocess, "run", _fake_run)
    assert server_watchdog.is_process_alive("unified_server.py") is False


def test_is_process_alive_passes_a_regex_pattern_to_pgrep(monkeypatch):
    import subprocess

    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(server_watchdog.subprocess, "run", _fake_run)
    assert server_watchdog.is_process_alive("unified_server.py") is True
    assert captured["cmd"][:2] == ["pgrep", "-f"]
    assert "unified_server" in captured["cmd"][2]
    assert captured["cmd"][2] != "unified_server.py"
