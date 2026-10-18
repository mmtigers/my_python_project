#!/usr/bin/env python3
"""docs/specifications/ の「根拠」行番号引用が、実ソースの定義位置と一致するかを検証する。

## なぜ必要か

各仕様書は `* 根拠: ... (行番号: 44〜87 / 抜粋: "def filter(self, ...")` の形で、
記述の裏付けとなるソースの位置を示す規約になっている。この行番号は、ソース側が
リファクタや行の増減で動いても自動では追従しないため、放置すると「行番号は書いてあるが
その行には別のコードがある」という、読み手を確実に誤誘導する状態になる。

`check_spec_drift.py` はソースと仕様書の**コミット日時の前後関係**しか見ないため、
この種のズレは構造的に検知できない（仕様書を何かの理由で1行でも触ると「追従済み」に
見えてしまう）。2026-09の棚卸しでは、この死角により865件の引用のうち約600件が
実際の定義位置とずれており、`services/quest_service.py:412-458` のように
Issue #550 の分割後は存在しない行範囲を指すものまで残っていた。

## 何を検証するか

「行番号がどこを指しているか機械的に確定できる」引用だけを対象に、`行番号:` の値が
実ソースの位置と一致するかを見る。確定できる引用は次の2種類。

1. 抜粋が `def X(` / `class X` で始まる引用（＝その定義の開始行を指すことが確実）
2. 抜粋が定義で始まらない引用（式・文）のうち、位置が一意に決まるもの（AUDIT-019）
   - **単一行の引用** かつ **抜粋の先頭行がソース内でちょうど1行にだけ現れる**
   - **範囲引用** かつ **抜粋が複数行** かつ **先頭行・末尾行がともに1行にだけ現れ**、
     **抜粋の行数が引用範囲の幅と一致する**
     （この場合は範囲の開始も終端も抜粋の文字列だけで決まる。範囲の方が広い場合は
     抜粋がその中の見本にすぎず、抜粋の位置へ範囲を狭めると著者が示した範囲を失う）

1 は Python のみ（`ast` が要る）。2 は文字列の照合だけなので `MY_HOME_SYSTEM`/`DDD` の
`.py`・`.sh` と、`family-quest` の TypeScript すべてが対象。

2 は当初「位置を機械的に特定できない」として一律に対象外にしていたが、
`import datetime` のようにソース内で一意なリテラルであれば、定義と同じ確実さで
位置が決まる。ゲートの保証範囲が全引用の2割強にとどまり、残りが半数以上ずれたまま
放置されていた（`health_watch.md` は式・文の引用52件すべてがずれていた）ことへの対策。

次のものは引き続き対象外（誤検知を出さない側に倒す）。

- 抜粋がソース内に見つからない／2箇所以上に現れる引用（どの行を指すか決められない）
- 範囲引用（`行番号: 137〜237`）に**単一行**の抜粋が付いたものの**位置の追従**。抜粋は範囲の先頭とは
  限らない（モジュールdocstringの1行目、クラス本体の途中の1行など）ため、先頭行の
  ズレに合わせて終端もずらすと引用の幅そのものが壊れる。抜粋が複数行なら両端が
  決まるので、そちらは対象に含める
  （ただし抜粋の先頭行がソース内で一意なら、その行が引用範囲の**中に**あることだけは
  検証する — `_range_containment`、Issue #950。正しい引用は必ず含むので誤検知にならず、
  範囲がずれて抜粋を含まなくなったことを検知できる。--fix では書き換えない）
- 8文字未満の短い抜粋（`return` 等。偶然の一致が多い）
- 同名の定義が複数あり、直前の `### `クラス名.メソッド名`` 見出しでも一意化できないもの
- `行番号: A, B / 抜粋: "s1", "s2"` のように要素数が一致する並列引用は、位置対応で
  それぞれ検証する

対象は `MY_HOME_SYSTEM/**/*.py` と `DDD/**/*.py`（テスト・`__init__.py` は除く）に
1対1で対応する仕様書のみ。TypeScript側は `def`/`class` の規約が異なるため対象外。

行番号の代わりに「どの関数の中か」を書く `(参照: シンボル / 抜粋: "…")` 書式(Issue #950)は
scan_symbol_references が検証する。こちらは Python に加えてシェル(`.sh`)の関数名も解決する。

## 使い方

    python3 .github/scripts/check_spec_line_refs.py            # 検証(不一致があれば exit 1)
    python3 .github/scripts/check_spec_line_refs.py --fix      # 一致するよう行番号を書き換える

CI では test.yml の lint ジョブから `pytest .github/scripts/` 経由で
`test_check_spec_line_refs.py` が呼び出す。失敗した場合は `--fix` を実行し、
差分を目視確認したうえでコミットすること（`--fix` が触るのは行番号だけなので、
抜粋そのものが古い場合＝定義名が変わった/消えた、コードの書き方が変わった場合は
手で直す必要がある）。
"""
from __future__ import annotations

import argparse
import ast
import collections
import re
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple, Union

# Issue #722: 「廃止notice」の判定は check_spec_drift.py が持つものを**共有する**。
# 同じ判定を2箇所に複製すると RETIRED_NOTICE_PREFIX の書式を変えたときに片方だけ
# 直す事故が起きる(docs/specifications/README.md にも「この目印は
# check_spec_drift.py の RETIRED_NOTICE_PREFIX と対になっています。書式を変えるときは
# 両方直してください」と明記されている)。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_spec_drift import is_retired_doc

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = REPO_ROOT / "docs" / "specifications"
SOURCE_ROOTS = ("MY_HOME_SYSTEM", "DDD")

# 「行番号: 12」「行番号: 12〜34」「行番号: 12, 34〜56」を丸ごと捉える
_BLOCK_RE = re.compile(
    r"行番号: ((?:\d+(?:[〜~-]\d+)?)(?:, ?\d+(?:[〜~-]\d+)?)*)"
)
_ITEM_RE = re.compile(r"\d+(?:[〜~-]\d+)?")
_SNIPPET_HEAD_RE = re.compile(r'抜粋: "(?:async )?(?:def|class) (\w+)')
_HEADING_RE = re.compile(r"^### `?([A-Za-z_][\w.]*)")
# 見出しに並ぶ全てのシンボル(`### `a` / `B``)。同名メソッドを持つクラスが並ぶ見出しで、
# どのクラスのメソッドかを決める候補にする(Issue #950)。
_HEADING_SYMBOLS_RE = re.compile(r"`([A-Za-z_][\w.]*)`")
# 書式B(`def foo(...)` (行番号: 28))の抜粋。行番号ブロックの**手前**にある
# バッククォート引用を拾う(Issue #679)。
_BACKTICK_SNIPPET_RE = re.compile(r"`([^`]+)`")
_DEF_SNIPPET_RE = re.compile(r'"(?:async )?(?:def|class) \w+')
# 「別ファイルの行番号」を明示している引用の目印(Issue #679)
_OTHER_SOURCE_RE = re.compile(r"[\w/]+\.(?:py|sh)$")


class Finding(NamedTuple):
    spec: Path
    line_no: int
    symbol: str
    cited: str
    actual: Optional[int]
    reason: str
    # 範囲引用の終端。単一行の引用では None(表示を開始行だけに絞るため)。
    actual_end: Optional[int] = None

    def describe(self) -> str:
        where = "%s:%d" % (self.spec.relative_to(REPO_ROOT), self.line_no)
        if self.actual is None:
            return "%s  `%s` — %s (引用: 行番号: %s)" % (where, self.symbol, self.reason, self.cited)
        actual = (
            "%d〜%d" % (self.actual, self.actual_end)
            if self.actual_end is not None
            else "%d" % self.actual
        )
        return "%s  `%s` — 引用は %s 行目だが実際は %s 行目 (%s)" % (
            where, self.symbol, self.cited, actual, self.reason,
        )


