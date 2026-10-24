"""deploy/claude-skills/install.sh の配布・ずれ検知(--check)の回帰テスト (#1021)。

正本は deploy/claude-skills/(git管理)。~/.claude/skills はその配布先で、同期漏れを
--check が検知する。CLAUDE_SKILLS_DIR で配布先を一時ディレクトリへ向けて検証する。
"""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "deploy" / "claude-skills" / "install.sh"
SKILLS = sorted(p.name for p in INSTALL.parent.iterdir() if p.is_dir())


def _sh(dest, *args, force=False):
    env = {**os.environ, "CLAUDE_SKILLS_DIR": str(dest)}
    if force:
        env["FORCE"] = "1"
    return subprocess.run(["bash", str(INSTALL), *args], capture_output=True, text=True, env=env)


def test_repo_has_the_expected_skills():
    assert SKILLS == ["issue-fix", "repo-survey", "verify-handoff"]


def test_install_then_check_is_clean(tmp_path):
    assert _sh(tmp_path / "skills").returncode == 0
    r = _sh(tmp_path / "skills", "--check")
    assert r.returncode == 0 and r.stdout.count("up-to-date") == len(SKILLS)


def test_check_reports_missing_and_only_drift_ignores_it(tmp_path):
    dest = tmp_path / "skills"
    r = _sh(dest, "--check")
    assert r.returncode == 1 and r.stderr.count("MISSING") == len(SKILLS)
    quiet = _sh(dest, "--check", "--only-drift")
    assert quiet.returncode == 0 and quiet.stdout == "" and quiet.stderr == ""
    assert not dest.exists()  # --check は何も作らない


def test_check_detects_drift_in_either_direction_and_changes_nothing(tmp_path):
    dest = tmp_path / "skills"
    _sh(dest)
    edited = dest / "verify-handoff" / "SKILL.md"
    before = edited.read_text(encoding="utf-8")
    edited.write_text(before + "\n配布先だけ編集\n", encoding="utf-8")
    r = _sh(dest, "--check", "--only-drift")
    assert r.returncode == 1 and "DRIFT: verify-handoff" in r.stderr
    assert "DRIFT: issue-fix" not in r.stderr
    assert edited.read_text(encoding="utf-8").endswith("配布先だけ編集\n")  # 書き換えない


def test_install_does_not_overwrite_a_differing_skill_without_force(tmp_path):
    dest = tmp_path / "skills"
    _sh(dest)
    edited = dest / "repo-survey" / "SKILL.md"
    edited.write_text("local edit", encoding="utf-8")
    r = _sh(dest)
    assert r.returncode == 1 and "SKIP(differs): repo-survey" in r.stderr
    assert edited.read_text(encoding="utf-8") == "local edit"
    assert _sh(dest, force=True).returncode == 0  # FORCE=1 で正本に戻る
    assert _sh(dest, "--check").returncode == 0


def test_unknown_argument_is_rejected(tmp_path):
    r = _sh(tmp_path / "skills", "--bogus")
    assert r.returncode == 2 and "usage" in r.stderr


def test_session_start_hook_runs_the_drift_check():
    """同期漏れの検知がフックから消えていないこと(CI は ~/.claude を見られないため、この経路が唯一の自動検知)。"""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]
    assert any("install.sh --check --only-drift" in c for c in commands)
