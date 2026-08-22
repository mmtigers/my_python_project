# MY_HOME_SYSTEM/tests/test_start_all_sh.py
"""
start_all.sh (サーバー再起動運用スクリプト) の静的な内容検証。

bashスクリプトを実際に実行するテストインフラ(bats等)は本リポジトリに
無いため、H-9の回帰防止として「pkillの対象パターンが実ファイル名と
一致していること」「存在しないファイルへの言及が無いこと」「force kill
(-9)がwait(段階化)より後に置かれ、全対象に適用されること」を
テキストベースで検証する。
"""
import os
import re

START_ALL_SH_PATH = os.path.join(os.path.dirname(__file__), "..", "start_all.sh")


def _read_script() -> str:
    with open(START_ALL_SH_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _cleanup_targets() -> list:
    """CLEANUP_TARGETS=(...) 配列の要素(実際にpkill/pgrepへ渡されるパターン)を抽出する"""
    script = _read_script()
    m = re.search(r"CLEANUP_TARGETS=\((.*?)\)", script, flags=re.DOTALL)
    assert m, "CLEANUP_TARGETS配列が見つかりません"
    return re.findall(r'"([^"]+)"', m.group(1))


class TestStartAllShCleanupTargets:
    def test_targets_actual_scheduler_boot_filename_not_bare_scheduler(self):
        """H-9: 実体は scheduler_boot.py であり、旧 'scheduler.py' という
        パターンはマッチしないため、pkillの対象は scheduler_boot.py であること。"""
        targets = _cleanup_targets()
        assert "scheduler_boot.py" in targets
        assert "scheduler.py" not in targets

    def test_no_reference_to_nonexistent_bluetooth_monitor(self):
        """H-9: リポジトリに存在しない bluetooth_monitor.py が
        pkill/pgrepの対象に含まれていないこと。"""
        targets = _cleanup_targets()
        assert "bluetooth_monitor.py" not in targets

    def test_force_kill_is_staged_after_a_wait_loop(self):
        """H-9: pkill -9 (SIGKILL)は、TERM送信後の待機ループより後に置かれ、
        いきなり強制終了しない段階的な構成になっていること。"""
        script = _read_script()
        wait_loop_idx = script.index("Waiting for shutdown")
        force_kill_idx = script.index("pkill -9")
        assert wait_loop_idx < force_kill_idx

    def test_all_cleanup_targets_are_force_killed_not_only_unified_server(self):
        """H-9: unified_serverだけでなく、scheduler_boot/camera_monitor/streamlitも
        生き残っていれば強制終了(-9)の対象になること(孤児化の再発防止)。
        force-kill段階も同じ CLEANUP_TARGETS 配列をループする実装になっているため、
        force-killセクション内で配列がループされていることを確認する。"""
        script = _read_script()
        force_kill_section = script[script.index("pkill -9"):]
        assert "for target in \"${CLEANUP_TARGETS[@]}\"" in script[: script.index("pkill -9")]
        assert 'pkill -9 -f "$target"' in force_kill_section

    def test_cleanup_targets_cover_all_four_known_processes(self):
        targets = _cleanup_targets()
        assert set(targets) == {
            "unified_server.py",
            "camera_monitor.py",
            "scheduler_boot.py",
            "streamlit run",
        }
