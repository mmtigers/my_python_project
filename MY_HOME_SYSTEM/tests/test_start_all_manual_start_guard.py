# MY_HOME_SYSTEM/tests/test_start_all_manual_start_guard.py
"""start_all.sh の手動起動ガード (Issue #824) の挙動テスト。

systemd がサーバー/ダッシュボードを管理している実機でフルモード(引数なし)を実行すると、
掃除フェーズが systemd 管理下のプロセスへ SIGTERM を送り(= systemd から見て正常終了なので
Restart=on-failure は再起動しない)、Phase 4 が nohup で起動し直してプロセスが systemd の
管理外に出る。サイトは孤児が応答するので普通に動いて見え、#646 の自動復旧だけが黙って
止まる。2026-09-21 09:54 に実機で起き、約1時間半 NRestarts=344 のまま放置された。

## テストの仕組み

`test_start_all_sh.py` は「bash を実際に実行するテストインフラが無い」ためテキスト検証に
留めているが、ガードは**スクリプトの先頭**にあり、その手前には引数解析しか無い。そこで
スクリプトを `# --- end 手動起動ガード ---` の行で切り出し、末尾に `echo GUARD_PASSED` を
足したものを実行する。こうすると

- 検証しているのは**本物のガードのコードそのもの**(写しではない)
- ガードを通過しても、その先(掃除・NAS待ち・サーバー起動)は**一切実行されない**

ので、実機(Raspberry Pi)上で走らせても安全で、CI でも同じように動く。

`systemctl` は PATH 上のスタブで差し替え、PATH にはスタブのディレクトリだけを置く。
ガードは外部コマンドとして `systemctl` しか使わない(出力は printf ビルトイン)ので、
「systemd の無い環境」も PATH から外すだけで再現できる。
"""
import os
import shutil
import stat
import subprocess

import pytest

START_ALL_SH_PATH = os.path.join(os.path.dirname(__file__), "..", "start_all.sh")
GUARD_END_MARKER = "# --- end 手動起動ガード ---"
BASH = shutil.which("bash")

# `systemctl is-enabled --quiet <unit>` だけに応答するスタブ。有効なユニットは環境変数
# ENABLED_UNITS(空白区切り)で与える。PATH にスタブしか無いため、外部コマンドは使わず
# シェルのビルトインだけで書く。
_SYSTEMCTL_STUB = """#!/bin/sh
[ "$1" = "is-enabled" ] || exit 1
for arg in "$@"; do unit="$arg"; done
case " $ENABLED_UNITS " in
  *" $unit "*) exit 0 ;;
  *) exit 1 ;;
esac
"""

pytestmark = pytest.mark.skipif(BASH is None, reason="bash が無い環境")


def _guard_prefix() -> str:
    """スクリプトの先頭からガードの終わりまでを返す。"""
    with open(START_ALL_SH_PATH, encoding="utf-8") as f:
        script = f.read()
    assert GUARD_END_MARKER in script, (
        f"{GUARD_END_MARKER!r} が見つからない。マーカーを消すとこのテストが"
        "ガードを切り出せず、実機でスクリプト全体を走らせる危険な形になる"
    )
    return script[: script.index(GUARD_END_MARKER) + len(GUARD_END_MARKER)]


