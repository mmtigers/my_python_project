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


class TestClaudeBinaryResolution:
    """Issue #339: systemd 配下の PATH に ~/.local/bin が無く、exit=127 で自動調査が
    一度も動いていなかった。PATH に頼らず claude を見つけられること。"""

    def _run(self, tmp_path, env_extra):
        func_src = _extract_function(_read_script(), "resolve_claude_bin")
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": str(tmp_path)}
        env.update(env_extra)
        return subprocess.run(
            ["bash", "-c", func_src + "\nresolve_claude_bin"],
            capture_output=True, text=True, env=env, check=False,
        )

    def test_finds_claude_in_home_local_bin_without_path(self, tmp_path):
        bin_dir = tmp_path / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        claude = bin_dir / "claude"
        claude.write_text("#!/bin/sh\n")
        claude.chmod(0o755)

        result = self._run(tmp_path, {})

        assert result.returncode == 0
        assert result.stdout.strip() == str(claude)

    def test_explicit_claude_bin_wins(self, tmp_path):
        result = self._run(tmp_path, {"CLAUDE_BIN": "/opt/claude/bin/claude"})
        assert result.stdout.strip() == "/opt/claude/bin/claude"

    def test_fails_when_nowhere_to_be_found(self, tmp_path):
        assert self._run(tmp_path, {}).returncode == 1

    def test_invocation_uses_the_resolved_binary(self):
        script = _read_script()
        assert '"$CLAUDE_CMD" -p "$PROMPT"' in script
        assert re.search(r'timeout[^\n]*\bclaude -p\b', script) is None


class TestAutoIssueDailyCap:
    """Issue #339: 起票モードは公開リポジトリへ無人で書き込むため、1日の件数に上限を設け、
    件数を確認できないときは書かない側(ドライラン)に倒すこと。"""

    def _run(self, tmp_path, gh_body, cap="2"):
        func_src = _extract_function(_read_script(), "auto_issue_cap_reached")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!/bin/sh\n" + gh_body + "\n")
        gh.chmod(0o755)
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "MAX_ISSUES_PER_DAY": cap,
            "AUTO_ISSUE_LABEL": "auto-investigation",
        }
        return subprocess.run(
            ["bash", "-c", func_src + "\nauto_issue_cap_reached"],
            capture_output=True, text=True, env=env, check=False,
        ).returncode

    def test_below_cap_allows_filing(self, tmp_path):
        assert self._run(tmp_path, "echo 1") == 1

    def test_at_cap_switches_to_dry_run(self, tmp_path):
        assert self._run(tmp_path, "echo 2") == 0

    def test_gh_failure_is_treated_as_cap_reached(self, tmp_path):
        assert self._run(tmp_path, "exit 1") == 0

    def test_unparseable_count_is_treated_as_cap_reached(self, tmp_path):
        assert self._run(tmp_path, "echo oops") == 0

    def test_counts_only_today_in_jst_with_the_auto_label(self, tmp_path):
        """gh に渡す検索条件そのものを確認する(ラベルと当日 JST の作成日)。"""
        log = tmp_path / "args"
        self._run(tmp_path, f'printf "%s\\n" "$@" > {log}; echo 0')
        args = log.read_text().splitlines()
        assert "auto-investigation" in args
        assert any(a.startswith("created:>=") for a in args)
        assert "--state" in args and "all" in args

    def test_cap_check_runs_before_the_prompt_is_built(self):
        script = _read_script()
        cap_at = script.index('if [ "$DRY_RUN" != "1" ] && auto_issue_cap_reached; then')
        prompt_at = script.index('if [ "$DRY_RUN" = "1" ]; then')
        assert cap_at < prompt_at


class TestFilingModePromptGuardrails:
    """Issue #339: 公開リポジトリへの起票で、ログ由来の個人情報を書かせないこと、
    重複起票と一時的な状態での起票を避けさせること。"""

    def _filing_block(self) -> str:
        script = _read_script()
        start = script.index('if [ "$DRY_RUN" = "1" ]; then')
        end = script.index("PROMPT=$(cat <<EOF")
        return script[start:end].split("else", 1)[1]

    def test_privacy_instruction_is_present(self):
        block = self._filing_block()
        assert "一般公開" in block
        for word in ("メッセージ本文", "人名", "IPアドレス", "認証情報"):
            assert word in block

    def test_duplicate_check_and_label_are_required(self):
        block = self._filing_block()
        assert "gh issue list --label ${AUTO_ISSUE_LABEL}" in block
        assert "gh issue comment" in block
        assert "gh issue create --label ${AUTO_ISSUE_LABEL}" in block
        for tool in ("Bash(gh issue list*)", "Bash(gh issue view*)", "Bash(gh issue comment*)"):
            assert tool in block

    def test_transient_states_are_not_filed(self):
        block = self._filing_block()
        assert "origin/master より遅れている" in block
        assert "一時的な通信失敗" in block

    def test_no_write_access_beyond_issues_and_draft_prs(self):
        script = _read_script()
        assert "git push" not in script.split("ALLOWED_TOOLS=", 1)[1].split("\n", 1)[0]
        assert "--dangerously-skip-permissions" not in script.replace(
            "--dangerously-skip-permissions は絶対に使わない", ""
        )
