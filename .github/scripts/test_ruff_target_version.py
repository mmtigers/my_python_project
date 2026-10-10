# .github/scripts/test_ruff_target_version.py
"""CIのruff構文チェック(E9)が、実機・CIのPython版を target-version に渡していることの検証。

背景(PR #933): 手元の開発環境がPython 3.13、CI・実機が3.11という差で、3.12以降でしか通らない
構文(f-stringの式の中のバックスラッシュ)を書いてしまい、CIがimport時のSyntaxErrorで落ちた
(実機に入っていたらサーバーが起動しなかった)。ruff は target-version が未指定だとこれを検知しない
ため、構文チェックに `MY_HOME_SYSTEM/.python-version` の版を渡す。このテストは、その設定が外れたり、
版の求め方が壊れたりしていないことを確かめる。
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
PYTHON_VERSION_FILE = REPO_ROOT / "MY_HOME_SYSTEM" / ".python-version"
GATE = "ruff check . --select F821,F822,F823,E9"


def _target_version() -> str:
    """test.yml の「Resolve ruff target version」ステップと同じ求め方(`3.11` → `py311`)。"""
    major_minor = ".".join(PYTHON_VERSION_FILE.read_text().strip().split(".")[:2])
    return "py" + major_minor.replace(".", "")


def test_python_version_file_gives_a_valid_ruff_target():
    assert re.fullmatch(r"py3\d{1,2}", _target_version()), _target_version()


def test_workflow_resolves_the_target_from_the_python_version_file():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "RUFF_TARGET_VERSION=" in text
    assert "MY_HOME_SYSTEM/.python-version" in text.split("RUFF_TARGET_VERSION=")[0].split("Resolve ruff target version")[-1]


def test_every_blocking_ruff_step_passes_the_target_version():
    """MY_HOME_SYSTEM と DDD の両方の構文チェックに --target-version が付いていること。"""
    gates = [line.strip() for line in WORKFLOW.read_text(encoding="utf-8").splitlines() if GATE in line and "run:" in line]
    assert len(gates) == 2, f"ブロッキングのruffステップが2つ(MY_HOME_SYSTEM・DDD)でなくなっています: {gates}"
    for line in gates:
        assert '--target-version "$RUFF_TARGET_VERSION"' in line, line


def test_the_target_version_step_runs_before_the_gates():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.index("Resolve ruff target version") < text.index(GATE)


def test_ruff_flags_syntax_newer_than_the_target(tmp_path):
    """実際にruffが、対象より新しい構文(f-stringの式中のバックスラッシュ)を構文エラーにすること。"""
    target = _target_version()
    if int(target[3:]) >= 12:
        pytest.skip(f"対象が{target}(3.12以降)のため、この構文は有効。前提が変わったらテストの題材を見直すこと")
    source = tmp_path / "newer_syntax.py"
    source.write_text("flag = True\ntext = f'{\" class=\\\"x\\\"\" if flag else \"\"}'\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "E9", "--target-version", target,
         "--no-cache", "--isolated", str(source)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "invalid-syntax" in result.stdout