def _run(tmp_path, *, args=(), enabled_units=None, with_systemctl=True, env_extra=None):
    """切り出したガードを実行し (returncode, stdout, stderr) を返す。"""
    script = tmp_path / "guard_only.sh"
    script.write_text(_guard_prefix() + "\necho GUARD_PASSED\n", encoding="utf-8")

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    if with_systemctl:
        stub = stub_dir / "systemctl"
        stub.write_text(_SYSTEMCTL_STUB, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    env = {
        # 実機の /usr/bin/systemctl を拾わないよう、PATH はスタブだけにする。
        "PATH": str(stub_dir),
        "ENABLED_UNITS": " ".join(enabled_units or []),
    }
    env.update(env_extra or {})
    proc = subprocess.run(
        [BASH, str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        # 拒否(exit 1)も検証対象なので、非ゼロ終了で例外にしない。
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestRefusesWhenSystemdManagesTheServices:
    def test_refuses_full_mode_when_home_system_is_enabled(self, tmp_path):
        """これが本 Issue そのもの。掃除に進む前に止まること。"""
        rc, out, err = _run(tmp_path, enabled_units=["home_system.service"])
        assert rc == 1
        assert "GUARD_PASSED" not in out, "ガードを素通りして掃除フェーズへ進んでいる"
        assert "home_system.service" in err

    def test_message_tells_the_correct_way_to_restart(self, tmp_path):
        """拒否するだけでなく、正しい手順が読み取れること。

        #829: ダッシュボードはStreamlit版(別ユニット home_dashboard.service)を廃止し
        unified_server.py 自身が配信するようになったため、再起動対象は
        home_system.service だけになった。"""
        _, _, err = _run(tmp_path, enabled_units=["home_system.service"])
        assert "systemctl restart home_system.service" in err
        assert "home_dashboard.service" not in err
        assert "ALLOW_MANUAL_START=1" in err

    @pytest.mark.parametrize("value", ["0", "true", "yes", ""])
    def test_only_exact_1_overrides(self, tmp_path, value):
        """上書きは ALLOW_MANUAL_START=1 のときだけ。曖昧な値で素通りさせない。"""
        rc, out, _ = _run(
            tmp_path,
            enabled_units=["home_system.service"],
            env_extra={"ALLOW_MANUAL_START": value},
        )
        assert rc == 1
        assert "GUARD_PASSED" not in out


class TestAllowsTheLegitimatePaths:
    def test_prepare_mode_is_never_blocked(self, tmp_path):
        """systemd の ExecStartPre(--prepare)自体を止めてはいけない。止めるとサーバーが起動しない。"""
        rc, out, _ = _run(
            tmp_path,
            args=["--prepare"],
            enabled_units=["home_system.service"],
        )
        assert rc == 0
        assert "GUARD_PASSED" in out

    def test_explicit_override_proceeds_with_a_warning(self, tmp_path):
        rc, out, err = _run(
            tmp_path,
            enabled_units=["home_system.service"],
            env_extra={"ALLOW_MANUAL_START": "1"},
        )
        assert rc == 0
        assert "GUARD_PASSED" in out
        assert "自動再起動されません" in err, "上書き時も管理外になることを警告すること"

    def test_proceeds_when_units_are_not_enabled(self, tmp_path):
        """ユニットを disable した実機では従来どおり手動起動できる。"""
        rc, out, _ = _run(tmp_path, enabled_units=[])
        assert rc == 0
        assert "GUARD_PASSED" in out

    def test_proceeds_when_systemctl_does_not_exist(self, tmp_path):
        """systemd の無い環境(開発機・CI)では従来どおり動く。"""
        rc, out, _ = _run(tmp_path, with_systemctl=False)
        assert rc == 0
        assert "GUARD_PASSED" in out


class TestGuardPlacement:
    """ガードは「何かを止める前」に置かれていなければ意味が無い。"""

    def _script(self):
        with open(START_ALL_SH_PATH, encoding="utf-8") as f:
            return f.read()

    def test_guard_runs_before_the_cleanup_phase(self):
        script = self._script()
        assert script.index(GUARD_END_MARKER) < script.index("# まずは優しく停止"), (
            "ガードが掃除フェーズより後ろにある。拒否した時点で既にプロセスを止めている"
        )

    def test_guard_runs_before_changing_directory(self):
        """cd の失敗(CI 等)と、ガードによる拒否を取り違えないため、cd より前にあること。"""
        script = self._script()
        assert script.index(GUARD_END_MARKER) < script.index('cd "$PROJECT_DIR"')

    def test_guard_uses_is_enabled_not_is_active(self):
        """is-active だと、この事故で既に inactive に落ちた状態から再度叩いたとき素通りする。"""
        # コメント(なぜ is-active ではないかの説明)に語が出てくるので、実行される行だけを見る。
        code = "\n".join(
            line for line in _guard_prefix().splitlines() if not line.lstrip().startswith("#")
        )
        assert "is-enabled" in code
        assert "is-active" not in code
