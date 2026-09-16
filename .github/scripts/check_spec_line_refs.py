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

抜粋が `def X(` / `class X` で始まる引用（＝行番号がその定義の位置を指していることが
確実なもの）だけを対象に、`行番号:` の値が実ソースのその定義の開始行と一致するかを見る。

- 抜粋が定義で始まらない引用（式や文の抜粋）は、位置を機械的に特定できないため対象外
- 同名の定義が複数ある場合は、直前の `### `クラス名.メソッド名`` 見出しで一意化する。
  それでも特定できなければスキップする（誤検知を出さない側に倒す）
- `行番号: A, B / 抜粋: "s1", "s2"` のように要素数が一致する並列引用は、位置対応で
  それぞれ検証する

対象は `MY_HOME_SYSTEM/**/*.py` と `DDD/**/*.py`（テスト・`__init__.py` は除く）に
1対1で対応する仕様書のみ。TypeScript側は `def`/`class` の規約が異なるため対象外。

## 使い方

    python3 .github/scripts/check_spec_line_refs.py            # 検証(不一致があれば exit 1)
    python3 .github/scripts/check_spec_line_refs.py --fix      # 一致するよう行番号を書き換える

CI では test.yml の lint ジョブから `pytest .github/scripts/` 経由で
`test_check_spec_line_refs.py` が呼び出す。失敗した場合は `--fix` を実行し、
差分を目視確認したうえでコミットすること（`--fix` は定義の開始行・終了行しか
触らないため、抜粋そのものが古い場合＝定義名が変わった/消えた場合は手で直す必要がある）。
"""
from __future__ import annotations

import argparse
import ast
import collections
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

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


class Finding(NamedTuple):
    spec: Path
    line_no: int
    symbol: str
    cited: str
    actual: Optional[int]
    reason: str

    def describe(self) -> str:
        where = "%s:%d" % (self.spec.relative_to(REPO_ROOT), self.line_no)
        if self.actual is None:
            return "%s  `%s` — %s (引用: 行番号: %s)" % (where, self.symbol, self.reason, self.cited)
        return "%s  `%s` — 引用は %s 行目だが実際の定義は %d 行目 (%s)" % (
            where, self.symbol, self.cited, self.actual, self.reason,
        )


def _definitions(source: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    """(クラス名, メソッド名) -> 範囲 と、名前 -> 範囲リスト を返す。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None, None
    qualified: Dict[Tuple[str, str], Tuple[int, int]] = {}
    flat: Dict[str, List[Tuple[int, int]]] = collections.defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified[(node.name, child.name)] = (child.lineno, child.end_lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            flat[node.name].append((node.lineno, node.end_lineno))
    return qualified, flat


def _source_index() -> Dict[str, List[Path]]:
    """仕様書のベース名 -> 対応しうるソースファイル"""
    index: Dict[str, List[Path]] = collections.defaultdict(list)
    for root in SOURCE_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.py"):
            name = path.stem
            if name == "__init__" or name.startswith("test_") or "tests" in path.parts:
                continue
            index[name].append(path)
    return index


def _split_snippets(rest: str) -> List[str]:
    """`抜粋: "a", "b"` の抜粋部分をスニペット単位に分割する。"""
    match = re.search(r'抜粋: (".*)$', rest)
    if not match:
        return []
    return re.split(r'(?<="), ?(?=")', match.group(1))


def _resolve(symbol: str, klass: Optional[str], qualified: Dict, flat: Dict) -> Optional[Tuple[int, int]]:
    candidates = flat.get(symbol)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if klass and (klass, symbol) in qualified:
        return qualified[(klass, symbol)]
    return None


def scan(fix: bool = False) -> Tuple[List[Finding], int]:
    """全仕様書を走査する。戻り値は (不一致の一覧, 検証できた引用の総数)。"""
    index = _source_index()
    findings: List[Finding] = []
    checked = 0

    for spec in sorted(SPEC_ROOT.rglob("*.md")):
        if spec.name == "README.md":
            continue
        candidates = index.get(spec.stem)
        if not candidates or len(candidates) != 1:
            continue
        qualified, flat = _definitions(candidates[0].read_text(encoding="utf-8"))
        if qualified is None:
            continue

        lines = spec.read_text(encoding="utf-8").splitlines(keepends=True)
        klass: Optional[str] = None
        changed = False

        for idx, line in enumerate(lines):
            heading = _HEADING_RE.match(line)
            if heading:
                klass = heading.group(1).split(".")[0]

            blocks = list(_BLOCK_RE.finditer(line))
            if not blocks:
                continue
            rewritten = line
            shift = 0
            for b_i, block in enumerate(blocks):
                stop = blocks[b_i + 1].start() if b_i + 1 < len(blocks) else len(line)
                rest = line[block.end():stop]
                snippets = _split_snippets(rest)
                items = _ITEM_RE.findall(block.group(1))
                # スニペット数と行番号要素数が揃っているときだけ位置対応で検証する
                pairs: Iterable[Tuple[int, str]]
                if len(snippets) == len(items) and len(items) > 1:
                    pairs = list(enumerate(snippets))
                else:
                    head = _SNIPPET_HEAD_RE.search(rest)
                    if not head:
                        continue
                    pairs = [(0, '"def %s(' % head.group(1))]

                new_items = list(items)
                touched = False
                for pos, snippet in pairs:
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fix", action="store_true", help="不一致を実ソースの定義位置へ書き換える")
    args = parser.parse_args(argv)

    findings, checked = scan(fix=args.fix)
    if args.fix:
        # 書き換え後に残るのは「定義そのものが見つからない」＝手で直すしかないもの
        findings, checked = scan(fix=False)

    if not findings:
        print("✅ 行番号引用チェック: %d件すべて実ソースの定義位置と一致しています。" % checked)
        return 0

    print("❌ 行番号引用チェック: %d件中 %d件が実ソースと一致しません。" % (checked, len(findings)))
    print()
    for finding in findings:
        print("  - " + finding.describe())
    print()
    print("`python3 .github/scripts/check_spec_line_refs.py --fix` で行番号を実位置へ揃えられます。")
    print("「定義がソースに存在しない」ものは改名・削除が原因のため、仕様書の記述自体を直してください。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
