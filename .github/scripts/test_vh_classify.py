"""deploy/claude-skills/verify-handoff/scripts/vh.py の失敗分類(env / fail)の回帰テスト (#1022)。

env は「依存・コマンド未導入=環境起因」。リポジトリ自身のモジュールの import 失敗は fail のままにする。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("vh_under_test", ROOT / "deploy/claude-skills/verify-handoff/scripts/vh.py")
vh = importlib.util.module_from_spec(_spec)
sys.modules["vh_under_test"] = vh
_spec.loader.exec_module(vh)


def _classify(tmp_path, text, cwd="."):
    return vh.classify_failure(text, tmp_path, cwd)


def test_third_party_module_missing_is_env(tmp_path):
    assert _classify(tmp_path, "ModuleNotFoundError: No module named 'yt_dlp'") == "env"


def test_local_package_import_failure_is_fail(tmp_path):
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    assert _classify(tmp_path, "ModuleNotFoundError: No module named 'mypkg.typo'") == "fail"


def test_local_module_file_import_failure_is_fail(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "helper.py").write_text("")
    assert _classify(tmp_path, "ModuleNotFoundError: No module named 'helper.x'", cwd="sub") == "fail"


def test_src_layout_package_is_local(tmp_path):
    (tmp_path / "src" / "mypkg").mkdir(parents=True)
    (tmp_path / "src" / "mypkg" / "__init__.py").write_text("")
    assert _classify(tmp_path, "ModuleNotFoundError: No module named 'mypkg.typo'") == "fail"


def test_mixed_local_and_third_party_is_fail(tmp_path):
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    text = "ModuleNotFoundError: No module named 'yt_dlp'\nModuleNotFoundError: No module named 'mypkg.typo'"
    assert _classify(tmp_path, text) == "fail"


@pytest.mark.parametrize("text", ["sh: 1: foo: not found", "bash: foo: command not found"])
def test_missing_command_is_env(tmp_path, text):
    assert _classify(tmp_path, text) == "env"


def test_unrelated_failure_is_fail(tmp_path):
    assert _classify(tmp_path, "AssertionError: 1 != 2") == "fail"


def test_unparseable_module_error_stays_env(tmp_path):
    assert _classify(tmp_path, "ModuleNotFoundError") == "env"


def test_plain_directory_with_same_name_is_not_local(tmp_path):
    # docs/ のような import 可能でないディレクトリは自コードではない(未導入の外部パッケージ扱い)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("")
    assert _classify(tmp_path, "ModuleNotFoundError: No module named 'docs'") == "env"


def test_unquoted_python_m_form_is_env(tmp_path):
    # `python -m pytest` で pytest 未導入のときの出力(引用符も ModuleNotFoundError も無い)
    assert _classify(tmp_path, "/usr/bin/python: No module named pytest") == "env"
