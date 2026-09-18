# .github/scripts/test_check_lint_ratchet.py
"""check_lint_ratchet.py の回帰テスト。

多重集合による新規指摘の判定(純粋ロジック)と、tmp_path 上の疑似 git リポジトリで
実際に ruff / bandit を base worktree と head で走らせる結合テストからなる。
ruff / bandit が PATH に無い環境では結合テストをスキップする。
CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent / "check_lint_ratchet.py"
_spec = importlib.util.spec_from_file_location("check_lint_ratchet", SCRIPT_PATH)
module = importlib.util.module_from_spec(_spec)
sys.modules["check_lint_ratchet"] = module
_spec.loader.exec_module(module)  # type: ignore[union-attr]


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "lint-ratchet-test", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "lint-ratchet-test", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    })
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout


class TestNewFindings:
    def test_reports_only_increased_keys(self):
        k1 = ("a.py", "F401", "unused")
        k2 = ("a.py", "E701", "multi")
        base = [(k1, 3), (k2, 10)]
        head = [(k1, 5), (k2, 12), (k2, 20)]  # k1 は行が移動しただけ、k2 は1件増えた
        added = module.new_findings(base, head)
        assert added == [(k2, 20)]

    def test_removed_findings_do_not_count(self):
        k1 = ("a.py", "F401", "unused")
        assert module.new_findings([(k1, 1), (k1, 2)], [(k1, 9)]) == []

    def test_multiple_new_of_same_key_reported_with_highest_rows(self):
        k = ("a.py", "E701", "multi")
        added = module.new_findings([(k, 1)], [(k, 1), (k, 7), (k, 9)])
        assert added == [(k, 7), (k, 9)]


class TestParsers:
    def test_ruff_paths_are_relative_to_target(self, tmp_path):
        payload = ('[{"filename": "%s", "code": "F401", "message": "x imported but unused", '
                   '"location": {"row": 4, "column": 1}}]' % (tmp_path / "MY_HOME_SYSTEM" / "core" / "a.py"))
        out = module.parse_ruff(payload, tmp_path / "MY_HOME_SYSTEM")
        assert out == [(("core/a.py", "F401", "x imported but unused"), 4)]

    def test_bandit_key_includes_severity_and_confidence(self, tmp_path):
        payload = ('{"results": [{"filename": "./core/a.py", "test_id": "B602", "issue_severity": "HIGH", '
                   '"issue_confidence": "HIGH", "issue_text": "shell=True", "line_number": 12}]}')
        out = module.parse_bandit(payload, tmp_path)
        assert out == [(("core/a.py", "B602[HIGH/HIGH]", "shell=True"), 12)]


@pytest.fixture
def pseudo_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "MY_HOME_SYSTEM").mkdir(parents=True)
    _git(repo, "init", "-q")
    # 実リポジトリと同様、E402 だけを無視する設定をルートに置く
    (repo / "ruff.toml").write_text('[lint]\nignore = ["E402"]\n', encoding="utf-8")
    (repo / "MY_HOME_SYSTEM" / "app.py").write_text(
        "import os\n"          # F401 (既存指摘)
        "def f(x):\n"
        "    if x: return 1\n"  # E701 (既存指摘)
        "    return 2\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    return repo


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff が PATH に無い")
class TestRuffIntegration:
    def test_no_new_findings_when_existing_ones_merely_move(self, pseudo_repo, capsys):
        base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
        # 先頭にコメント行を足して既存指摘の行番号をずらす(件数は不変)
        p = pseudo_repo / "MY_HOME_SYSTEM" / "app.py"
        p.write_text("# moved\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
        assert module.main(["--tool", "ruff", "--dir", "MY_HOME_SYSTEM", "--base", base]) == 0
        assert "新規 0件" in capsys.readouterr().out

    def test_new_finding_fails_with_annotation(self, pseudo_repo, capsys):
        base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
        p = pseudo_repo / "MY_HOME_SYSTEM" / "app.py"
        p.write_text(p.read_text(encoding="utf-8") + "import sys\n", encoding="utf-8")  # 新たな F401
        assert module.main(["--tool", "ruff", "--dir", "MY_HOME_SYSTEM", "--base", base]) == 1
        out = capsys.readouterr().out
        assert "::error file=MY_HOME_SYSTEM/app.py,line=5" in out and "F401" in out

    def test_fixing_existing_finding_passes(self, pseudo_repo):
        base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
        p = pseudo_repo / "MY_HOME_SYSTEM" / "app.py"
        p.write_text(p.read_text(encoding="utf-8").replace("import os\n", ""), encoding="utf-8")
        assert module.main(["--tool", "ruff", "--dir", "MY_HOME_SYSTEM", "--base", base]) == 0

    def test_base_worktree_is_cleaned_up(self, pseudo_repo):
        base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
        module.main(["--tool", "ruff", "--dir", "MY_HOME_SYSTEM", "--base", base])
        worktrees = _git(pseudo_repo, "worktree", "list")
        assert "lint-ratchet-base-" not in worktrees

    def test_missing_dir_in_base_treats_all_as_new(self, pseudo_repo):
        base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
        (pseudo_repo / "DDD").mkdir()
        (pseudo_repo / "DDD" / "x.py").write_text("import os\n", encoding="utf-8")
        assert module.main(["--tool", "ruff", "--dir", "DDD", "--base", base]) == 1


@pytest.mark.skipif(shutil.which("bandit") is None, reason="bandit が PATH に無い")
class TestBanditIntegration:
    def test_new_high_severity_finding_fails_and_extra_args_are_passed(self, pseudo_repo, capsys):
        base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
        p = pseudo_repo / "MY_HOME_SYSTEM" / "app.py"
        # 変数由来のコマンドを shell=True で実行 → B602 (HIGH/HIGH)。定数文字列だと LOW 扱いになる
        p.write_text(p.read_text(encoding="utf-8")
                     + "import subprocess\ndef run(cmd):\n    subprocess.call(cmd, shell=True)\n", encoding="utf-8")
        rc = module.main(["--tool", "bandit", "--dir", "MY_HOME_SYSTEM", "--base", base, "--", "-ll", "-ii"])
        assert rc == 1
        assert "B602[HIGH/HIGH]" in capsys.readouterr().out

    def test_low_severity_is_filtered_out_by_tool_args(self, pseudo_repo):
        base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
        p = pseudo_repo / "MY_HOME_SYSTEM" / "app.py"
        p.write_text(p.read_text(encoding="utf-8") + "assert True\n", encoding="utf-8")  # B101 (LOW)
        assert module.main(["--tool", "bandit", "--dir", "MY_HOME_SYSTEM", "--base", base, "--", "-ll", "-ii"]) == 0
