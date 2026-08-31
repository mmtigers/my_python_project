# MY_HOME_SYSTEM/tests/test_keep_alive_anker_sh.py
"""
tools/keep_alive_anker.sh のCONNECT_SCRIPTパスの回帰テスト。

M-8-5: CONNECT_SCRIPTが"<deploy_root>/connect_speaker.sh"を指しており、
実際のデプロイ配置("<deploy_root>/tools/connect_speaker.sh",
リポジトリのMY_HOME_SYSTEM/tools/connect_speaker.shに対応)と一致しない。
CONNECT_SCRIPTが指すファイルが存在しないため、切断検知時の再接続が
永久にスキップされる(`if [ -x "$CONNECT_SCRIPT" ]`がfalseのまま)。
"""
import os
import re

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "tools", "keep_alive_anker.sh"
)
REPO_CONNECT_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "tools", "connect_speaker.sh"
)


def _read_script() -> str:
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_connect_script_path_points_under_tools_dir():
    """
    CONNECT_SCRIPTのデプロイ先パスは、LOGFILEと同じデプロイルート配下の
    tools/connect_speaker.sh を指すべき(リポジトリの実際の配置と一致させる)。
    """
    src = _read_script()

    logfile_match = re.search(r'LOGFILE="([^"]+)/logs/[^"/]+"', src)
    connect_match = re.search(r'CONNECT_SCRIPT="([^"]+)"', src)
    assert logfile_match, "LOGFILEの定義が見つからない"
    assert connect_match, "CONNECT_SCRIPTの定義が見つからない"

    deploy_root = logfile_match.group(1)
    connect_script_path = connect_match.group(1)

    assert connect_script_path == f"{deploy_root}/tools/connect_speaker.sh"


def test_connect_speaker_sh_actually_lives_under_tools():
    assert os.path.isfile(REPO_CONNECT_SCRIPT)


class TestLogTimestampFreshness:
    """#249回帰防止: 以前はTIMESTAMPをスクリプト起動時に1回だけ評価しており、
    再接続処理のsleep等を挟んで複数回log()が呼ばれても、記録される時刻は
    常に起動時刻のままだった(全ログ行が同一時刻になり、実際の発生時刻が
    ログから分からなくなっていた)。"""

    def test_no_global_timestamp_computed_once_at_startup(self):
        src = _read_script()
        # トップレベル(関数定義の外)でTIMESTAMPを1回だけ評価する代入が
        # 存在しないこと。
        assert not re.search(r'^TIMESTAMP=\$\(date', src, re.MULTILINE), (
            "TIMESTAMPをトップレベルで一度だけ評価してはならない"
            "(以降のlog()呼び出しが全て同じ古い時刻を記録してしまう)"
        )

    def test_log_function_calls_date_internally(self):
        src = _read_script()
        log_func_match = re.search(r'^log\(\)\s*\{(.*?)^\}', src, re.DOTALL | re.MULTILINE)
        assert log_func_match, "log()関数の定義が見つからない"
        log_body = log_func_match.group(1)

        assert "date" in log_body, (
            "log()関数は呼び出しのたびにdateコマンドで現在時刻を取得するべき"
        )
        # log()の出力行がグローバル変数TIMESTAMPを参照していないこと
        # (関数外で1回だけ評価された古い値を使い回してしまうため)
        assert "$TIMESTAMP" not in log_body
