# .github/scripts/test_crontab_run_task.py
"""Issue #947 の回帰テスト(旧 test_crontab_newface_monitor_log_dir.py〈Issue #587〉を置き換え)。

`deploy/cron/crontab` の全エントリは `MY_HOME_SYSTEM/run_task.sh` を経由すること。
経由しないと、Python が起動する前の失敗(ImportError・.venv破損)が誰にも通知されず、
rclone のように出力を捨てるエントリは失敗が常に無言になる(`run_task.sh` は終了コード非0の
ときだけ `tools/notify_task_failure.py` で通知する。Issue #751)。

あわせて `run_task.sh` の挙動(作業ディレクトリ・ログ先の上書き、ログ先の mkdir -p、
.sh の実行、失敗時の通知呼び出し)を、パスを差し替えた一時コピーで検証する。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CRONTAB = REPO_ROOT / "deploy" / "cron" / "crontab"
RUN_TASK = REPO_ROOT / "MY_HOME_SYSTEM" / "run_task.sh"
RUN_TASK_PATH = "/home/masahiro/develop/MY_HOME_SYSTEM/run_task.sh"


def _crontab_lines():
    return [
        line for line in CRONTAB.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _entries_for(script: str):
    return [line for line in _crontab_lines() if script in line]


def test_every_cron_entry_goes_through_run_task():
    for line in _crontab_lines():
        assert RUN_TASK_PATH in line, f"run_task.sh を経由しないcronエントリがあります: {line}"


def test_no_cron_entry_discards_all_output():
    for line in _crontab_lines():
        assert "/dev/null" not in line, f"出力を捨てるcronエントリがあります(失敗が無言になる): {line}"


def test_newface_monitor_runs_hourly_in_ddd_with_ddd_logs():
    (line,) = _entries_for("newface_monitor.py")
    assert line.strip().startswith("0 * * * *"), f"1時間毎の実行のはずです: {line}"
    assert "RUN_TASK_CWD=/home/masahiro/develop/DDD" in line
    # ログ先は従来どおり DDD/logs(logrotate の対象パス)
    assert "RUN_TASK_LOG_DIR=/home/masahiro/develop/DDD/logs" in line


def test_timelapse_entries_keep_schedule_and_arguments():
    lines = _entries_for("daily_timelapse_job.py")
    assert len(lines) == 2
    assert any(l.startswith("15 9 * * *") and "--start 07:30 --end 09:00" in l for l in lines)
    assert any(l.startswith("15 15 * * *") and "--start 13:00 --end 15:00" in l for l in lines)


def test_rclone_goes_through_wrapper_script():
    (line,) = _entries_for("rclone")
    assert line.startswith("0 3 * * *")
    assert "tools/rclone_sync_docs.sh" in line
    lines = (REPO_ROOT / "MY_HOME_SYSTEM" / "tools" / "rclone_sync_docs.sh").read_text(encoding="utf-8").splitlines()
    wrapper = "\n".join(l for l in lines if not l.strip().startswith("#"))
    # 終了コードを握りつぶさない(`|| true`・出力の破棄をしない)
    assert "|| true" not in wrapper and "/dev/null" not in wrapper
    assert "rclone sync" in wrapper


# --- run_task.sh の挙動 ------------------------------------------------------


def _sandbox(tmp_path: Path) -> Path:
    """run_task.sh のパスを tmp に差し替えたコピーと、偽の venv / notify を用意する。"""
    root = tmp_path / "MY_HOME_SYSTEM"
    (root / "tools").mkdir(parents=True)
    (root / ".venv" / "bin").mkdir(parents=True)
    fake_python = root / ".venv" / "bin" / "python3"
    fake_python.write_text(
        "#!/bin/bash\n"
        'echo "cwd=$(pwd) args=$*"\n'
        'if [[ "$*" == *fail* ]]; then exit 3; fi\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    script = RUN_TASK.read_text(encoding="utf-8")
    script = script.replace("/home/masahiro/develop/MY_HOME_SYSTEM", str(root))
    script = script.replace('DEVELOP_ROOT="/home/masahiro/develop"', f'DEVELOP_ROOT="{tmp_path}"')
    copy = root / "run_task.sh"
    copy.write_text(script, encoding="utf-8")
    copy.chmod(0o755)
    return copy


def _run(copy: Path, *args: str, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(["bash", str(copy), *args], env=env, capture_output=True, text=True)


def test_run_task_default_cwd_and_log_dir(tmp_path):
    copy = _sandbox(tmp_path)
    r = _run(copy, "monitors/x.py", "a")
    assert r.returncode == 0
    log = (tmp_path / "MY_HOME_SYSTEM" / "logs" / "x.log").read_text(encoding="utf-8")
    assert f"cwd={tmp_path / 'MY_HOME_SYSTEM'} args=monitors/x.py a" in log
    assert "Success" in log


def test_run_task_cwd_and_log_dir_override_creates_missing_log_dir(tmp_path):
    copy = _sandbox(tmp_path)
    ddd = tmp_path / "DDD"
    ddd.mkdir()
    r = _run(copy, "newface_monitor.py",
             env_extra={"RUN_TASK_CWD": str(ddd), "RUN_TASK_LOG_DIR": str(ddd / "logs")})
    assert r.returncode == 0
    log = (ddd / "logs" / "newface_monitor.log").read_text(encoding="utf-8")
    assert f"cwd={ddd} args=newface_monitor.py" in log


def test_run_task_runs_sh_with_bash_and_propagates_failure(tmp_path):
    copy = _sandbox(tmp_path)
    tool = tmp_path / "MY_HOME_SYSTEM" / "tools" / "ok.sh"
    tool.write_text("echo from-sh\n", encoding="utf-8")
    assert _run(copy, "tools/ok.sh").returncode == 0
    assert "from-sh" in (tmp_path / "MY_HOME_SYSTEM" / "logs" / "ok.log").read_text(encoding="utf-8")

    bad = tmp_path / "MY_HOME_SYSTEM" / "tools" / "bad.sh"
    bad.write_text("exit 7\n", encoding="utf-8")
    notify_log = tmp_path / "MY_HOME_SYSTEM" / "logs" / "run_task.log"
    r = _run(copy, "tools/bad.sh")
    assert r.returncode == 7
    assert "ERROR: Exit Code 7" in (tmp_path / "MY_HOME_SYSTEM" / "logs" / "bad.log").read_text(encoding="utf-8")
    # 失敗時は notify_task_failure.py(偽 venv 経由)が <script> <exit_code> <log> で呼ばれる
    assert "notify_task_failure.py tools/bad.sh 7" in notify_log.read_text(encoding="utf-8")
