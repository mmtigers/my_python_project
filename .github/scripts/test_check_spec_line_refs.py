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


def test_ignores_range_citation_with_literal_snippet(fake_repo):
    """範囲引用に式・文の抜粋が付いたものは対象外(抜粋は範囲の先頭とは限らない)。"""
    _write_spec(fake_repo, '* 根拠: 戻り値 (行番号: 998〜999 / 抜粋: "return \\"bye \\" + name")\n')
    findings, checked = checker.scan(fix=False)
    assert checked == 0
    assert findings == []


def test_ignores_short_literal_snippet(fake_repo):
    """8文字未満の短い抜粋は偶然の一致が多いため対象外。"""
    # `Greeter` はソース内で一意だが7文字しかない。
    _write_spec(fake_repo, '* 根拠: クラス名 (行番号: 999 / 抜粋: "Greeter")\n')
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
