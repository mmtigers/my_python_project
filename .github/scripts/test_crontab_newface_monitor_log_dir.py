# .github/scripts/test_crontab_newface_monitor_log_dir.py
"""Issue #587の回帰テスト。

deploy/cron/crontab の newface_monitor.py 起動エントリは、標準出力/標準エラーを
DDD/logs/newface_monitor.log へリダイレクトするが、DDD/logs はリポジトリ管理外
(.gitignore対象)で、他のcronエントリ(run_task.sh経由。run_task.sh自体は
ディレクトリを作らないが、起動時にstart_all.shがMY_HOME_SYSTEM/logsを
mkdir -pしている)と異なりこのディレクトリを作成する手段がどこにも無かった。
存在しない場合はシェルのリダイレクト自体が失敗し、Pythonプロセスが一切
起動しない(cronはこの失敗を検知できず無言で失敗する)。

本テストは、newface_monitor.py のcronエントリが実行のたびに対象ログ
ディレクトリを`mkdir -p`してから起動するようになっていることを固定する。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CRONTAB = REPO_ROOT / "deploy" / "cron" / "crontab"


def _crontab_lines():
    return [
        line for line in CRONTAB.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _newface_monitor_line() -> str:
    matches = [line for line in _crontab_lines() if "newface_monitor.py" in line]
    assert len(matches) == 1, (
        f"newface_monitor.py を起動するcronエントリが1件のはずですが{len(matches)}件見つかりました: {matches}"
    )
    return matches[0]


def test_newface_monitor_entry_creates_log_dir_before_redirecting():
    line = _newface_monitor_line()
    # リダイレクト先ディレクトリ(DDD/logs)を、リダイレクトより前にmkdir -pしていること
    mkdir_match = re.search(r"mkdir -p (\S+/logs)\b", line)
    assert mkdir_match, f"newface_monitor.pyのcronエントリに 'mkdir -p .../logs' が見つかりません: {line}"

    redirect_match = re.search(r">>\s*(\S+)\s+2>&1", line)
    assert redirect_match, f"標準出力/エラーのリダイレクト先が見つかりません: {line}"

    log_dir = mkdir_match.group(1)
    redirect_target = redirect_match.group(1)
    assert redirect_target.startswith(log_dir + "/"), (
        f"mkdir -pの対象({log_dir})がリダイレクト先({redirect_target})の親ディレクトリではありません"
    )
    assert line.index("mkdir -p") < line.index(">>"), "mkdir -pはリダイレクトより前に実行されること"


def test_newface_monitor_entry_still_runs_hourly():
    """回帰防止: 修正時に誤って実行頻度を変えていないこと。"""
    line = _newface_monitor_line()
    assert line.strip().startswith("0 * * * *"), f"1時間毎(0 * * * *)の実行のはずです: {line}"
