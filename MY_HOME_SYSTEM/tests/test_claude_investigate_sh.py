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
import shutil
import subprocess

import pytest

CLAUDE_INVESTIGATE_SH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "claude_investigate.sh"
)


def _read_script() -> str:
    with open(CLAUDE_INVESTIGATE_SH_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function(script: str, name: str) -> str:
    m = re.search(rf'^{name}\(\) \{{\n.*?\n\}}\n', script, re.DOTALL | re.MULTILINE)
    assert m, f"{name} 関数の定義が見つかりません"
    return m.group(0)


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


@pytest.mark.skipif(shutil.which("bash") is None, reason="bashが必要")
class TestUtf8CharTruncation:
    """#577回帰防止: 異常サマリ・調査結果の切り詰め(SUMMARY_MAX_CHARS・Discord通知の
    先頭1500字)が wc -c/head -c によるバイト単位で行われ、日本語主体の文章では
    マルチバイト文字の途中で切断され不正なUTF-8バイト列が埋め込まれうる問題の
    回帰テスト。"""

    def test_byte_based_truncation_is_no_longer_used(self):
        # コメント中の言及(修正経緯の説明)は許容し、実際に `| head -c`/`| wc -c` と
        # パイプで使われている箇所が無いことだけを検証する。
        script = _read_script()
        code_lines = [ln for ln in script.splitlines() if not ln.strip().startswith("#")]
        code = "\n".join(code_lines)
        assert "| head -c" not in code
        assert re.search(r"\|\s*wc -c\b", code) is None

    def test_truncate_utf8_chars_does_not_split_multibyte_characters(self):
        script = _read_script()
        func_src = _extract_function(script, "truncate_utf8_chars")
        # マルチバイト文字の境界をまたぐ長さの日本語文字列
        text = "ディスク使用率が 95.0% です" * 3

        result = subprocess.run(
            ["bash", "-c", f'{func_src}\ntruncate_utf8_chars "$1"', "_", "10"],
            input=text, capture_output=True, text=True, check=True,
        )

        assert result.stdout == text[:10]
        # 出力は常に妥当なUTF-8であること(マルチバイト文字の途中で切れていない)
        result.stdout.encode("utf-8").decode("utf-8")

    def test_truncate_utf8_chars_leaves_short_text_unchanged(self):
        script = _read_script()
        func_src = _extract_function(script, "truncate_utf8_chars")
        text = "短い文章"

        result = subprocess.run(
            ["bash", "-c", f'{func_src}\ntruncate_utf8_chars "$1"', "_", "4000"],
            input=text, capture_output=True, text=True, check=True,
        )

        assert result.stdout == text

    def test_utf8_char_count_counts_characters_not_bytes(self):
        script = _read_script()
        func_src = _extract_function(script, "utf8_char_count")
        text = "あ" * 100  # UTF-8で3バイト/文字だが100文字

        result = subprocess.run(
            ["bash", "-c", func_src + "\nutf8_char_count"],
            input=text, capture_output=True, text=True, check=True,
        )

        assert result.stdout.strip() == "100"