def _definitions(source: str, with_decorators: bool = False) -> Tuple[Optional[Dict], Optional[Dict]]:
    """(クラス名, メソッド名) -> 範囲 と、名前 -> 範囲リスト を返す。

    範囲の開始は `def`/`class` の行(行番号引用のゲートが照合する位置)。`with_decorators=True` のときは
    デコレータの行から始める(シンボル参照用。`@router.get(...)` や `@classmethod` の抜粋も、
    そのシンボルの中にあるものとして扱うため。Issue #950)。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None, None

    def span(node) -> Tuple[int, int]:
        start = node.lineno
        if with_decorators and node.decorator_list:
            start = min([start] + [d.lineno for d in node.decorator_list])
        return (start, node.end_lineno)

    qualified: Dict[Tuple[str, str], Tuple[int, int]] = {}
    flat: Dict[str, List[Tuple[int, int]]] = collections.defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified[(node.name, child.name)] = span(child)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            flat[node.name].append(span(node))
    return qualified, flat


def _is_skipped_source(path: Path) -> bool:
    """仕様書を持たない(規約上の対象外)ソースか。"""
    name = path.stem
    if name == "__init__" or name.startswith("test_") or "tests" in path.parts:
        return True
    # .venv / node_modules / 隠しディレクトリ配下は対象外。
    # 判定は REPO_ROOT からの相対パスで行う: 絶対パスのまま見ると、リポジトリ自体が
    # ドット始まりのディレクトリ配下にある場合(このリポジトリの Claude Code セッションは
    # `.claude/worktrees/<名前>/` に git worktree を作る)に全ソースが「隠しディレクトリ配下」
    # と誤判定され、走査対象が 0 件になってゲートが常に「引用を1件も検証できていない」で
    # 落ちていた(CI は non-dotted なパスへ checkout するため露見しなかった)。
    try:
        parts = path.relative_to(REPO_ROOT).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") or part in ("node_modules", "migrations") for part in parts)


def _iter_sources(suffixes: Tuple[str, ...] = (".py",)) -> Iterable[Path]:
    for root in SOURCE_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in suffixes and not _is_skipped_source(path):
                yield path


def _source_index(suffixes: Tuple[str, ...] = (".py",)) -> Dict[str, List[Path]]:
    """仕様書のベース名 -> 対応しうるソースファイル。

    Issue #655: 以前はソースの素の stem でしか索引しておらず、`check_spec_drift.py` が
    採用する `<親dir>_<stem>.md` の曖昧性解消規約(例: `views/dashboard/common.py` →
    `dashboard_common.md`)を解決できなかった。その結果 `dashboard_common.md` や
    `quest_*.md` など10件が「候補が1件でない」として**無言でスキップ**され、
    たとえば `dashboard_common.md` が現行と違うシグネチャを引用していても CI は緑だった。

    ここでは素の stem に加えて、ソースディレクトリ直下より深いファイルを
    `<親dir>_<stem>` でも索引する。さらに素の stem が複数候補になる場合は、
    `<親dir>_<stem>.md` の仕様書が実在する(=そちらが正)ものを候補から外して一意化する。
    """
    index: Dict[str, List[Path]] = collections.defaultdict(list)
    deep: Dict[str, List[Path]] = collections.defaultdict(list)
    for path in _iter_sources(suffixes):
        rel = path.relative_to(REPO_ROOT)
        index[path.stem].append(path)
        if len(rel.parts) > 2:
            qualified = "%s_%s" % (rel.parts[-2], path.stem)
            index[qualified].append(path)
            deep[path.stem].append(path)

    # 素の stem が衝突する場合、曖昧性解消済み仕様書を持つ深いファイルを候補から外す
    for stem, paths in list(index.items()):
        if len(paths) < 2:
            continue
        narrowed = []
        for path in paths:
            rel = path.relative_to(REPO_ROOT)
            if path in deep.get(stem, []):
                alt = SPEC_ROOT / rel.parts[0] / ("%s_%s.md" % (rel.parts[-2], path.stem))
                if alt.exists():
                    continue
            narrowed.append(path)
        if narrowed:
            index[stem] = narrowed
    return index


# `(参照: Class.method / 抜粋: "…")`。シンボル参照の書式(Issue #950。検証は scan_symbol_references)
_SYMBOL_BLOCK_RE = re.compile(r"参照: ([A-Za-z_][\w.]*) / 抜粋: ")


# 引用の閉じ括弧の直後に次の引用(`` `x` `` や `[種別]`、`**（Issue #NNN）**` の注記)が続く箇所。`(行番号: 1 / 抜粋: "a"), `b` (行番号: 2)` の
# ように1行に引用が並ぶとき、手前の抜粋をここで閉じる(Issue #950: 閉じないと後ろの引用の文字まで抜粋に取り込み、
# 「ソースに見つからない」扱いになっていた)。
_CITATION_END_RE = re.compile(r'"\)(?=(?:、|, ?|，)\s*(?:[`\[]|\*\*))')


def _block_stop(line: str, block, blocks, index: int) -> int:
    """`行番号:` ブロックに属する抜粋の終端(次のブロックの手前)を返す。

    次の `行番号:` ブロックだけでなく、次の `参照: シンボル` ブロックも境界にする(Issue #950)。
    境界にしないと、シンボル参照へ書き換えた引用が同じ行に並んでいるとき、手前の行番号引用の
    抜粋として後ろの `抜粋: "…"` まで拾われ、抜粋の数が項の数と合わなくなってゲートから黙って外れる。
    """
    stop = blocks[index + 1].start() if index + 1 < len(blocks) else len(line)
    symbol = _SYMBOL_BLOCK_RE.search(line, block.end())
    if symbol is not None:
        stop = min(stop, symbol.start())
    closing = _CITATION_END_RE.search(line, block.end(), stop)
    if closing is not None:
        stop = closing.start() + 2  # `")` までを抜粋に含める
    return stop


# `(`other.py` 行番号: 13〜17 / 抜粋: …)` のように、括弧の中で**別ファイル**を名指しした引用の目印
_NAMED_FILE_RE = re.compile(r"`([\w/.-]+\.(?:py|sh|ts|tsx))`")
_FILE_BEFORE_PAREN_RE = re.compile(r"`([\w/.-]+\.(?:py|sh|ts|tsx))`\s*$")


def _names_other_file(line: str, block, source: Optional[Path]) -> bool:
    """この `行番号:` ブロックが、仕様書に対応するソース以外のファイルの行を指しているか。

    書式Aの引用でも `(`test_foo.py` 行番号: 13〜17 / 抜粋: …)` のように括弧の中で別ファイルを
    名指しすることがある(書式Bの同種は _backtick_snippets が除外する。Issue #679)。その行番号は
    対応ソースの行ではないので、照合すると「ずれている」と誤検知する(Issue #950)。名指しされた
    ファイルが対応ソース自身なら、通常どおり検証する。
    """
    paren = line.rfind("(", 0, block.start())
    if paren == -1:
        return False
    named = _NAMED_FILE_RE.findall(line[paren:block.start()])
    # 括弧の**直前**にファイル名だけが置かれた形(`` `routers/x.py` (行番号: 42) ``)も見る。
    # 直前に限るのは、表の別の列や前の文で触れたファイル名を拾わないため。
    before = _FILE_BEFORE_PAREN_RE.search(line, 0, paren)
    if before is not None:
        named.append(before.group(1))
    if not named:
        return False
    own = source.name if source is not None else None
    return any(Path(name).name != own for name in named)


def _split_snippets(rest: str) -> List[str]:
    """`抜粋: "a", "b"` の抜粋部分をスニペット単位に分割する(書式A)。"""
    match = re.search(r'抜粋: (".*)$', rest)
    if not match:
        return []
    return re.split(r'(?<="), ?(?=")', match.group(1))


def _backtick_snippets(before: str) -> List[str]:
    """行番号ブロックの手前にあるバッククォート引用を順に返す(書式B、Issue #679)。

    このリポジトリの仕様書には根拠の書き方が2通りある:

        書式A: `(行番号: 28 / 抜粋: "def foo(")`      … 抜粋が行番号の**後ろ**
        書式B: ``` `def foo(...)` (行番号: 28) ```   … 抜粋が行番号の**前**

    以前は書式Aしか解析しておらず、書式Bの `def`/`class` 引用がゲートを
    無言で素通りしていた。`before` は呼び出し側で「直前の行番号ブロックの終わり
    から、このブロックの始まりまで」に限定して渡すこと。1行に複数の根拠が並ぶ
    場合(`根拠: X (行番号: 1)、Y (行番号: 2)`)に、手前のブロックの抜粋を
    このブロックのものと取り違えないための前提である。

    戻り値は書式Aの `_split_snippets` と同じ「先頭が `"` の文字列」に揃えてあり、
    呼び出し側の記号抽出をそのまま使える。

    2つの除外がある。どちらも「このブロックのものではない抜粋」を拾わないため:

    - 窓に `抜粋:` を含む場合は空を返す。書式Aの抜粋がバッククォートで書かれている
      ことがあり(`抜粋: ``def foo``)、その抜粋が**次の**ブロックの窓に入るため。
    - 最後のバッククォートとブロックの間に `)` がある場合も空を返す。手前の
      括弧つき根拠が閉じており、このブロックとは別の引用だと分かるため。

    加えて、窓の中で**別の `.py` を明示している**引用も空を返す
    (例: ``切り出し先 `def trigger_tv_unlock(...)` (`services/switchbot_service.py`側、行番号: 94〜125)``)。
    この行番号は仕様書に対応するソースではなく、そちらのファイルの行を指すため、
    本チェッカーでは検証できない(検証したことにする方が有害)。
    """
    if "抜粋:" in before:
        return []
    matches = list(_BACKTICK_SNIPPET_RE.finditer(before))
    if not matches or ")" in before[matches[-1].end():]:
        return []
    snippets = [m.group(1) for m in matches]
    if any(_OTHER_SOURCE_RE.search(s) for s in snippets):
        return []
    return ['"%s' % s for s in snippets]


# 式・文の抜粋を照合するときの最小文字数。短い抜粋(`return`、`try:` 等)は
# ソース中に何度も現れて一意に決まらないうえ、偶然の一致で誤った行へ
# 「追従」させる危険があるため、最初から対象にしない(--report と同じ閾値)。
_MIN_LITERAL_HEAD = 8


def _unique_literal_line(head: str, src_lines: List[str]) -> Optional[int]:
    """抜粋の先頭行がソース中でちょうど1行にだけ現れるならその行番号(1始まり)を返す。

    Issue #655 で可視化した「def/class 以外の引用」(全引用の約8割)は、位置を
    機械的に特定できないという理由でゲートからも --fix からも外れていた。
    しかし `import datetime` のように**ソース内で一意なリテラル**であれば、
    定義と同じ確実さで位置を決められる。0件(抜粋自体が古い)や2件以上
    (どちらを指しているか決められない)は、誤った行へ書き換える方が有害なので
    None を返して対象外にする。
    """
    if len(head) < _MIN_LITERAL_HEAD:
        return None
    hits = [i + 1 for i, line in enumerate(src_lines) if head in line]
    return hits[0] if len(hits) == 1 else None


def _literal_head(snippet: str) -> Optional[str]:
    """def/class ではない抜粋から、照合に使う先頭1行を取り出す(def/class なら None)。"""
    if _DEF_SNIPPET_RE.match(snippet):
        return None
    head = _snippet_first_line(snippet)
    return head or None


def _snippet_last_line(snippet: str) -> Optional[str]:
    """複数行の抜粋から末尾1行を取り出す(単一行の抜粋なら None)。

    範囲引用(`行番号: 44〜87`)の**終端**を確定するために使う。抜粋が複数行なら
    「先頭行=範囲の開始」「末尾行=範囲の終端」と読めるので、両方が一意であれば
    範囲全体が抜粋の文字列だけで決まる。
    """
    body = snippet.strip()
    if body.startswith('"'):
        body = body[1:]
    close = body.rfind('"')
    if close != -1:
        body = body[:close]
    parts = _split_snippet_lines(body)
    if len(parts) < 2:
        return None
    return parts[-1] or None


def _literal_target(
    snippet: str, cited: str, src_lines: List[str]
) -> Optional[Tuple[int, Optional[int]]]:
    """式・文の抜粋の引用について、機械的に確定できる実位置を返す。

    返り値は `(開始行, 終端行 or None)`。確定できなければ None(＝非ゲート)。
    **ゲートの判定と `--report` の除外は必ずこの関数を共有すること** — 条件が
    2箇所に分かれると「ゲートにも非ゲートの集計にも現れない引用」が生まれ、
    出力する保証範囲が実態より良く見えてしまう。

    確定できるのは次の2つだけ。

    1. 単一行の引用 かつ 抜粋の先頭行がソース内で一意
    2. 範囲引用 かつ 抜粋が複数行 かつ 先頭行・末尾行がともにソース内で一意で、
       末尾行が先頭行以降にあり、**抜粋の行数が引用範囲の幅と一致する**
       （範囲の方が広ければ、抜粋はその中の見本にすぎないので対象外）

    範囲引用に**単一行の抜粋**が付いたものは対象外。抜粋は範囲の先頭とは限らず
    (モジュールdocstringの1行目、クラス本体の途中の1行など)、先頭行のズレに
    合わせて終端もずらすと引用の幅そのものが壊れるため。
    """
    head = _literal_head(snippet)
    if head is None:
        return None
    if not re.search(r"[〜~-]", cited):
        start = _unique_literal_line(head, src_lines)
        return None if start is None else (start, None)

    tail = _snippet_last_line(snippet)
    if tail is None or len(tail) < _MIN_LITERAL_HEAD:
        return None
    start = _unique_literal_line(head, src_lines)
    end = _unique_literal_line(tail, src_lines)
    if start is None or end is None or end < start:
        return None
    # 抜粋が引用範囲を**ちょうど覆っている**ときだけ対象にする。
    # 範囲の方が広い場合、抜粋はその中の一部を示す見本にすぎず(例: 9行の
    # docstring を指す引用に2行だけ抜粋を載せている)、抜粋の位置へ範囲を
    # 狭めると著者が示した範囲の情報が失われる。行数が一致していれば
    # 「その範囲＝この抜粋」と読めるので、ずれた分だけ安全に追従できる。
    cited_bounds = [int(v) for v in re.findall(r"\d+", cited)]
    if len(cited_bounds) != 2:
        return None
    if (cited_bounds[1] - cited_bounds[0]) != (end - start):
        return None
    return (start, end)


def _range_containment(snippet: str, cited: str, src_lines: List[str]) -> Optional[Tuple[int, int, int]]:
    """範囲引用の抜粋が、引用した範囲の**中に**あるかを調べるための材料を返す(Issue #950)。

    `_literal_target` が確定できない範囲引用(単一行の抜粋が付いたもの、範囲が抜粋より広いもの)も、
    抜粋の先頭行がソース内で一意なら「その行は引用範囲 `開始〜終了` の中になければならない」。
    抜粋が範囲の先頭とは限らないので位置の書き換え先は決まらない(--fix の対象外)が、
    範囲が抜粋の行を含まなくなった＝ずれた、ことは確実に言える。正しい引用は必ず含むので誤検知にならない。
    返り値は `(抜粋の行, 範囲の開始, 範囲の終了)`。範囲引用でない・先頭行が一意でないなら None。
    **ゲートの判定と `--report` の除外は必ずこの関数を共有すること**(`_literal_target` と同じ理由)。
    """
    head = _literal_head(snippet)
    if head is None or not re.search(r"[〜~-]", cited):
        return None
    bounds = [int(v) for v in re.findall(r"\d+", cited)]
    if len(bounds) != 2:
        return None
    line = _unique_literal_line(head, src_lines)
    return None if line is None else (line, bounds[0], bounds[1])


def _definition_pairs(rest, before, snippets, back_snippets, items):
    """根拠ブロックの def/class 引用を (位置, 抜粋) で返し、その位置集合も返す。

    戻り値は `(pairs, def_claimed)`。`def_claimed` は「その位置の行番号は def/class の
    定義位置を指しており、式・文の抜粋として解釈してはいけない」位置の集合。

    書式B(``` `def foo(...)` (行番号: 28 / 抜粋: "x = 1") ```)では、行番号は def の
    位置を指していて、後ろの `抜粋:` はその定義の**中身の一例**にすぎない。これを
    独立した式・文の引用として扱うと、AUDIT-019 の照合がその行番号を抜粋の位置へ
    書き換えてしまい、引用が壊れる(実際に `migrations.md` の `_split_statements` で
    `78〜94` が `93〜93` に書き換わる事故を作り込んだ)。
    """
    if len(snippets) == len(items) and len(items) > 1:
        # 書式Aの並列引用。各位置の抜粋がそのまま行番号に対応するので、
        # def でない位置は式・文の引用として扱ってよい。
        return list(enumerate(snippets)), set()
    if len(back_snippets) == len(items) and len(items) > 1:
        pairs = list(enumerate(back_snippets))
        return pairs, {pos for pos, _ in pairs}
    head = _SNIPPET_HEAD_RE.search(rest)
    if head:
        # 書式A: `抜粋: "def X("`。位置0の抜粋そのものが def なので、
        # 式・文としては解釈されない(_literal_head が None を返す)。
        return [(0, '"def %s(' % head.group(1))], set()
    # 書式B: このブロックに最も近い def/class の抜粋を使う
    defs = [s for s in back_snippets if _DEF_SNIPPET_RE.match(s)]
    if not defs:
        return [], set()
    return [(0, defs[-1])], {0}


def _resolve(
    symbol: str, klass: Union[None, str, Sequence[str]], qualified: Dict, flat: Dict
) -> Optional[Tuple[int, int]]:
    """名前から定義の範囲を引く。同名が複数あるときは `klass`(直前の見出しのクラス。複数でも可)で一意化する。"""
    candidates = flat.get(symbol)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if klass is None:
        return None
    klasses = [klass] if isinstance(klass, str) else list(klass)
    # 見出しに複数のクラスが並ぶときは、その名前のメソッドを持つクラスがちょうど1つのときだけ決められる
    hits = {qualified[(k, symbol)] for k in klasses if (k, symbol) in qualified}
    return hits.pop() if len(hits) == 1 else None


def _spec_expects_python_source(spec: Path) -> bool:
    """この仕様書が Python ソースに1対1対応する規約の対象か。

    family-quest(TypeScript)配下と、`.sh` にしか対応しない仕様書は本チェッカーの
    対象外(ast が使えない)なので、スキップ警告の対象からも外す。
    """
    try:
        rel = spec.relative_to(SPEC_ROOT)
    except ValueError:
        return False
    if not rel.parts or rel.parts[0] not in SOURCE_ROOTS:
        return False
    shell_index = _source_index((".sh",))
    return spec.stem not in shell_index


def scan(fix: bool = False, skipped: Optional[List[Tuple[Path, str]]] = None) -> Tuple[List[Finding], int]:
    """全仕様書を走査する。戻り値は (不一致の一覧, 検証できた引用の総数)。

    `skipped` を渡すと、対応ソースを一意に決められず検証できなかった仕様書を
    (パス, 理由) で追記する(Issue #655: 以前はこれを無言で捨てていた)。
    """
    index = _source_index()
    findings: List[Finding] = []
    checked = 0

    for spec in sorted(SPEC_ROOT.rglob("*.md")):
        if spec.name == "README.md":
            continue
        candidates = index.get(spec.stem)
        if spec.relative_to(SPEC_ROOT).parts[0] in FQ_SPEC_ROOT_PARTS:
            # family-quest の仕様書は TypeScript のソースに対応する。ベース名が同じ Python
            # (`family-quest/src/lib/utils.md` と `core/utils.py` など)を候補にすると、TS の引用を
            # Python のソースと突き合わせてしまい、検証が無言で素通りする(Issue #950)。
            candidates = None
        if not candidates or len(candidates) != 1:
            if skipped is not None and _spec_expects_python_source(spec):
                # Issue #722: 規約どおり廃止noticeを付けて処理済みの仕様書は、
                # 対応ソースが無いのが**正しい状態**なので警告しない
                # (#687 で check_spec_drift.py 側は既に同じ扱いにしてある)。
                if not candidates and is_retired_doc(spec):
                    continue
                reason = "対応ソースが見つからない" if not candidates else (
                    "同名ソースが%d件あり一意に決められない" % len(candidates)
                )
                skipped.append((spec, reason))
            # `.py` として一意に決まらなくても、`.sh` / TypeScript のソースに対応する
            # 仕様書なら「式・文の抜粋」の照合は同じようにできる(一意なリテラルの
            # 照合に言語は関係ない)。def/class の検証だけを諦めて走査を続ける。
            source = _report_source_for_spec(spec)
            if source is None or source.suffix == ".py":
                continue
            source_text = source.read_text(encoding="utf-8")
            qualified, flat = {}, {}
            is_python = False
        else:
            source = candidates[0]
            source_text = candidates[0].read_text(encoding="utf-8")
            qualified, flat = _definitions(source_text)
            if qualified is None:
                continue
            is_python = True
        src_lines = source_text.splitlines()

        lines = spec.read_text(encoding="utf-8").splitlines(keepends=True)
        klass: Optional[List[str]] = None
        changed = False

        for idx, line in enumerate(lines):
            heading = _HEADING_RE.match(line)
            if heading:
                klass = [
                    name.split(".")[0] for name in _HEADING_SYMBOLS_RE.findall(line)
                ] or [heading.group(1).split(".")[0]]

            blocks = list(_BLOCK_RE.finditer(line))
            if not blocks:
                continue
            rewritten = line
            shift = 0
            for b_i, block in enumerate(blocks):
                if _names_other_file(line, block, source):
                    continue
                stop = _block_stop(line, block, blocks, b_i)
                rest = line[block.end():stop]
                # 書式B(Issue #679)の抜粋はブロックの手前にある。直前のブロックの
                # 終わりまでに窓を限ることで、1行に複数の根拠が並ぶときに
                # 前の根拠の抜粋を拾ってしまうのを防ぐ。
                start_of_window = blocks[b_i - 1].end() if b_i else 0
                before = line[start_of_window:block.start()]
                snippets = _split_snippets(rest)
                back_snippets = _backtick_snippets(before)
                items = _ITEM_RE.findall(block.group(1))
                # どの位置が def/class の引用かの判定は _definition_pairs に集約する
                # (式・文の照合と二重に解釈しないため)。
                pairs, def_claimed = _definition_pairs(
                    rest, before, snippets, back_snippets, items
                )

                new_items = list(items)
                touched = False
                # def/class の位置解決は ast が要るので Python のときだけ。
                # TypeScript の `class X` の抜粋も _DEF_SNIPPET_RE には一致するが、
                # 解決手段が無いので「定義が存在しない」と誤報しないよう対象から外す
                # (行番号がその定義を指しているという事実は def_claimed で使う)。
                for pos, snippet in (pairs if is_python else []):
                    sym = re.match(r'"(?:async )?(?:def|class) (\w+)', snippet)
                    if not sym:
                        continue
                    symbol = sym.group(1)
                    resolved = _resolve(symbol, klass, qualified, flat)
                    if resolved is None:
                        if symbol not in flat:
                            findings.append(Finding(
                                spec, idx + 1, symbol, items[pos], None,
                                "この名前の定義がソースに存在しない(改名/削除された可能性)",
                            ))
                        continue
                    start, end = resolved
                    checked += 1
                    cited = items[pos]
                    sep = re.search(r"[〜~-]", cited)
                    cited_start = int(_ITEM_RE.findall(cited)[0].split(sep.group(0))[0]) if sep else int(cited)
                    if cited_start == start:
                        continue
                    findings.append(Finding(
                        spec, idx + 1, symbol, cited, start, "定義位置がずれている",
                    ))
                    new_items[pos] = ("%d%s%d" % (start, sep.group(0), end)) if sep else str(start)
                    touched = True

                # Issue #655 / AUDIT-019: def/class 以外の抜粋(全引用の約8割)は
                # 「位置を機械的に特定できない」という理由でゲートにも --fix にも
                # 入っていなかった。しかし抜粋がソース内で**一意**なら、定義と同じ
                # 確実さで位置が決まる。判定は _literal_target に集約してあり、
                # 「単一行の引用 かつ 先頭行が一意」と「範囲引用 かつ 抜粋が複数行で
                # 先頭行・末尾行がともに一意」の2つだけを対象にする。0件(抜粋自体が
                # 古い)や2件以上(どちらか決められない)、範囲引用に単一行の抜粋が
                # 付いたものは従来どおり対象外(誤った行へ追従させる方が有害なため)。
                if len(snippets) == len(items):
                    for pos, snippet in enumerate(snippets):
                        if pos in def_claimed:
                            continue
                        cited = items[pos]
                        target = _literal_target(snippet, cited, src_lines)
                        if target is None:
                            contained = _range_containment(snippet, cited, src_lines)
                            if contained is None:
                                continue
                            checked += 1
                            line_no, lo, hi = contained
                            if lo <= line_no <= hi:
                                continue
                            findings.append(Finding(
                                spec, idx + 1, _literal_head(snippet) or "", cited, line_no,
                                "抜粋の行が引用範囲の外にある(範囲がずれている)",
                            ))
                            continue
                        start_line, end_line = target
                        checked += 1
                        bounds = [int(v) for v in re.findall(r"\d+", cited)]
                        sep = re.search(r"[〜~-]", cited)
                        if end_line is None:
                            if bounds[0] == start_line:
                                continue
                            replacement = str(start_line)
                        else:
                            if (bounds[0], bounds[1]) == (start_line, end_line):
                                continue
                            replacement = "%d%s%d" % (start_line, sep.group(0), end_line)
                        findings.append(Finding(
                            spec, idx + 1, _literal_head(snippet) or "", cited,
                            start_line, "抜粋の位置がずれている", end_line,
                        ))
                        new_items[pos] = replacement
                        touched = True

                if touched and fix:
                    joined = ", ".join(new_items)
                    s = block.start(1) + shift
                    e = block.end(1) + shift
                    rewritten = rewritten[:s] + joined + rewritten[e:]
                    shift += len(joined) - (block.end(1) - block.start(1))

            if rewritten != line:
                lines[idx] = rewritten
                changed = True

        if changed and fix:
            spec.write_text("".join(lines), encoding="utf-8")

    return findings, checked


# --- Issue #655: def/class 以外の引用の粗いレポート ---

# family-quest(TypeScript)側の仕様書 → ソースの対応。docs/specifications/family-quest/src/**.md
# は family-quest/src/**.{ts,tsx} をミラーし、App.md / main.md だけは src/ を省いた位置にある。
FQ_SPEC_ROOT_PARTS = ("family-quest",)
FQ_SOURCE_ROOT = "family-quest/src"
FQ_SUFFIXES = (".ts", ".tsx")


def _report_source_for_spec(spec: Path) -> Optional[Path]:
    """レポート用に、仕様書へ対応するソースを1件だけ返す(見つからなければ None)。"""
    rel = spec.relative_to(SPEC_ROOT)
    if rel.parts and rel.parts[0] in SOURCE_ROOTS:
        for suffixes in ((".py",), (".sh",)):
            candidates = _source_index(suffixes).get(spec.stem) or []
            if len(candidates) == 1:
                return candidates[0]
        return None
    if rel.parts and rel.parts[0] in FQ_SPEC_ROOT_PARTS:
        sub = rel.relative_to(rel.parts[0])
        if sub.parts and sub.parts[0] == "src":
            sub = sub.relative_to("src")
        for suffix in FQ_SUFFIXES:
            candidate = REPO_ROOT / FQ_SOURCE_ROOT / sub.with_suffix(suffix)
            if candidate.exists():
                return candidate
    return None


# 抜粋の中の行区切り。仕様書では実際の改行を `\n`(バックスラッシュ1つ)で書き、
# ソースコードの中に文字として現れる `\n` は `\\n`(バックスラッシュ2つ)で書く。
# 後者を行区切りと誤認すると、1行の抜粋が複数行に見えてしまう
# (例: `cleaned = \"\\n\".join(...)` が2行の抜粋と判定され、範囲引用を壊した)。
_SNIPPET_NEWLINE_RE = re.compile(r"(?<!\\)\\n")


# 抜粋の中のエスケープ(`\\"` → `"`、`\\\\` → `\\`)。仕様書では正規表現などソースのバックスラッシュを
# `\\\\d` のように2つで書くため、1つへ戻してから照合する(Issue #950: 戻さずに照合していたため、
# 正規表現を含む抜粋はすべて「ソースに見つからない」扱いになっていた)。Markdown の表の中で
# パイプを `\\|` と書いたものも `|` へ戻す(シェルのパイプを含む抜粋が表に入るため)。
_SNIPPET_ESCAPE_RE = re.compile(r'\\(["\\|])')
# Markdown に食われたダンダー名(`__init__` → `**init**`、`__name__` → `**name**`)。抜粋を書き写す際に
# 強調記法として解釈されたもの。ソースの `**` は `2**30` のように単語を挟まないので、戻しても誤一致しない。
# 英字の識別子に限る(`"🚨 **重大エラー発生**"` のような、ソース中の文字列リテラルの強調は戻さない)。
_MANGLED_DUNDER_RE = re.compile(r"\*\*([A-Za-z_][A-Za-z0-9_]*)\*\*")
# 抜粋の省略記号。`from core.logger import setup...` のように途中で切った抜粋は、手前の部分で照合する。
_SNIPPET_ELLIPSIS_RE = re.compile(r"(?:\.\.\.|…)")


def _normalize_snippet_line(part: str) -> str:
    """抜粋の1行を、ソースと照合できる形へ戻す。

    エスケープを戻し、Markdown に食われたダンダー名を戻し、行全体を囲むバッククォート(`` `import sys` ``)を外し、省略記号から後ろを落とす。
    省略記号を落とした結果は抜粋の**先頭部分**なので、`head in line` の照合はそのまま使える。
    """
    part = _SNIPPET_ESCAPE_RE.sub(r"\1", part).strip()
    part = _MANGLED_DUNDER_RE.sub(r"__\1__", part)
    if len(part) >= 2 and part.startswith("`") and part.endswith("`") and part.count("`") == 2:
        part = part[1:-1].strip()
    ellipsis = _SNIPPET_ELLIPSIS_RE.search(part)
    if ellipsis is not None:
        part = part[:ellipsis.start()].rstrip()
    return part


def _split_snippet_lines(body: str) -> List[str]:
    """抜粋の本文を行のリストへ分割し、各行を照合できる形へ戻す(_normalize_snippet_line)。"""
    return [_normalize_snippet_line(part) for part in _SNIPPET_NEWLINE_RE.split(body)]


def _snippet_lines(snippet: str) -> List[str]:
    """引用の抜粋を、照合に使う行のリストにして返す(各行は _normalize_snippet_line 済み)。"""
    body = snippet.strip()
    if body.startswith('"'):
        body = body[1:]
    # 末尾の閉じ引用符より後ろ(`")` や `") |` など、引用を囲む Markdown/表の記号)を落とす
    close = body.rfind('"')
    if close != -1:
        body = body[:close]
    return _split_snippet_lines(body)


def _snippet_first_line(snippet: str) -> str:
    """引用の抜粋から、照合に使う先頭1行を取り出す。"""
    return _snippet_lines(snippet)[0]


def _iter_ungated_citations():
    """ゲートに入らない引用を1件ずつ返す: (仕様書名, 行リスト, 引用の項, 抜粋の先頭行)。

    `report_non_definition_citations()` と `triage_non_gated_citations()` が共有する。
    除外条件(def/class の引用・短すぎる抜粋・scan() が厳密に検証するもの)はここに1箇所だけ持つ。
    """
    scanned = _source_index()
    for spec in sorted(SPEC_ROOT.rglob("*.md")):
        if spec.name == "README.md":
            continue
        source = _report_source_for_spec(spec)
        if source is None:
            continue
        src_lines = source.read_text(encoding="utf-8").splitlines()
        # scan() が実際に走査する仕様書(対応する .py が一意に決まるもの)だけ、
        # 一意な抜粋がゲート済みとして除外できる。
        # scan() は family-quest の仕様書を常に TS ソースと照合する(同名の .py があっても)。ここも同じ条件にする。
        gated = (
            source.suffix == ".py"
            or spec.relative_to(SPEC_ROOT).parts[0] in FQ_SPEC_ROOT_PARTS
            or len(scanned.get(spec.stem) or []) != 1
        )
        name = str(spec.relative_to(SPEC_ROOT))
        for line in spec.read_text(encoding="utf-8").splitlines():
            blocks = list(_BLOCK_RE.finditer(line))
            for b_i, block in enumerate(blocks):
                if _names_other_file(line, block, source):
                    continue
                stop = _block_stop(line, block, blocks, b_i)
                rest = line[block.end():stop]
                before = line[blocks[b_i - 1].end() if b_i else 0:block.start()]
                snippets = _split_snippets(rest)
                back_snippets = _backtick_snippets(before)
                items = _ITEM_RE.findall(block.group(1))
                if not snippets or len(snippets) != len(items):
                    continue
                _, def_claimed = _definition_pairs(
                    rest, before, snippets, back_snippets, items
                )
                for pos, snippet in enumerate(snippets):
                    if pos in def_claimed:
                        continue  # 行番号は def/class の位置を指す(scan() が検証済み)
                    if re.match(r'"(?:async )?(?:def|class) \w+', snippet.strip()):
                        continue  # scan() が厳密に検証済み
                    head = _snippet_first_line(snippet)
                    if len(head) < _MIN_LITERAL_HEAD:
                        continue  # 短すぎる抜粋は誤検知が多いので数えない
                    # scan() が厳密に検証するのは「単一行の引用 かつ 抜粋がソース内で
                    # 一意」のものだけ。ここの除外条件はそれと完全に一致させる
                    # (広く除外すると、ゲートにも非ゲートの集計にも現れない引用が生まれる)。
                    if gated and (
                        _literal_target(snippet, items[pos], src_lines) is not None
                        or _range_containment(snippet, items[pos], src_lines) is not None
                    ):
                        continue
                    yield name, src_lines, items[pos], head


def _cited_span(item: str) -> Tuple[int, int]:
    """引用の項(`12` / `12〜20`)から (開始行, 終了行) を返す。"""
    nums = [int(n) for n in re.findall(r"\d+", item)]
    return nums[0], nums[-1]


def report_non_definition_citations() -> Tuple[Dict[str, Tuple[int, int]], int, int]:
    """ゲートに入らない引用について「抜粋の先頭行が引用行±1に実在するか」を数える。

    Issue #655: 本チェッカーが保証しているのは `def X(` / `class X` で始まる引用だけで、
    式や文の抜粋(全体の大半)は未検証だった。まずは件数を可視化する。戻り値は
    (仕様書名 -> (不一致数, 検査数), 総不一致数, 総検査数)。

    AUDIT-019: その後、式・文の抜粋でも**ソース内で一意に一致する**ものは scan() が
    厳密に検証するようになった。ここで二重に数えると「非ゲートの件数」が実態より
    多く見え、main() が出す保証範囲の表示が誤りになるため、scan() と同じ条件で
    一意に決まる引用はこのレポートから除く。残るのは、抜粋が一意でない・
    ソースに見つからない・`.sh` / TypeScript 側といった、今も非ゲートのものだけ。
    """
    counts: Dict[str, List[int]] = {}
    total_bad = total_checked = 0
    for name, src_lines, item, head in _iter_ungated_citations():
        cited = _cited_span(item)[0]
        window = src_lines[max(0, cited - 2): cited + 1]
        bad = 0 if any(head in l for l in window) else 1
        entry = counts.setdefault(name, [0, 0])
        entry[0] += bad
        entry[1] += 1
        total_bad += bad
        total_checked += 1
    per_spec = {name: (bad, checked) for name, (bad, checked) in counts.items()}
    return per_spec, total_bad, total_checked


# triage_non_gated_citations() の分類名
TRIAGE_OK = "引用範囲内に抜粋が実在(おそらく正しい)"
TRIAGE_UNIQUE = "抜粋は別の1行に実在(引用範囲の外=ずれている)"
TRIAGE_MULTI = "抜粋は複数行に実在し、どれも引用範囲の外(ずれているが位置を決められない)"
TRIAGE_MISSING = "抜粋がソースに見つからない(表記ゆれ・要約・ソース側の変更)"


def triage_non_gated_citations() -> Tuple[Dict[str, Dict[str, int]], Dict[str, int]]:
    """ゲート外の引用を、抜粋の先頭行の実在位置で4種類に仕分ける(Issue #950)。

    `--report` の「引用行±1」判定は、範囲引用(`12〜40`)の途中に抜粋がある正しい引用も
    「見つからない」と数えてしまう。ここでは範囲引用は範囲の終端まで見て、さらに
    ずれている引用を「位置が決まる/決まらない」「そもそもソースに無い」に分ける。
    戻り値は (仕様書名 -> 分類別件数, 全体の分類別件数)。
    """
    per_spec: Dict[str, Dict[str, int]] = {}
    total: Dict[str, int] = {}
    for name, src_lines, item, head in _iter_ungated_citations():
        start, end = _cited_span(item)
        if any(head in l for l in src_lines[max(0, start - 2): max(start, end)]):
            kind = TRIAGE_OK
        else:
            occurrences = [i for i, l in enumerate(src_lines) if head in l]
            if not occurrences:
                kind = TRIAGE_MISSING
            elif len(occurrences) == 1:
                kind = TRIAGE_UNIQUE
            else:
                kind = TRIAGE_MULTI
        per_spec.setdefault(name, {}).setdefault(kind, 0)
        per_spec[name][kind] += 1
        total[kind] = total.get(kind, 0) + 1
    return per_spec, total


# --- シンボル参照(Issue #950) -----------------------------------------------
#
# 行番号は、ソースの行が増減するたびに静かに古くなる(非ゲート分の約45%が既にずれていた)。
# 行番号の代わりに「どの関数/メソッドの中か」を書く書式を用意する:
#
#     (参照: Class.method / 抜粋: "return foo(")
#
# 検証は「そのシンボルがソースに存在し、抜粋の先頭行がその本体の中にある」こと。行が
# 増減しても、同じ関数の中にある限り通る(関数の改名・削除・抜粋の移動だけが検知される)。
# 対象は Python と シェル(`.sh`)のソースに1対1で対応する仕様書。シェルのシンボルは関数名
# (`name() {`)だけ(_shell_function_spans)。


def _resolve_symbol(symbol: str, qualified: Dict, flat: Dict) -> Optional[Tuple[int, int]]:
    """`name` / `Class.method` からソース内の行範囲を引く。曖昧・不在なら None。"""
    if "." in symbol:
        klass, _, name = symbol.rpartition(".")
        return qualified.get((klass.split(".")[-1], name))
    return _resolve(symbol, None, qualified, flat)


# 変換時に見出しからシンボルを読む。`### `name`` に加え、`### 関数 `name`` のような
# 種別つきの見出しも読む(scan() 側の _HEADING_RE は変えない: そちらは def/class 引用の一意化に使う)。
_CONVERT_HEADING_RE = re.compile(r"^###\s+(?:(?:関数|メソッド|クラス)\s+)?`?([A-Za-z_][\w.]*)")


def _symbol_spans(source: str) -> List[Tuple[str, int, int]]:
    """シンボル参照に書ける (シンボル, 開始行, 終了行) の一覧。

    トップレベルの関数・クラスと、クラス直下のメソッド(`Class.method`)。どれも
    `_resolve_symbol` で同じ範囲へ一意に解決できるものだけを返す(書いたシンボルが
    scan_symbol_references で「一意に決まらない」とならないように)。
    """
    tree = ast.parse(source)
    qualified, flat = _definitions(source, with_decorators=True)
    spans: List[Tuple[str, int, int]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        names = [(node.name, node)]
        if isinstance(node, ast.ClassDef):
            names += [
                ("%s.%s" % (node.name, child.name), child)
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
        for name, target in names:
            start = min([target.lineno] + [d.lineno for d in target.decorator_list])
            span = (start, target.end_lineno)
            if _resolve_symbol(name, qualified, flat) == span:
                spans.append((name, span[0], span[1]))
    return spans


# シェル関数の定義行(`name() {` / `function name {`)。行頭(インデント無し)のものだけを関数とみなす。
_SHELL_FUNC_RE = re.compile(r"^(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*\))?\s*\{\s*(?:#.*)?$")
_SHELL_FUNC_END_RE = re.compile(r"^\}\s*(?:#.*)?$")


def _shell_function_spans(source: str) -> List[Tuple[str, int, int]]:
    """シェルスクリプトの関数を (関数名, 開始行, 終了行) で返す(Issue #950)。

    関数の範囲は「行頭の `name() {` から、次の行頭の `}` まで」とする。このリポジトリの
    シェルスクリプトはすべてこの書き方で、本体はインデントされている。`function` キーワードの
    有無はどちらも受け付ける(`function name {` は `()` が無くても関数定義)。閉じ括弧が
    見つからないもの・同名が複数あるもの(どちらを指すか決められない)は返さない。
    """
    lines = source.splitlines()
    spans: List[Tuple[str, int, int]] = []
    i = 0
    while i < len(lines):
        match = _SHELL_FUNC_RE.match(lines[i])
        if match and (lines[i].startswith("function") or "(" in lines[i]):
            for j in range(i + 1, len(lines)):
                if _SHELL_FUNC_END_RE.match(lines[j]):
                    spans.append((match.group(1), i + 1, j + 1))
                    i = j
                    break
        i += 1
    counts = collections.Counter(name for name, _, _ in spans)
    return [span for span in spans if counts[span[0]] == 1]


class _SymbolTable(NamedTuple):
    """シンボル参照の解決に使う、仕様書1件ぶんの対応ソースの情報。"""
    source: Path
    src_lines: List[str]
    # シンボル -> (開始行, 終了行)。曖昧・不在なら None
    resolve: Callable[[str], Optional[Tuple[int, int]]]
    # _enclosing_symbol 用の (シンボル, 開始行, 終了行) 一覧
    spans: List[Tuple[str, int, int]]


def _symbol_table_for_spec(spec: Path, py_index: Dict[str, List[Path]], sh_index: Dict[str, List[Path]]) -> Optional[_SymbolTable]:
    """仕様書に対応するソースのシンボル表を返す(Python を優先し、無ければシェル)。対応が一意でなければ None。"""
    candidates = py_index.get(spec.stem)
    if candidates and len(candidates) == 1:
        source_text = candidates[0].read_text(encoding="utf-8")
        qualified, flat = _definitions(source_text, with_decorators=True)
        if qualified is None:
            return None
        return _SymbolTable(
            candidates[0], source_text.splitlines(),
            lambda symbol: _resolve_symbol(symbol, qualified, flat), _symbol_spans(source_text),
        )
    if candidates:
        return None
    candidates = sh_index.get(spec.stem)
    if not candidates or len(candidates) != 1:
        return None
    source_text = candidates[0].read_text(encoding="utf-8")
    spans = _shell_function_spans(source_text)
    by_name = {name: (start, end) for name, start, end in spans}
    return _SymbolTable(candidates[0], source_text.splitlines(), by_name.get, spans)


def _snippet_hits(snippet_lines: List[str], src_lines: List[str]) -> List[int]:
    """抜粋がソースに現れる位置(先頭行の行番号、1始まり)を返す。

    複数行の抜粋は、空でない行が**連続する行に順に**現れる位置だけを数える。先頭行だけでは
    複数箇所に現れる抜粋(`except Exception:` 等)も、続く行まで見れば1箇所に決まることが多い。
    """
    lines = [line for line in snippet_lines if line]
    if not lines:
        return []
    return [
        i + 1
        for i in range(len(src_lines) - len(lines) + 1)
        if all(part in src_lines[i + k] for k, part in enumerate(lines))
    ]


def _enclosing_symbol(snippet_lines: List[str], src_lines: List[str], spans: List[Tuple[str, int, int]]) -> Optional[str]:
    """抜粋を含むシンボルを返す。出現が全て同じ1つのシンボルの中にあるときだけ。

    出現(_snippet_hits)ごとに「それを含む最も内側のシンボル」を求め、全出現で一致したらそれを返す。
    モジュールレベル(どのシンボルにも含まれない)の出現が1つでもあれば None。
    """
    found: Optional[str] = None
    hits = _snippet_hits(snippet_lines, src_lines)
    if not hits:
        return None
    for lineno in hits:
        inside = [(end - start, name) for name, start, end in spans if start <= lineno <= end]
        if not inside:
            return None
        name = min(inside)[1]
        if found is not None and name != found:
            return None
        found = name
    return found


def _symbol_enclosing_span(
    snippet_lines: List[str], src_lines: List[str], spans: List[Tuple[str, int, int]], start: int, end: int
) -> Optional[str]:
    """引用範囲 start〜end の中にある抜粋の出現が、すべて同じ1つのシンボルの中にあるときそのシンボルを返す。"""
    found: Optional[str] = None
    hits = [h for h in _snippet_hits(snippet_lines, src_lines) if start <= h <= end]
    if not hits:
        return None
    for lineno in hits:
        inside = [(e - s, name) for name, s, e in spans if s <= lineno <= e]
        if not inside:
            return None
        name = min(inside)[1]
        if found is not None and name != found:
            return None
        found = name
    return found


def _symbol_blocks(line: str):
    """1行から (ブロックの開始, 終了, シンボル, 抜粋リスト) を順に返す。"""
    blocks = list(_SYMBOL_BLOCK_RE.finditer(line))
    for i, block in enumerate(blocks):
        stop = blocks[i + 1].start() if i + 1 < len(blocks) else len(line)
        other = _BLOCK_RE.search(line, block.end())
        if other is not None:
            stop = min(stop, other.start())
        closing = _CITATION_END_RE.search(line, block.end(), stop)
        if closing is not None:
            stop = closing.start() + 2  # `")` までを抜粋に含める(_block_stop と同じ)
        yield block.start(), block.end(), block.group(1), _split_snippets(line[block.start():stop])


def scan_symbol_references() -> Tuple[List[str], int]:
    """`参照: シンボル / 抜粋: ...` の引用を検証する。戻り値は (問題の一覧, 検証した抜粋の数)。"""
    py_index, sh_index = _source_index(), _source_index((".sh",))
    problems: List[str] = []
    checked = 0
    for spec in sorted(SPEC_ROOT.rglob("*.md")):
        if spec.name == "README.md":
            continue
        table = _symbol_table_for_spec(spec, py_index, sh_index)
        if table is None:
            continue
        src_lines = table.src_lines
        for lineno, line in enumerate(spec.read_text(encoding="utf-8").splitlines(), 1):
            for _, _, symbol, snippets in _symbol_blocks(line):
                where = "%s:%d" % (spec.relative_to(REPO_ROOT), lineno)
                span = table.resolve(symbol)
                if span is None:
                    problems.append("%s `%s` がソースに存在しない(または同名が複数で一意に決まらない)" % (where, symbol))
                    continue
                body = src_lines[span[0] - 1: span[1]]
                for snippet in snippets:
                    head = _snippet_first_line(snippet)
                    if len(head) < _MIN_LITERAL_HEAD:
                        continue
                    checked += 1
                    if not any(head in l for l in body):
                        problems.append(
                            "%s 抜粋 `%s` が `%s`(%d〜%d行)の中に見つからない" % (where, head[:60], symbol, span[0], span[1])
                        )
    return problems, checked


def convert_to_symbol_references(specs: List[Path], write: bool = True, include_valid: bool = False) -> Tuple[int, int]:
    """指定した仕様書の「ずれている行番号引用」を、シンボル参照へ書き換える。戻り値は (変換数, 変換できなかった数)。

    変換するのは、(1) 引用が 1 項 + 抜粋 1 つの書式Aで、(2) 引用した行(範囲引用なら範囲)に
    抜粋の先頭行が無く(=すでにずれている)、(3) 直前の `### `シンボル`` 見出しのシンボルが
    ソースで一意に決まり、(4) その本体の中に抜粋の先頭行がある、ものだけ。
    どれかを満たさない引用は触らない(位置が確かめられないものを、確かめたことにしない)。
    ゲート済みの引用(def/class・一意な単一行リテラル)も触らない。

    `include_valid=True` のときは、(2) を外して「いまは正しい」引用も対象にする。ただし抜粋が
    複数行に現れる等でゲートに入れられない引用に限り(ゲート済みは行番号のまま検証される)、
    **引用範囲の中にある出現**がすべて同じ1つのシンボルの中にあるときだけ、そのシンボルへ書き換える。
    範囲が複数のシンボルやモジュールレベルにまたがる引用は触らない(行番号が持つ範囲の情報を捨てないため)。

    (3) の見出しが無い・シンボルとして解決できない場合に限り、抜粋の先頭行のソース内の出現が
    **すべて同じ1つのシンボルの中にある**なら、そのシンボルを使う(`_enclosing_symbol`)。
    見出しのシンボルが解決でき、その中に抜粋が無いもの(別の関数を引いている)は従来どおり触らない
    (著者の意図した場所と抜粋の場所が食い違っており、どちらが正しいか機械では決められないため)。
    """
    py_index, sh_index = _source_index(), _source_index((".sh",))
    converted = skipped = 0
    for spec in specs:
        converted_before = converted
        table = _symbol_table_for_spec(spec, py_index, sh_index)
        if table is None:
            continue
        src_lines, spans = table.src_lines, table.spans
        heading_symbol: Optional[str] = None
        original = spec.read_text(encoding="utf-8")
        out_lines: List[str] = []
        for line in original.splitlines():
            if re.match(r"#{1,3} ", line):
                # シンボルを読めない見出し(`### インポート一覧`・`## 補足` 等)では、前の見出しの
                # シンボルを引き継がない(別の節の引用を、前の節の関数へ紐付けてしまうため)。
                # `#### ` はシンボルの節の中の小見出しなので、引き継ぐ。
                heading = _CONVERT_HEADING_RE.match(line)
                heading_symbol = heading.group(1) if heading else None
            blocks = list(_BLOCK_RE.finditer(line))
            edits: List[Tuple[int, int, str]] = []
            for b_i, block in enumerate(blocks):
                if _names_other_file(line, block, table.source):
                    continue
                stop = _block_stop(line, block, blocks, b_i)
                rest = line[block.end():stop]
                before = line[blocks[b_i - 1].end() if b_i else 0:block.start()]
                snippets = _split_snippets(rest)
                items = _ITEM_RE.findall(block.group(1))
                if len(items) != 1 or len(snippets) != 1 or not rest.startswith(" / 抜粋: "):
                    continue
                _, def_claimed = _definition_pairs(rest, before, snippets, _backtick_snippets(before), items)
                snippet = snippets[0]
                if 0 in def_claimed or re.match(r'"(?:async )?(?:def|class) \w+', snippet.strip()):
                    continue
                head = _snippet_first_line(snippet)
                if len(head) < _MIN_LITERAL_HEAD:
                    continue
                if _literal_target(snippet, items[0], src_lines) is not None:
                    continue  # ゲート済み
                start, end = _cited_span(items[0])
                in_cited_place = any(head in l for l in src_lines[max(0, start - 2): max(start, end)])
                if in_cited_place and not include_valid:
                    continue  # 引用した場所に実在する(ずれていない)
                if in_cited_place:
                    if _range_containment(snippet, items[0], src_lines) is not None:
                        continue  # 範囲内検証でゲート済み(行番号のまま検証される)
                    symbol = _symbol_enclosing_span(_snippet_lines(snippet), src_lines, spans, start, end)
                    if symbol is None:
                        skipped += 1
                        continue
                    edits.append((block.start(), block.end(), "参照: %s" % symbol))
                    continue
                span = table.resolve(heading_symbol) if heading_symbol else None
                if span is not None:
                    symbol = heading_symbol
                    if not any(head in l for l in src_lines[span[0] - 1: span[1]]):
                        symbol = None
                else:
                    symbol = _enclosing_symbol(_snippet_lines(snippet), src_lines, spans)
                if symbol is None:
                    skipped += 1
                    continue
                edits.append((block.start(), block.end(), "参照: %s" % symbol))
            for a, b, text in reversed(edits):
                line = line[:a] + text + line[b:]
                converted += 1
            out_lines.append(line)
        # 変換が無ければ書き戻さない(CRLF の仕様書の改行を変えないため)
        if write and converted > converted_before:
            text = "\n".join(out_lines) + ("\n" if original.endswith("\n") else "")
            spec.write_text(text, encoding="utf-8")
    return converted, skipped


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fix", action="store_true", help="不一致を実ソースの位置へ書き換える")
    parser.add_argument(
        "--report",
        action="store_true",
        help="ゲートに入らない引用も「抜粋の先頭行が引用行±1にあるか」で粗く照合し、件数だけ報告する(exit 0)",
    )
    parser.add_argument(
        "--triage",
        action="store_true",
        help="ゲートに入らない引用を、抜粋の実在位置で4種類(正しそう/ずれ・位置確定/ずれ・位置不明/ソースに無い)に仕分けして件数を出す(exit 0。Issue #950)",
    )
    parser.add_argument(
        "--include-valid",
        action="store_true",
        help="--to-symbol-refs で、いまは正しい(引用範囲内に抜粋がある)ゲート外の引用も、範囲内の出現が1シンボルに収まるものに限り書き換える(Issue #950)",
    )
    parser.add_argument(
        "--to-symbol-refs",
        nargs="+",
        metavar="SPEC.md",
        type=Path,
        help="指定した仕様書の「ずれている行番号引用」を、見出しのシンボルが確かめられるものに限り `参照: シンボル` へ書き換える(Issue #950)",
    )
    args = parser.parse_args(argv)

    if args.to_symbol_refs:
        converted, skipped = convert_to_symbol_references([p.resolve() for p in args.to_symbol_refs], include_valid=args.include_valid)
        print("✅ シンボル参照へ %d件を書き換えました(確かめられず据え置き: %d件)。" % (converted, skipped))
        return 0

    if args.triage:
        per_spec, total = triage_non_gated_citations()
        kinds = (TRIAGE_OK, TRIAGE_UNIQUE, TRIAGE_MULTI, TRIAGE_MISSING)
        grand = sum(total.values())
        print("ℹ️ ゲートに入らない引用 %d件の仕分け(抜粋の先頭行の実在位置による。ゲートではない):" % grand)
        for kind in kinds:
            print("  %5d件  %s" % (total.get(kind, 0), kind))
        print()
        print("  仕様書ごと(ずれている/ソースに無い の合計が多い順):")
        bad_kinds = kinds[1:]
        worst = sorted(per_spec.items(), key=lambda kv: sum(kv[1].get(k, 0) for k in bad_kinds), reverse=True)
        for name, counts in worst[:20]:
            print("  %4d/%-4d  %s" % (sum(counts.get(k, 0) for k in bad_kinds), sum(counts.values()), name))
        return 0

    if args.report:
        per_spec, total_bad, total_checked = report_non_definition_citations()
        print("ℹ️ ゲートに入らない引用の粗い照合: %d件中 %d件が引用行±1に見つかりません。" % (total_checked, total_bad))
        print("   (範囲引用に単一行の抜粋が付いたもの・抜粋が一意でない/ソースに見つからないものが対象。")
        print("    ゲートではなく、抜粋の表記ゆれによる偽陽性を含む。)")
        print()
        worst = sorted(per_spec.items(), key=lambda kv: kv[1][0], reverse=True)
        for name, (bad, checked_count) in worst[:20]:
            if bad:
                print("  %4d/%-4d  %s" % (bad, checked_count, name))
        return 0

    skipped: List[Tuple[Path, str]] = []
    findings, checked = scan(fix=args.fix, skipped=skipped)
    if args.fix:
        # 書き換え後に残るのは「定義そのものが見つからない」＝手で直すしかないもの
        skipped = []
        findings, checked = scan(fix=False, skipped=skipped)

    for spec, reason in skipped:
        print("⚠️ 検証をスキップ: %s (%s)" % (spec.relative_to(REPO_ROOT), reason))

    symbol_problems, symbol_checked = scan_symbol_references()
    if symbol_problems:
        print("❌ シンボル参照チェック: %d件中 %d件に問題があります。" % (symbol_checked, len(symbol_problems)))
        for problem in symbol_problems:
            print("  - " + problem)
        print()

    # Issue #748 (AUDIT-019): 以前の出力は「✅ N件すべて一致」としか言わず、
    # それを見た人は「仕様書の行番号引用はすべて正しい」と読んでしまっていた。
    # 保証範囲を、結果と同じ場所に必ず出す(制限自体は docs/specifications/README.md にも
    # 記載しているが、緑の結果を見る場所とは別だった)。
    # ここで数えるのは出力用の内訳だけで、ゲートの判定(findings / 終了コード)には一切影響しない。
    _, ungated_bad, ungated_total = report_non_definition_citations()
    total_citations = checked + ungated_total
    gated_pct = (checked * 100 // total_citations) if total_citations else 0
    scope_lines = [
        "   ゲート対象は def/class の抜粋の引用と、抜粋がソース内で一意な式・文の引用"
        "(単一行の引用、および抜粋が複数行で両端とも一意な範囲引用)で、"
        "全 %d件中 %d件(%d%%)。" % (total_citations, checked, gated_pct),
        "   残り %d件(範囲引用に単一行の抜粋が付いたもの・抜粋が一意でない/見つからないもの)は"
        "非ゲートで、うち %d件は引用行±1に抜粋が見つかりません" % (ungated_total, ungated_bad),
        "   (--report で内訳。抜粋の表記ゆれによる偽陽性を含む)。保証範囲: docs/specifications/README.md",
    ]

    scope_lines.append("   別に、`参照: シンボル` の引用 %d件(行番号に依存しない書式。Issue #950)を検証しています。" % symbol_checked)

    if not findings and not symbol_problems:
        print("✅ 行番号引用チェック: ゲート対象 %d件はすべて実ソースの位置と一致しています。" % checked)
        for line in scope_lines:
            print(line)
        return 0
    if not findings:
        return 1

    print("❌ 行番号引用チェック: ゲート対象 %d件中 %d件が実ソースと一致しません。" % (checked, len(findings)))
    for line in scope_lines:
        print(line)
    print()
    for finding in findings:
        print("  - " + finding.describe())
    print()
    print("`python3 .github/scripts/check_spec_line_refs.py --fix` で行番号を実位置へ揃えられます。")
    print("「定義がソースに存在しない」ものは改名・削除が原因のため、仕様書の記述自体を直してください。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
