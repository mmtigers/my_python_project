"""scripts/firewall_apply.sh のテスト(2026-09-19)。

iptables と ip を、呼び出しを記録し状態(チェーンの有無・INPUT の入口の有無)を持つ
スタブに差し替えて実行する。実機の iptables には一切触れない。
"""
import os
import stat
import subprocess
import textwrap

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "firewall_apply.sh")

IPTABLES_STUB = textwrap.dedent("""\
    #!/bin/bash
    # 呼び出しを記録し、チェーン/入口の有無だけを状態ファイルで表現するスタブ
    echo "$*" >> "$STUB_DIR/calls.log"
    case "$1" in
      -N) [ -e "$STUB_DIR/chain" ] && exit 1; touch "$STUB_DIR/chain" ;;
      -L) [ -e "$STUB_DIR/chain" ] || exit 1 ;;
      -X) rm -f "$STUB_DIR/chain" ;;
      -C) [ -e "$STUB_DIR/hook" ] || exit 1 ;;
      -I) touch "$STUB_DIR/hook" ;;
      -D) rm -f "$STUB_DIR/hook" ;;
    esac
    exit 0
""")

IP_STUB = textwrap.dedent("""\
    #!/bin/bash
    printf '%b' "$STUB_ROUTES"
""")

# 実機と同じく、有線と無線が同じ LAN に直結している構成
TWO_NICS_SAME_LAN = (
    "192.168.1.0/24 dev eth0 proto kernel src 192.168.1.15 metric 100 \\n"
    "192.168.1.0/24 dev wlan0 proto kernel src 192.168.1.200 metric 600 \\n"
)


@pytest.fixture
def stub(tmp_path):
    for name, body in (("iptables", IPTABLES_STUB), ("ip", IP_STUB)):
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def run(*args, routes=TWO_NICS_SAME_LAN):
        env = dict(os.environ, STUB_DIR=str(tmp_path), STUB_ROUTES=routes,
                   IPTABLES=str(tmp_path / "iptables"), IP_CMD=str(tmp_path / "ip"))
        result = subprocess.run(["bash", SCRIPT, *args], env=env, capture_output=True, text=True, check=False)
        log = tmp_path / "calls.log"
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return result, calls

    return run


def test_rules_allow_loopback_lan_tailscale_then_drop(stub):
    result, calls = stub()
    assert result.returncode == 0, result.stderr
    appended = [c for c in calls if c.startswith("-A HOME_APP_8000")]
    assert appended == [
        "-A HOME_APP_8000 -s 127.0.0.0/8 -j RETURN",
        "-A HOME_APP_8000 -s 192.168.1.0/24 -j RETURN",  # 有線/無線の同じサブネットは1本にまとめる
        "-A HOME_APP_8000 -s 100.64.0.0/10 -j RETURN",
        "-A HOME_APP_8000 -m limit --limit 5/min -j LOG --log-prefix HOME_APP_8000 DROP: ",
        "-A HOME_APP_8000 -j DROP",
    ]
    assert "-I INPUT 1 -p tcp --dport 8000 -j HOME_APP_8000" in calls


def test_only_port_8000_is_hooked(stub):
    _result, calls = stub()
    hooks = [c for c in calls if c.startswith(("-I INPUT", "-A INPUT"))]
    assert hooks == ["-I INPUT 1 -p tcp --dport 8000 -j HOME_APP_8000"]


def test_rerun_is_idempotent(stub):
    stub()
    _result, calls = stub()
    # 2回目はチェーンを空にして積み直すだけで、INPUT の入口は増やさない
    assert "-F HOME_APP_8000" in calls
    assert not [c for c in calls if c.startswith("-I INPUT")]


def test_fails_open_when_no_directly_connected_subnet(stub):
    stub()  # いったん制限を掛けた状態から
    result, calls = stub(routes="")
    assert result.returncode == 0
    assert "制限せずに終了" in result.stdout
    assert not [c for c in calls if c.endswith("-j DROP")]
    # 家族の端末を締め出さないよう、既存の制限も外す
    assert "-D INPUT -p tcp --dport 8000 -j HOME_APP_8000" in calls
    assert "-X HOME_APP_8000" in calls


def test_remove_rolls_back(stub):
    stub()
    result, calls = stub("--remove")
    assert result.returncode == 0
    assert "-D INPUT -p tcp --dport 8000 -j HOME_APP_8000" in calls
    assert "-X HOME_APP_8000" in calls
    assert not [c for c in calls if c.startswith("-A ")]


def test_remove_without_existing_rules_is_harmless(stub):
    result, calls = stub("--remove")
    assert result.returncode == 0
    assert not [c for c in calls if c.startswith(("-D", "-X", "-F"))]
