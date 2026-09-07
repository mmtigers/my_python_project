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
    # 配列要素(正規表現)自体に括弧を含みうるため、行頭の ")" までを配列本体とみなす
    m = re.search(r"CLEANUP_TARGETS=\((.*?)\n\)", script, flags=re.DOTALL)
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
        # #360: scheduler 配下の監視スクリプトと HLS 用 ffmpeg も停止対象に含める
        assert set(targets) == {
            "unified_server.py",
            "camera_monitor.py",
            "scheduler_boot.py",
            "streamlit run",
            "python.*monitors/(switchbot_power_monitor|nature_remo_monitor|server_watchdog|tv_lock_monitor|memory_monitor|nas_monitor)\\.py",
            "ffmpeg.*hls_streams",
        }

    def test_monitors_pattern_only_matches_scheduler_children(self):
        """監視スクリプトの停止パターンは scheduler_boot.TASKS が起動するものだけに一致し、
        systemd(network_logger.service)や cron(health_watch/daily_timelapse_job/log_analyzer)
        で独立に動くスクリプトを巻き添えで SIGTERM しないこと。"""
        import re
        import scheduler_boot

        pattern = next(t for t in _cleanup_targets() if t.startswith("python.*monitors/"))
        regex = re.compile(pattern)
        for task in scheduler_boot.TASKS:
            cmdline = f"/home/masahiro/develop/MY_HOME_SYSTEM/.venv/bin/python3 {task['script']}"
            assert regex.search(cmdline), f"scheduler child not covered: {task['script']}"
        for independent in ("network_logger", "health_watch", "daily_timelapse_job", "log_analyzer", "smart_timelapse_generator", "camera_monitor"):
            cmdline = f"/home/masahiro/develop/MY_HOME_SYSTEM/.venv/bin/python3 monitors/{independent}.py"
            assert not regex.search(cmdline), f"independent process would be killed: {independent}"


class TestStartAllShBackgroundProcessesSurviveLogout:
    """M-8-4の一部: バックグラウンド起動('&'のみ)がSSHログアウト時にシェルから
    SIGHUPを受けて終了してしまう余地があった問題。nohupでSIGHUPを無視し、
    disownでシェルのジョブ管理からも外していることを検証する。"""

    def _background_launch_lines(self) -> list:
        script = _read_script()
        return [line for line in script.splitlines() if line.rstrip().endswith("&")]

    def test_background_launches_use_nohup(self):
        launch_lines = self._background_launch_lines()
        assert launch_lines, "バックグラウンド起動('&')の行が見つかりません"
        for line in launch_lines:
            assert "nohup" in line, (
                f"バックグラウンド起動にnohupが付いておらず、SSHログアウトで"
                f"SIGHUP終了する余地がある: {line!r}"
            )

    def test_background_launches_are_disowned(self):
        script = _read_script()
        launch_lines = self._background_launch_lines()
        lines = script.splitlines()
        for line in launch_lines:
            idx = lines.index(line)
            following = "\n".join(lines[idx + 1: idx + 3])
            assert "disown" in following, (
                f"バックグラウンド起動の直後にdisownが無く、シェルのジョブ管理から"
                f"外れていない: {line!r}"
            )


class TestStartAllShPythonDependencyFreshnessCheck:
    """Issue #483: requirements.txt 変更後に実機の .venv が追従しないと
    ImportError で起動失敗しうる問題への対応(family-quest の deploy.sh
    --if-stale と同じ、ハッシュ比較による冪等チェック)を検証する。"""

    def test_dependency_freshness_check_runs_between_nas_mount_and_frontend_build(self):
        script = _read_script()
        nas_phase_idx = script.index("Check NAS Mount")
        dependency_phase_idx = script.index("Check Python dependencies freshness")
        frontend_phase_idx = script.index("Ensure family-quest dist is fresh")
        assert nas_phase_idx < dependency_phase_idx < frontend_phase_idx

    def test_hash_file_is_stored_under_venv_directory(self):
        script = _read_script()
        m = re.search(r'REQ_HASH_FILE="([^"]+)"', script)
        assert m, "REQ_HASH_FILE の定義が見つかりません"
        assert m.group(1).startswith(".venv/"), (
            ".venv/ は gitignore 済みのため、ハッシュファイルもここに置き "
            "副作用を出さないこと"
        )

    def test_pip_install_uses_python_exec_and_targets_requirements_txt(self):
        script = _read_script()
        assert '"$PYTHON_EXEC" -m pip install -r requirements.txt' in script

    def test_pip_install_failure_does_not_abort_startup(self):
        """pip install失敗時は警告を出すのみで、スクリプトの実行(サーバー起動)を
        continueすること(旧依存で動かす方が起動失敗より優先される)。"""
        script = _read_script()
        section_start = script.index("Check Python dependencies freshness")
        section_end = script.index("Ensure family-quest dist is fresh")
        section = script[section_start:section_end]
        assert "pip install failed" in section
        assert "exit" not in section


class TestStartAllShGitHooksRegistration:
    """Phase 1.6: post-merge フック(deploy/git-hooks/)の core.hooksPath 登録。

    以前は .git/hooks/post-merge にローカル設置していて git 管理外だったため、
    clone し直すたびに手で再設置が必要だった。リポジトリ管理のフックディレクトリを
    start_all.sh が冪等に登録すること、およびフック本体が実行可能な状態で
    コミットされていることを検証する。
    """

    REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")

    def test_registers_repo_managed_hooks_dir_as_core_hookspath(self):
        script = _read_script()
        assert 'HOOKS_DIR="$DEVELOP_ROOT/deploy/git-hooks"' in script
        assert 'git -C "$DEVELOP_ROOT" config core.hooksPath "$HOOKS_DIR"' in script

    def test_registration_is_idempotent_and_never_aborts_startup(self):
        """既に登録済みなら再設定せず、失敗しても警告のみでサーバー起動へ進むこと。"""
        script = _read_script()
        assert 'config --get core.hooksPath)" != "$HOOKS_DIR"' in script
        phase = script[script.index("Register git hooks"): script.index("Ensure family-quest dist is fresh")]
        assert "exit" not in phase, "フック登録の失敗でサーバー起動を止めてはいけない"

    def test_registration_runs_before_frontend_freshness_check(self):
        script = _read_script()
        assert script.index("Register git hooks") < script.index("Ensure family-quest dist is fresh")

    def test_post_merge_hook_exists_and_is_executable(self):
        hook = os.path.join(self.REPO_ROOT, "deploy", "git-hooks", "post-merge")
        assert os.path.isfile(hook), "deploy/git-hooks/post-merge がリポジトリに無い"
        assert os.access(hook, os.X_OK), "post-merge に実行権限が無い(chmod +x してコミットすること)"
        with open(hook, "r", encoding="utf-8") as f:
            content = f.read()
        assert content.startswith("#!"), "shebang が無い"
        assert "deploy.sh" in content and "--if-stale" in content
