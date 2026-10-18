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


def test_verifies_unique_literal_snippet(fake_repo):
    """定義以外の抜粋でも、ソース内で一意な単一行の引用は検証されること(AUDIT-019)。"""
    _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 999 / 抜粋: "return \\"bye \\" + name")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1
    assert findings[0].actual == 10
    assert "抜粋の位置" in findings[0].reason


def test_fix_rewrites_unique_literal_snippet(fake_repo):
    """一意な式・文の引用は --fix で実際の行へ追従すること。"""
    path = _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 999 / 抜粋: "return \\"bye \\" + name")\n')
    checker.scan(fix=True)
    assert "行番号: 10" in path.read_text(encoding="utf-8")
    findings, _ = checker.scan(fix=False)
    assert findings == []


def test_ignores_ambiguous_literal_snippet(fake_repo):
    """抜粋がソース内の複数行に一致する場合は、どれを指すか決められないので対象外。"""
    # `return "` は greet(6行目)と farewell(10行目)の両方に現れる。
    _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 999 / 抜粋: "return \\"")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 0
    assert findings == []


def test_range_citation_with_literal_snippet_inside_range_is_accepted(fake_repo):
    """範囲引用に単一行の抜粋が付いたものは、一意な抜粋の行が範囲の中にあれば通ること(Issue #950)。"""
    # `return "bye " + name` は10行目にだけ現れる。範囲 9〜10 はそれを含む(先頭ではなく末尾)。
    _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 9〜10 / 抜粋: "return \\"bye \\" + name")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert findings == []


def test_range_citation_with_literal_snippet_outside_range_is_detected(fake_repo):
    """一意な抜粋の行が引用範囲の外にあれば検出し、--fix では書き換えないこと(先頭とは限らないため)。"""
    path = _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 998〜999 / 抜粋: "return \\"bye \\" + name")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1
    assert findings[0].actual == 10
    assert "引用範囲の外" in findings[0].reason
    checker.scan(fix=True)
    assert "行番号: 998〜999" in path.read_text(encoding="utf-8")


def test_range_citation_snippet_one_line_before_range_is_detected(fake_repo):
    """範囲が1行ずれて抜粋の行を含まなくなった(境界は含む側)場合も検出すること。"""
    _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 11〜12 / 抜粋: "return \\"bye \\" + name")\n')
    findings, _ = checker.scan(fix=False)
    assert len(findings) == 1


def test_range_citation_with_ambiguous_snippet_is_not_gated(fake_repo):
    """範囲引用でも抜粋が一意でなければ範囲に含まれるか決められないので対象外のままにすること。"""
    _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 998〜999 / 抜粋: "return \\"")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 0
    assert findings == []


def test_range_citation_with_short_snippet_is_not_gated(fake_repo):
    """8文字未満の短い抜粋は範囲引用でも対象外のままにすること。"""
    _write_spec(fake_repo, '* 根拠: クラス (行番号: 998〜999 / 抜粋: "Greeter")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 0
    assert findings == []


def test_report_excludes_range_citations_gated_by_containment(fake_repo):
    """範囲内検証でゲートに入った引用を、非ゲートとして二重に数えないこと。"""
    _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 9〜10 / 抜粋: "return \\"bye \\" + name")\n')
    _, _, total_checked = checker.report_non_definition_citations()
    assert total_checked == 0


