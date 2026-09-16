# .github/scripts/test_check_spec_line_refs.py
"""check_spec_line_refs.py の回帰テストと、リポジトリ全体に対するゲート。

`test_spec_readme_counts.py`（READMEの件数整合）と同じ方針で、仕様書のメタ情報が
実態からずれたらCIで落とす。こちらが見るのは「根拠」の行番号引用で、
`check_spec_drift.py` のコミット日時比較では構造的に検知できない死角にあたる。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
失敗したときは `python3 .github/scripts/check_spec_line_refs.py --fix` を実行し、
差分を確認してからコミットすること。
"""
import importlib.util
import textwrap
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent / "check_spec_line_refs.py"
_spec = importlib.util.spec_from_file_location("check_spec_line_refs", MODULE_PATH)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def test_repository_line_references_are_accurate():
    """リポジトリ全体: 定義を指す行番号引用がすべて実ソースと一致すること。"""
    findings, checked = checker.scan(fix=False)
    assert checked > 0, "引用を1件も検証できていない（スキャン対象の解決に失敗している可能性）"
    assert not findings, (
        "仕様書の行番号引用が実ソースとずれています(%d件):\n%s\n\n"
        "`python3 .github/scripts/check_spec_line_refs.py --fix` で行番号を揃えられます。"
        % (len(findings), "\n".join("  - " + f.describe() for f in findings))
    )


@pytest.fixture()
def fake_repo(tmp_path, monkeypatch):
    """tmp_path 上に「ソース1件 + 対応する仕様書1件」の疑似リポジトリを作る。"""
    (tmp_path / "MY_HOME_SYSTEM").mkdir()
    (tmp_path / "DDD").mkdir()
    (tmp_path / "docs" / "specifications" / "MY_HOME_SYSTEM").mkdir(parents=True)

    src = tmp_path / "MY_HOME_SYSTEM" / "sample.py"
    src.write_text(
        textwrap.dedent(
            '''\
            """モジュールdocstring。"""


            class Greeter:
                def greet(self, name):
                    return "hello " + name


            def farewell(name):
                return "bye " + name
            '''
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(checker, "SPEC_ROOT", tmp_path / "docs" / "specifications")
    return tmp_path


def _write_spec(repo, body):
    path = repo / "docs" / "specifications" / "MY_HOME_SYSTEM" / "sample.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_detects_stale_line_number(fake_repo):
    _write_spec(fake_repo, '* 根拠: 関数定義 (行番号: 42 / 抜粋: "def farewell(name):")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1
    assert findings[0].symbol == "farewell"
    assert findings[0].actual == 9


def test_accepts_correct_line_number(fake_repo):
    _write_spec(fake_repo, '* 根拠: 関数定義 (行番号: 9 / 抜粋: "def farewell(name):")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert findings == []


def test_fix_rewrites_range_to_actual_definition(fake_repo):
    path = _write_spec(fake_repo, '* 根拠: 関数定義 (行番号: 40〜44 / 抜粋: "def farewell(name):")\n')
    checker.scan(fix=True)
    assert "行番号: 9〜10" in path.read_text(encoding="utf-8")
    findings, _ = checker.scan(fix=False)
    assert findings == []


def test_reports_removed_symbol_without_guessing(fake_repo):
    """改名・削除された定義は --fix では直せないため、理由付きで報告されること。"""
    _write_spec(fake_repo, '* 根拠: 関数定義 (行番号: 9 / 抜粋: "def goodbye(name):")\n')
    findings, _ = checker.scan(fix=False)
    assert len(findings) == 1
    assert findings[0].actual is None
    assert "存在しない" in findings[0].reason


def test_uses_heading_to_disambiguate_same_named_methods(fake_repo):
    """同名メソッドは直前の `### クラス名.メソッド名` 見出しで一意化されること。"""
    src = fake_repo / "MY_HOME_SYSTEM" / "sample.py"
    src.write_text(
        textwrap.dedent(
            '''\
            class A:
                def run(self):
                    return 1


            class B:
                def run(self):
                    return 2
            '''
        ),
        encoding="utf-8",
    )
    _write_spec(
        fake_repo,
        "### `B.run`\n\n* 根拠: メソッド定義 (行番号: 2 / 抜粋: \"def run(self):\")\n",
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1
    assert findings[0].actual == 7  # A.run(2行目)ではなく B.run(7行目)と突き合わせる


def test_skips_when_same_name_cannot_be_disambiguated(fake_repo):
    """見出しが無く同名定義が複数ある場合は、誤検知を避けて何も報告しないこと。"""
    src = fake_repo / "MY_HOME_SYSTEM" / "sample.py"
    src.write_text(
        textwrap.dedent(
            '''\
            class A:
                def run(self):
                    return 1


            class B:
                def run(self):
                    return 2
            '''
        ),
        encoding="utf-8",
    )
    _write_spec(fake_repo, '* 根拠: メソッド定義 (行番号: 999 / 抜粋: "def run(self):")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 0
    assert findings == []


def test_ignores_snippets_that_are_not_definitions(fake_repo):
    """定義以外の抜粋(式・文)は位置を機械判定できないため対象外であること。"""
    _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 999 / 抜粋: "return \\"bye \\" + name")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 0
    assert findings == []


def test_handles_parallel_citations_elementwise(fake_repo):
    """`行番号: A, B / 抜粋: "s1", "s2"` は位置対応でそれぞれ検証・修正されること。"""
    path = _write_spec(
        fake_repo,
        '* 根拠: 定義 (行番号: 99, 98 / 抜粋: "def greet(self, name):", "def farewell(name):")\n',
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 2
    assert len(findings) == 2
    checker.scan(fix=True)
    assert "行番号: 5, 9" in path.read_text(encoding="utf-8")
