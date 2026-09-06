# MY_HOME_SYSTEM/tests/test_claude_investigate_sh.py
"""
scripts/claude_investigate.sh の静的な内容検証。

Issue #380: CLAUDE_INVESTIGATE_PROJECT_DIR の既定値が
/home/masahiro/develop/my_python_project であり、tools/connect_speaker.sh等・
全systemdユニット・start_all.shが使う実機パス(/home/masahiro/develop/MY_HOME_SYSTEM)
と食い違っていたため、cd失敗+set -eで層2調査が無言で一度も動かない不具合があった。
test_start_all_sh.py と同じテキストベース検証の手法で、(1) 既定値が他ファイルと
一致していること、(2) パス不一致を無言のcd失敗に任せず明示的にチェックしている
ことを回帰防止する。
"""
import os
import re

CLAUDE_INVESTIGATE_SH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "claude_investigate.sh"
)


def _read_script() -> str:
    with open(CLAUDE_INVESTIGATE_SH_PATH, "r", encoding="utf-8") as f:
        return f.read()


class TestClaudeInvestigateShProjectDirDefault:
    def test_default_project_dir_matches_other_deploy_scripts(self):
        script = _read_script()
        m = re.search(r'PROJECT_DIR="\$\{CLAUDE_INVESTIGATE_PROJECT_DIR:-([^}]+)\}"', script)
        assert m, "PROJECT_DIR のデフォルト値定義が見つかりません"
        default_dir = m.group(1)
        # tools/connect_speaker.sh・全systemdユニット・start_all.sh はいずれも
        # /home/masahiro/develop/MY_HOME_SYSTEM を実機パスとして使っている。
        # このスクリプトは PROJECT_DIR + "/MY_HOME_SYSTEM" で HOME_SYSTEM_DIR を
        # 組み立てるため、PROJECT_DIR 自体は "develop" 直下(MY_HOME_SYSTEMの親)であるべき。
        assert default_dir == "/home/masahiro/develop"
        assert "my_python_project" not in default_dir

    def test_home_system_dir_existence_is_checked_explicitly_before_cd(self):
        """パス不一致を set -e 経由の無言の cd 失敗に任せず、明示的にチェックして
        エラーメッセージを出すこと。"""
        script = _read_script()
        check_idx = script.index('if [ ! -d "$HOME_SYSTEM_DIR" ]')
        cd_idx = script.index('cd "$HOME_SYSTEM_DIR"')
        assert check_idx < cd_idx, "存在チェックは cd より前に置かれていること"

        check_section = script[check_idx:cd_idx]
        assert "exit 1" in check_section
        assert ">&2" in check_section, "エラーメッセージは標準エラーへ出力すること"