def test_ignores_short_literal_snippet(fake_repo):
    """8文字未満の短い抜粋は偶然の一致が多いため対象外。"""
    # `Greeter` はソース内で一意だが7文字しかない。
    _write_spec(fake_repo, '* 根拠: クラス名 (行番号: 999 / 抜粋: "Greeter")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 0
    assert findings == []


@pytest.fixture()
def fake_repo_other_languages(tmp_path, monkeypatch):
    """`.sh` と TypeScript のソース + 対応する仕様書を持つ疑似リポジトリ。

    AUDIT-019 の式・文の照合は文字列の一致だけで成立するので、`ast` が使えない
    これらの言語でも同じ規則が使える。以前は Python の仕様書しか走査していなかった。
    """
    (tmp_path / "MY_HOME_SYSTEM").mkdir()
    (tmp_path / "DDD").mkdir()
    (tmp_path / "docs" / "specifications" / "MY_HOME_SYSTEM").mkdir(parents=True)
    (tmp_path / "docs" / "specifications" / "family-quest" / "src" / "lib").mkdir(parents=True)

    (tmp_path / "MY_HOME_SYSTEM" / "sample_script.sh").write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            set -euo pipefail

            NAS_MOUNT_POINT="/mnt/nas"
            echo "starting"
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "family-quest" / "src" / "lib").mkdir(parents=True)
    (tmp_path / "family-quest" / "src" / "lib" / "sample.ts").write_text(
        textwrap.dedent(
            """\
            import { z } from 'zod';

            export const RETRY_LIMIT = 3;

            export function buildUrl(id: string) {
              return `/api/cameras/${id}`;
            }
            """
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(checker, "SPEC_ROOT", tmp_path / "docs" / "specifications")
    return tmp_path


def test_verifies_shell_script_citation(fake_repo_other_languages):
    """`.sh` の仕様書でも式・文の引用を検証すること。"""
    path = (
        fake_repo_other_languages
        / "docs" / "specifications" / "MY_HOME_SYSTEM" / "sample_script.md"
    )
    path.write_text(
        '* 根拠: マウント先 (行番号: 99 / 抜粋: "NAS_MOUNT_POINT=\"/mnt/nas\"")\n',
        encoding="utf-8",
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert findings[0].actual == 4


def test_verifies_typescript_citation(fake_repo_other_languages):
    """TypeScript の仕様書でも式・文の引用を検証すること。"""
    path = (
        fake_repo_other_languages
        / "docs" / "specifications" / "family-quest" / "src" / "lib" / "sample.md"
    )
    path.write_text(
        '* 根拠: 上限 (行番号: 99 / 抜粋: "export const RETRY_LIMIT = 3;")\n',
        encoding="utf-8",
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert findings[0].actual == 3


def test_fix_rewrites_typescript_citation(fake_repo_other_languages):
    path = (
        fake_repo_other_languages
        / "docs" / "specifications" / "family-quest" / "src" / "lib" / "sample.md"
    )
    path.write_text(
        '* 根拠: 上限 (行番号: 99 / 抜粋: "export const RETRY_LIMIT = 3;")\n',
        encoding="utf-8",
    )
    checker.scan(fix=True)
    assert "行番号: 3" in path.read_text(encoding="utf-8")
    findings, _ = checker.scan(fix=False)
    assert findings == []


def test_verifies_multiline_range_citation(fake_repo):
    """抜粋が複数行の範囲引用は、両端が一意なら開始も終端も確定できること(AUDIT-019)。

    サンプルソースでは `return "hello " + name` が6行目、`return "bye " + name` が
    10行目で、どちらもソース内で一意。したがって 6〜10 に確定できる。
    """
    _write_spec(
        fake_repo,
        r'* 根拠: 実装 (行番号: 900〜904 / 抜粋: "return \"hello \" + name\nreturn \"bye \" + name")'
        + "\n",
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1
    assert (findings[0].actual, findings[0].actual_end) == (6, 10)


def test_fix_rewrites_multiline_range_citation(fake_repo):
    path = _write_spec(
        fake_repo,
        r'* 根拠: 実装 (行番号: 900〜904 / 抜粋: "return \"hello \" + name\nreturn \"bye \" + name")'
        + "\n",
    )
    checker.scan(fix=True)
    assert "行番号: 6〜10" in path.read_text(encoding="utf-8")
    findings, _ = checker.scan(fix=False)
    assert findings == []


def test_ignores_multiline_range_wider_than_its_snippet(fake_repo):
    """引用範囲の方が抜粋より広ければ位置を書き換えない(抜粋は範囲内の見本にすぎない)。

    抜粋は6行目と10行目の2行だが、引用は 5〜15(11行)を指している。ここで
    6〜10 へ書き換えると、著者が示した範囲の情報が失われる。範囲内検証(Issue #950)で
    抜粋の先頭行が範囲に含まれることだけは確認されるが、--fix は何も変えない。
    """
    path = _write_spec(
        fake_repo,
        r'* 根拠: 実装 (行番号: 5〜15 / 抜粋: "return \"hello \" + name\nreturn \"bye \" + name")'
        + "\n",
    )
    findings, checked = checker.scan(fix=True)
    assert checked == 1
    assert findings == []
    assert "行番号: 5〜15" in path.read_text(encoding="utf-8")


def test_ignores_multiline_range_when_the_end_is_not_unique(fake_repo):
    """末尾行が一意でなければ終端を決められないので、位置の書き換えはしない。

    `return "` は greet(6行目)と farewell(10行目)の両方に現れる。先頭行は一意なので
    範囲内検証だけが働き、範囲の外を指していれば検出するが、--fix は書き換えない。
    """
    path = _write_spec(
        fake_repo,
        r'* 根拠: 実装 (行番号: 900〜901 / 抜粋: "return \"hello \" + name\nreturn \"")'
        + "\n",
    )
    findings, checked = checker.scan(fix=True)
    assert checked == 1
    assert len(findings) == 1 and "引用範囲の外" in findings[0].reason
    assert "行番号: 900〜901" in path.read_text(encoding="utf-8")


def test_ignores_range_citation_whose_end_precedes_its_start(fake_repo):
    """末尾行が先頭行より前に来る抜粋は、対応が取れていないので位置を書き換えない。"""
    path = _write_spec(
        fake_repo,
        r'* 根拠: 逆順 (行番号: 8〜12 / 抜粋: "return \"bye \" + name\nreturn \"hello \" + name")'
        + "\n",
    )
    findings, checked = checker.scan(fix=True)
    assert checked == 1  # 先頭行(10行目)が範囲に含まれることだけを見る
    assert findings == []
    assert "行番号: 8〜12" in path.read_text(encoding="utf-8")


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


# --- 書式B(抜粋が行番号の手前にある引用)の解析 — Issue #679 ---------------------
#
# 仕様書には根拠の書き方が2通りある。
#
#     書式A: `(行番号: 9 / 抜粋: "def farewell(name):")`      … 抜粋が後ろ
#     書式B: ``` `def farewell(name):` (行番号: 9) ```        … 抜粋が前
#
# 以前は書式Aしか解析しておらず、書式Bの def/class 引用が**無言で**ゲートを
# 素通りしていた(検出も報告もされないので、ずれていてもCIは緑だった)。
# 以下は、書式Bを拾えること／拾ってはいけないものを拾わないことの両方を固定する。


def test_backtick_format_is_verified(fake_repo):
    """書式Bの引用も検証されること(以前は無言で素通りしていた)。"""
    _write_spec(fake_repo, '* 根拠: `def farewell(name):` (行番号: 42)\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1
    assert findings[0].symbol == "farewell"
    assert findings[0].actual == 9


def test_backtick_format_accepts_correct_line(fake_repo):
    _write_spec(fake_repo, '* 根拠: `def farewell(name):` (行番号: 9〜10)\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert findings == []


def test_backtick_format_is_fixable(fake_repo):
    path = _write_spec(fake_repo, '* 根拠: `def farewell(name):` (行番号: 40〜44)\n')
    checker.scan(fix=True)
    assert "行番号: 9〜10" in path.read_text(encoding="utf-8")


def test_backtick_format_handles_parallel_citations_elementwise(fake_repo):
    """`` `def a` と `def b` (行番号: A, B) `` も位置対応で検証されること。

    実例: `dashboard_common.md` の「CSS定数 と `def render_status_card_html(...)` (行番号: 18, 143)」
    """
    path = _write_spec(
        fake_repo,
        '* 根拠: `def greet(self, name):` と `def farewell(name):` (行番号: 99, 98)\n',
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 2
    assert len(findings) == 2
    checker.scan(fix=True)
    assert "行番号: 5, 9" in path.read_text(encoding="utf-8")


def test_backtick_format_does_not_leak_into_the_next_citation(fake_repo):
    """1行に根拠が複数並ぶとき、手前の抜粋を後ろのブロックのものと取り違えないこと。

    2つ目の `行番号: 6` は `return "hello " + name` を指しており、`def greet` の
    定義位置ではない。窓を直前のブロックの終わりまでに限っていないと、ここで
    `def greet` と対応づけて誤検出する。
    """
    _write_spec(
        fake_repo,
        '* 根拠: `def greet(self, name):` (行番号: 5)、戻り値 (行番号: 6)\n',
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 1  # 1つ目だけが検証対象
    assert findings == []


def test_backtick_excerpt_of_format_a_is_not_reused_by_the_next_block(fake_repo):
    """書式Aの抜粋がバッククォートで書かれていても、次のブロックが流用しないこと。

    実例: `config.md` の ``(行番号: 40 / 抜粋: `def verify_...`), [委譲] (行番号: 78〜85 / ...)``
    2つ目の行番号は委譲先の呼び出し箇所であって、`def` の定義位置ではない。
    """
    _write_spec(
        fake_repo,
        '* 根拠: [定義] (行番号: 9 / 抜粋: `def farewell(name):`), [呼び出し] (行番号: 40〜44)\n',
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 0
    assert findings == []


def test_backtick_citation_naming_another_source_file_is_skipped(fake_repo):
    """別ファイルの行番号を明示している引用は検証対象にしないこと。

    実例: ``切り出し先 `def trigger_tv_unlock(...)` (`services/switchbot_service.py`側、行番号: 94〜125)``
    この行番号は仕様書に対応するソースではなく移動先ファイルの行を指すため、
    こちらのソースと突き合わせても意味がない(検証したことにする方が有害)。
    """
    _write_spec(
        fake_repo,
        '* 根拠: 切り出し先 `def farewell(name):` (`MY_HOME_SYSTEM/other.py`側、行番号: 400〜420)\n',
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 0
    assert findings == []


def test_resolves_parent_prefixed_spec_name(fake_repo):
    """Issue #655: `<親dir>_<stem>.md` の曖昧性解消規約でも対応ソースを解決できること。

    以前は素の stem でしか索引していなかったため、`views/dashboard/common.py` に対応する
    `dashboard_common.md` は「候補が1件でない」として無言でスキップされ、
    シグネチャがずれても CI は緑のままだった。
    """
    deep_dir = fake_repo / "MY_HOME_SYSTEM" / "views" / "dashboard"
    deep_dir.mkdir(parents=True)
    (deep_dir / "widget.py").write_text(
        textwrap.dedent(
            '''\
            """ウィジェット描画。"""


            def render_card(title, value):
                return title + value
            '''
        ),
        encoding="utf-8",
    )
    spec = fake_repo / "docs" / "specifications" / "MY_HOME_SYSTEM" / "dashboard_widget.md"
    spec.write_text('* 根拠: 定義 (行番号: 99 / 抜粋: "def render_card(title, value):")\n', encoding="utf-8")

    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1
    assert findings[0].actual == 4


def test_flat_spec_prefers_shallow_source_when_disambiguated_one_exists(fake_repo):
    """同名 stem が2件あっても、深い方が `<親dir>_<stem>.md` を持つならフラット名は浅い方を指すこと。"""
    (fake_repo / "MY_HOME_SYSTEM" / "helper.py").write_text(
        "def shallow_only():\n    return 1\n", encoding="utf-8"
    )
    deep_dir = fake_repo / "MY_HOME_SYSTEM" / "views" / "dashboard"
    deep_dir.mkdir(parents=True)
    (deep_dir / "helper.py").write_text("def deep_only():\n    return 2\n", encoding="utf-8")
    specs = fake_repo / "docs" / "specifications" / "MY_HOME_SYSTEM"
    (specs / "dashboard_helper.md").write_text(
        '* 根拠: 定義 (行番号: 1 / 抜粋: "def deep_only():")\n', encoding="utf-8"
    )
    (specs / "helper.md").write_text(
        '* 根拠: 定義 (行番号: 1 / 抜粋: "def shallow_only():")\n', encoding="utf-8"
    )

    findings, checked = checker.scan(fix=False)
    assert checked == 2
    assert findings == []


def test_skipped_specs_are_reported_not_silently_dropped(fake_repo):
    """対応ソースを一意に決められない仕様書は、無言で捨てずに呼び出し元へ伝えること。"""
    spec = fake_repo / "docs" / "specifications" / "MY_HOME_SYSTEM" / "ghost.md"
    spec.write_text('* 根拠: 定義 (行番号: 1 / 抜粋: "def gone():")\n', encoding="utf-8")

    skipped = []
    checker.scan(fix=False, skipped=skipped)
    assert [p.name for p, _ in skipped] == ["ghost.md"]


def test_retired_specs_are_not_reported_as_skipped(fake_repo):
    """Issue #722: 規約どおり廃止noticeを付けた仕様書は警告しないこと。

    ソースが削除された仕様書は、中身を消さず冒頭に廃止noticeを追記して履歴として
    残す規約(docs/specifications/README.md)。この形で**処理済み**の仕様書に対して
    「対応ソースが見つからない」と警告し続けるのは、#687 で check_spec_drift.py
    について解消したのと同じ種類の恒久的なノイズになる。
    """
    spec = fake_repo / "docs" / "specifications" / "MY_HOME_SYSTEM" / "retired.md"
    spec.write_text(
        "> **⚠️ 廃止: このファイルは 2026-09-20 時点でソース "
        "(`MY_HOME_SYSTEM/retired.py`) が削除されたため廃止されました。**\n"
        "\n"
        '* 根拠: 定義 (行番号: 1 / 抜粋: "def gone():")\n',
        encoding="utf-8",
    )
    # 廃止noticeが無ければ従来どおり警告対象になることも同時に確かめる(対照)
    plain = fake_repo / "docs" / "specifications" / "MY_HOME_SYSTEM" / "ghost.md"
    plain.write_text('* 根拠: 定義 (行番号: 1 / 抜粋: "def gone():")\n', encoding="utf-8")

    skipped = []
    checker.scan(fix=False, skipped=skipped)
    assert [p.name for p, _ in skipped] == ["ghost.md"]


def test_retirement_judgement_is_shared_with_check_spec_drift():
    """判定を複製せず check_spec_drift.py のものを共有していること。

    2箇所に複製すると RETIRED_NOTICE_PREFIX の書式変更時に片方だけ直す事故が起きる
    (docs/specifications/README.md にもその旨が明記されている)。
    """
    # check_spec_line_refs 自身が読み込み時に .github/scripts を sys.path へ足すため、
    # ここでは素の import で同じモジュールオブジェクトが得られる。
    import check_spec_drift

    assert checker.is_retired_doc is check_spec_drift.is_retired_doc
    assert checker.is_retired_doc.__module__ == "check_spec_drift"


def test_report_counts_ungated_citations(fake_repo):
    """--report はゲートに入らない引用を「抜粋の先頭行が引用行±1にあるか」で粗く数えること。

    `return "` は greet と farewell の2箇所に現れるため一意に決まらず、scan() の
    ゲート対象にならない。こうした引用だけがレポートの対象になる。
    """
    _write_spec(
        fake_repo,
        '* 根拠: 実装 (行番号: 9 / 抜粋: "return \\"")\n'
        '* 根拠: 実装 (行番号: 1 / 抜粋: "return \\"")\n',
    )
    per_spec, total_bad, total_checked = checker.report_non_definition_citations()
    assert total_checked == 2
    # 9行目は一致(farewell の return は10行目なので ±1 の窓に入る)、1行目は不一致。
    assert total_bad == 1
    assert per_spec["MY_HOME_SYSTEM/sample.md"] == (1, 2)


def test_report_excludes_citations_that_scan_now_gates(fake_repo):
    """scan() が厳密に検証するようになった引用を、非ゲートとして二重に数えないこと。

    ここを広く除外すると「ゲートにも非ゲートの集計にも現れない引用」が生まれ、
    main() が出す保証範囲の表示が実態より良く見えてしまう。
    """
    _write_spec(fake_repo, '* 根拠: 実装 (行番号: 10 / 抜粋: "return \\"bye \\" + name")\n')
    _, _, total_checked = checker.report_non_definition_citations()
    assert total_checked == 0
    # 一方で scan() 側では数えられている。
    _, checked = checker.scan(fix=False)
    assert checked == 1


def test_is_skipped_source_judges_paths_relative_to_repo_root(tmp_path, monkeypatch):
    """リポジトリ自体がドット始まりのディレクトリ配下にあっても走査対象が消えないこと。

    このリポジトリの Claude Code セッションは `.claude/worktrees/<名前>/` に git worktree を
    作って作業する。`_is_skipped_source` が絶対パスの全要素を見ていた頃は、その配下の
    ソースすべてが「隠しディレクトリ配下」と誤判定されて走査対象が 0 件になり、
    `test_repository_line_references_are_accurate` が「引用を1件も検証できていない」で
    常に落ちていた(CI は non-dotted なパスへ checkout するため露見しなかった)。
    """
    repo_root = tmp_path / ".worktrees" / "wt"
    source = repo_root / "MY_HOME_SYSTEM" / "config.py"
    source.parent.mkdir(parents=True)
    source.write_text("", encoding="utf-8")
    monkeypatch.setattr(checker, "REPO_ROOT", repo_root)

    assert checker._is_skipped_source(source) is False
    # リポジトリ内のドット始まりディレクトリ(.venv 等)は従来どおり対象外
    assert checker._is_skipped_source(repo_root / ".venv" / "lib" / "mod.py") is True
    assert checker._is_skipped_source(repo_root / "MY_HOME_SYSTEM" / "tests" / "test_x.py") is True


def _triage(repo, body):
    _write_spec(repo, body)
    _, total = checker.triage_non_gated_citations()
    return total


def test_triage_range_citation_whose_snippet_is_inside_the_range_is_ok(fake_repo):
    """範囲引用の途中に抜粋がある引用は「引用範囲内に実在」(Issue #950。±1判定だと誤って未検出になる)。"""
    # `return "` は6行目と10行目に現れて一意でないため、範囲内検証(Issue #950)のゲートに入らない。
    total = _triage(fake_repo, '* 根拠: 戻り値 (行番号: 5〜7 / 抜粋: "return \\"")\n')
    assert total == {checker.TRIAGE_OK: 1}


def test_triage_range_citation_whose_unique_snippet_is_elsewhere_is_gated_not_triaged(fake_repo):
    """抜粋が引用範囲の外の1行にだけ実在する範囲引用は、今はゲートが検出するので仕分けの対象外。"""
    total = _triage(fake_repo, '* 根拠: 戻り値 (行番号: 1〜2 / 抜粋: "return \\"bye \\" + name")\n')
    assert total == {}


def test_triage_ambiguous_snippet_outside_the_range_is_multi(fake_repo):
    """抜粋が複数行に実在し、どれも引用範囲の外なら「ずれている・位置不明」。"""
    total = _triage(fake_repo, '* 根拠: 戻り値 (行番号: 1〜2 / 抜粋: "return \\"")\n')
    assert total == {checker.TRIAGE_MULTI: 1}


def test_triage_snippet_missing_from_source(fake_repo):
    """抜粋がソースのどこにも無い(表記ゆれ・要約)引用は「見つからない」。"""
    total = _triage(fake_repo, '* 根拠: 要約 (行番号: 5〜7 / 抜粋: "this text is not in the source")\n')
    assert total == {checker.TRIAGE_MISSING: 1}


# --- シンボル参照(Issue #950) -------------------------------------------------


def test_repository_symbol_references_are_accurate():
    """リポジトリの仕様書の `参照: シンボル / 抜粋: …` が、実ソースのそのシンボルの中にあること。"""
    problems, _ = checker.scan_symbol_references()
    assert not problems, "シンボル参照に問題があります(%d件):\n%s" % (
        len(problems), "\n".join("  - " + p for p in problems)
    )


def test_symbol_reference_accepts_snippet_inside_the_symbol(fake_repo):
    _write_spec(fake_repo, '* 根拠: 戻り値 (参照: farewell / 抜粋: "return \\"bye \\" + name")\n')
    problems, checked = checker.scan_symbol_references()
    assert (problems, checked) == ([], 1)


def test_symbol_reference_accepts_qualified_method_name(fake_repo):
    _write_spec(fake_repo, '* 根拠: 戻り値 (参照: Greeter.greet / 抜粋: "return \\"hello \\" + name")\n')
    assert checker.scan_symbol_references() == ([], 1)


def test_symbol_reference_detects_snippet_outside_the_symbol(fake_repo):
    """抜粋が別の関数の中にある(=参照先が違う)なら問題として報告する。"""
    _write_spec(fake_repo, '* 根拠: 戻り値 (参照: farewell / 抜粋: "return \\"hello \\" + name")\n')
    problems, _ = checker.scan_symbol_references()
    assert len(problems) == 1 and "farewell" in problems[0]


def test_symbol_reference_detects_missing_symbol(fake_repo):
    _write_spec(fake_repo, '* 根拠: 戻り値 (参照: no_such_function / 抜粋: "return \\"bye \\" + name")\n')
    problems, _ = checker.scan_symbol_references()
    assert len(problems) == 1 and "no_such_function" in problems[0]


def test_symbol_reference_survives_line_shifts(fake_repo):
    """行が増減しても、同じ関数の中にある限り通る(行番号引用との違い)。"""
    src = fake_repo / "MY_HOME_SYSTEM" / "sample.py"
    src.write_text("# 先頭に行を足してすべての行番号をずらす\n" * 20 + src.read_text(encoding="utf-8"), encoding="utf-8")
    _write_spec(fake_repo, '* 根拠: 戻り値 (参照: farewell / 抜粋: "return \\"bye \\" + name")\n')
    assert checker.scan_symbol_references() == ([], 1)


def test_symbol_reference_beside_line_number_citation_does_not_break_the_gate(fake_repo):
    """同じ行に `行番号:` 引用と `参照:` 引用が並んでも、前者のゲートから外れない。

    以前の境界(次の `行番号:` ブロックまで)だと、前者の抜粋として後ろの `参照:` の抜粋まで拾われ、
    抜粋の数が項の数と合わなくなって、ゲートから黙って外れていた。
    """
    _write_spec(
        fake_repo,
        '* 根拠: [定義] (行番号: 99 / 抜粋: "def farewell(")、[戻り値] (参照: farewell / 抜粋: "return \\"bye \\" + name")\n',
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1 and findings[0].actual == 9


def test_convert_rewrites_stale_citation_when_heading_symbol_contains_the_snippet(fake_repo):
    spec = _write_spec(
        fake_repo,
        "### `farewell`\n\n* 根拠: 戻り値 (行番号: 98〜99 / 抜粋: \"return \\\"bye \\\" + name\")\n",
    )
    converted, skipped = checker.convert_to_symbol_references([spec])
    assert (converted, skipped) == (1, 0)
    assert '(参照: farewell / 抜粋: "return' in spec.read_text(encoding="utf-8")
    assert checker.scan_symbol_references()[0] == []


def test_convert_leaves_citation_that_is_not_verifiable(fake_repo):
    """見出しのシンボルの中に抜粋が無いもの(別の関数を引いている)は触らない。"""
    body = "### `farewell`\n\n* 根拠: 戻り値 (行番号: 98〜99 / 抜粋: \"return \\\"hello \\\" + name\")\n"
    spec = _write_spec(fake_repo, body)
    converted, skipped = checker.convert_to_symbol_references([spec])
    assert (converted, skipped) == (0, 1)
    assert spec.read_text(encoding="utf-8") == body


def test_convert_leaves_gated_single_line_citation(fake_repo):
    """単一行で抜粋が一意な引用はゲート(--fix が行番号を直す)の対象なので、変換しない。"""
    body = "### `farewell`\n\n* 根拠: 戻り値 (行番号: 99 / 抜粋: \"return \\\"bye \\\" + name\")\n"
    spec = _write_spec(fake_repo, body)
    assert checker.convert_to_symbol_references([spec]) == (0, 0)
    assert spec.read_text(encoding="utf-8") == body


def test_convert_leaves_citation_that_is_already_correct(fake_repo):
    body = "### `farewell`\n\n* 根拠: 戻り値 (行番号: 9〜10 / 抜粋: \"return \\\"bye \\\" + name\")\n"
    spec = _write_spec(fake_repo, body)
    assert checker.convert_to_symbol_references([spec]) == (0, 0)
    assert spec.read_text(encoding="utf-8") == body


def test_convert_uses_enclosing_symbol_when_heading_is_not_a_symbol(fake_repo):
    """見出しがシンボルでない節(`### インポート一覧` 等)でも、抜粋の出現が1つのシンボルの中に限られるなら変換する。"""
    spec = _write_spec(
        fake_repo,
        "### 処理の流れ\n\n* 根拠: 戻り値 (行番号: 98〜99 / 抜粋: \"return \\\"hello \\\" + name\")\n",
    )
    assert checker.convert_to_symbol_references([spec]) == (1, 0)
    assert '(参照: Greeter.greet / 抜粋: "return' in spec.read_text(encoding="utf-8")
    assert checker.scan_symbol_references()[0] == []


def test_convert_does_not_inherit_symbol_from_previous_heading(fake_repo):
    """シンボルを読めない見出しの後では、前の見出しのシンボル(`farewell`)を使わない。"""
    spec = _write_spec(
        fake_repo,
        "### `farewell`\n\n### 処理の流れ\n\n* 根拠: 戻り値 (行番号: 98〜99 / 抜粋: \"return \\\"hello \\\" + name\")\n",
    )
    assert checker.convert_to_symbol_references([spec]) == (1, 0)
    assert "参照: Greeter.greet" in spec.read_text(encoding="utf-8")


def test_convert_reads_heading_with_kind_prefix(fake_repo):
    spec = _write_spec(
        fake_repo,
        "### 関数 `farewell`\n\n* 根拠: 戻り値 (行番号: 98〜99 / 抜粋: \"return \\\"bye \\\" + name\")\n",
    )
    assert checker.convert_to_symbol_references([spec]) == (1, 0)
    assert "参照: farewell" in spec.read_text(encoding="utf-8")


def test_convert_leaves_snippet_found_in_several_symbols(fake_repo):
    """抜粋が複数のシンボルに現れる(どれを指すか決められない)なら触らない。"""
    body = '### 処理の流れ\n\n* 根拠: 連結 (行番号: 98〜99 / 抜粋: "\\" + name")\n'
    spec = _write_spec(fake_repo, body)
    assert checker.convert_to_symbol_references([spec]) == (0, 1)
    assert spec.read_text(encoding="utf-8") == body


def test_convert_does_not_rewrite_file_without_conversions(fake_repo):
    """変換が0件なら書き戻さない(CRLF の改行を変えない)。"""
    spec = _write_spec(fake_repo, "")
    spec.write_bytes("### `farewell`\r\n\r\n* 根拠: x (行番号: 9〜10 / 抜粋: \"return \\\"bye \\\" + name\")\r\n".encode("utf-8"))
    before = spec.read_bytes()
    assert checker.convert_to_symbol_references([spec]) == (0, 0)
    assert spec.read_bytes() == before


def test_convert_does_not_inherit_symbol_across_h2_heading(fake_repo):
    """`## ` 見出しをはさんだ後でも、前の `### ` 見出しのシンボルを引き継がない。"""
    spec = _write_spec(
        fake_repo,
        "### `farewell`\n\n## 補足\n\n* 根拠: 戻り値 (行番号: 98〜99 / 抜粋: \"return \\\"hello \\\" + name\")\n",
    )
    assert checker.convert_to_symbol_references([spec]) == (1, 0)
    assert "参照: Greeter.greet" in spec.read_text(encoding="utf-8")


# --- 抜粋の正規化(Issue #950) -------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # 仕様書ではソースのバックスラッシュを2つで書く
        (r're.compile(r"(\\d+)/x")', r're.compile(r"(\d+)/x")'),
        (r'say \"hi\"', 'say "hi"'),
        # Markdown の表でエスケープしたパイプ
        (r'pactl list sinks short \| grep -q', "pactl list sinks short | grep -q"),
        # 行全体を囲むバッククォート
        ("`import sys`", "import sys"),
        # 省略記号から後ろは落とす(先頭部分で照合する)
        ("from core.logger import setup...", "from core.logger import setup"),
        ("_RE = re.compile(…)", "_RE = re.compile("),
        # Markdown の強調記法に食われたダンダー名
        ("def **init**(self)", "def __init__(self)"),
        ("logger = setup_logging(**name**)", "logger = setup_logging(__name__)"),
        # 文字列リテラル中の Markdown 強調(日本語)は戻さない
        ('title = "🚨 **重大エラー発生**"', 'title = "🚨 **重大エラー発生**"'),
    ],
)
def test_normalize_snippet_line(raw, expected):
    assert checker._normalize_snippet_line(raw) == expected


def test_escaped_backslash_snippet_is_gated(fake_repo):
    """正規表現のように `\\\\` で書いた抜粋も、ソースの `\\` と照合してゲートに入る。"""
    src = fake_repo / "MY_HOME_SYSTEM" / "sample.py"
    src.write_text(src.read_text(encoding="utf-8") + '\n\nPATTERN = r"\\d+-\\w+"\n', encoding="utf-8")
    _write_spec(fake_repo, '* 根拠: 定義 (行番号: 1 / 抜粋: "PATTERN = r\\"\\\\d+-\\\\w+\\"")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1 and findings[0].actual == 13


def test_symbol_reference_includes_decorator_lines(fake_repo):
    """デコレータの行(`@staticmethod` 等)も、そのシンボルの中として扱う。"""
    src = fake_repo / "MY_HOME_SYSTEM" / "sample.py"
    src.write_text(
        src.read_text(encoding="utf-8") + "\n\nclass Tool:\n    @staticmethod\n    def run(x):\n        return x\n",
        encoding="utf-8",
    )
    _write_spec(fake_repo, '* 根拠: 静的メソッド (参照: Tool.run / 抜粋: "@staticmethod")\n')
    assert checker.scan_symbol_references() == ([], 1)


def test_convert_uses_following_lines_to_disambiguate(fake_repo):
    """先頭行が複数のシンボルに現れても、複数行の抜粋全体が1箇所に決まれば変換する。"""
    src = fake_repo / "MY_HOME_SYSTEM" / "sample.py"
    src.write_text(
        src.read_text(encoding="utf-8")
        + "\n\ndef first():\n    try:\n        pass\n    except Exception:\n        return 1\n"
        + "\n\ndef second():\n    try:\n        pass\n    except Exception:\n        return 2\n",
        encoding="utf-8",
    )
    spec = _write_spec(
        fake_repo,
        "### 処理の流れ\n\n* 根拠: 例外 (行番号: 90〜91 / 抜粋: \"except Exception:\\n        return 2\")\n",
    )
    assert checker.convert_to_symbol_references([spec]) == (1, 0)
    assert "参照: second" in spec.read_text(encoding="utf-8")


def test_citation_naming_another_file_is_not_checked(fake_repo):
    """括弧の中で別ファイルを名指しした `行番号:` は、そのファイルの行なので照合しない(誤検知しない)。"""
    _write_spec(fake_repo, '* 根拠: テスト側 (`test_sample.py` 行番号: 40 / 抜粋: "def farewell(name):")\n')
    assert checker.scan(fix=False) == ([], 0)


def test_citation_naming_its_own_source_is_still_checked(fake_repo):
    _write_spec(fake_repo, '* 根拠: 定義 (`sample.py` 行番号: 40 / 抜粋: "def farewell(name):")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 1 and len(findings) == 1


def test_excerpt_ends_at_its_own_closing_paren(fake_repo):
    """1行に引用が並ぶとき(`(行番号: 9 / 抜粋: "a"), `b` (行番号: 99)`)、手前の抜粋は自分の `")` で閉じる。"""
    _write_spec(
        fake_repo,
        '* 根拠: [戻り値] (行番号: 99 / 抜粋: "return \\"bye \\" + name"), `farewell` (行番号: 9)\n',
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1 and findings[0].actual == 10


def test_citation_with_file_named_just_before_paren_is_not_checked(fake_repo):
    """`` `routers/other.py` (行番号: 42 / 抜粋: …) `` も別ファイルの行として照合しない。"""
    _write_spec(fake_repo, '* 根拠: 呼び出し側 `routers/other.py` (行番号: 40 / 抜粋: "def farewell(name):")\n')
    assert checker.scan(fix=False) == ([], 0)


def test_symbol_reference_excerpt_ends_at_its_own_closing_paren(fake_repo):
    """シンボル参照の抜粋も、後ろに並ぶ別の引用(`` `x` (行番号: …) ``)の文字を取り込まない。"""
    _write_spec(
        fake_repo,
        '* 根拠: 戻り値 (参照: farewell / 抜粋: "return \\"bye \\" + name"), `Greeter` (行番号: 4)\n',
    )
    assert checker.scan_symbol_references() == ([], 1)


# --- シェルのシンボル参照(Issue #950) -------------------------------------------

_SHELL_SAMPLE = textwrap.dedent(
    '''\
    #!/bin/bash
    set -eu

    log() {
        echo "[$(date)] $1" >> "$LOG_FILE"
    }

    function check_mount {
        if ! mountpoint -q "$MOUNT_POINT"; then
            log "NAS未マウント"
            return 1
        fi
    }

    log "start"
    '''
)


def _write_shell(repo, body):
    (repo / "MY_HOME_SYSTEM" / "tool.sh").write_text(_SHELL_SAMPLE, encoding="utf-8")
    path = repo / "docs" / "specifications" / "MY_HOME_SYSTEM" / "tool.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_shell_function_spans():
    assert checker._shell_function_spans(_SHELL_SAMPLE) == [("log", 4, 6), ("check_mount", 8, 13)]


def test_shell_function_spans_drop_duplicates_and_unclosed():
    source = "a() {\n  :\n}\na() {\n  :\n}\nb() {\n  echo\n"
    assert checker._shell_function_spans(source) == []


def test_shell_symbol_reference_is_checked(fake_repo):
    _write_shell(fake_repo, '* 根拠: 未マウント時 (参照: check_mount / 抜粋: "if ! mountpoint -q \\"$MOUNT_POINT\\"; then")\n')
    assert checker.scan_symbol_references() == ([], 1)


def test_shell_symbol_reference_detects_snippet_in_another_function(fake_repo):
    _write_shell(fake_repo, '* 根拠: 未マウント時 (参照: log / 抜粋: "if ! mountpoint -q \\"$MOUNT_POINT\\"; then")\n')
    problems, _ = checker.scan_symbol_references()
    assert len(problems) == 1 and "log" in problems[0]


def test_convert_shell_citation_to_symbol_reference(fake_repo):
    spec = _write_shell(
        fake_repo,
        "### `check_mount`\n\n* 根拠: 未マウント時 (行番号: 30〜40 / 抜粋: \"log \\\"NAS未マウント\\\"\")\n",
    )
    assert checker.convert_to_symbol_references([spec]) == (1, 0)
    assert "参照: check_mount" in spec.read_text(encoding="utf-8")
    assert checker.scan_symbol_references()[0] == []


def test_excerpt_ends_before_bold_note(fake_repo):
    """抜粋の `")` の直後に `**（Issue #NNN）**` の注記が続く場合も、そこで抜粋を閉じる。"""
    _write_spec(
        fake_repo,
        '* 根拠: [戻り値] (行番号: 99 / 抜粋: "return \\"bye \\" + name")、**（Issue #1で変更）** 補足 (行番号: 9)\n',
    )
    findings, checked = checker.scan(fix=False)
    assert checked == 1
    assert len(findings) == 1 and findings[0].actual == 10
