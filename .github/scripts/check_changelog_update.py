#!/usr/bin/env python3
"""family-quest のソースを変更した PR で、更新履歴 (changelog.ts) の追記漏れを検知する。

アプリ内「アップデートのれきし」の元データ `family-quest/src/lib/changelog.ts` は手動で
追記する運用のため、足し忘れるとそのアップデートは履歴に出ない。PR の差分に
family-quest/src のアプリ本体(テスト・型定義を除く)の変更があるのに changelog.ts が
含まれていなければ、気付きを促すレポートを出す。

あくまで非ブロッキング(常に exit 0)。内部リファクタなど履歴に載せない変更も多いため、
CI を赤くはしない(呼び出し側ワークフローも continue-on-error)。git コマンドが失敗した
場合のみ例外で非0終了する(「差分取得失敗=漏れなし」と誤解しないため)。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PurePosixPath("family-quest/src")
CHANGELOG_PATH = "family-quest/src/lib/changelog.ts"
APP_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}
EXCLUDE_SUFFIXES = (".d.ts", ".test.ts", ".test.tsx")
EXCLUDE_DIRS = {"test"}


def is_app_source(path: str) -> bool:
    """履歴の対象になりうるアプリ本体のソースかどうか(テスト・型定義・changelog自身を除く)。"""
    p = PurePosixPath(path)
    if path == CHANGELOG_PATH:
        return False
    if SRC_ROOT not in p.parents:
        return False
    if p.suffix not in APP_EXTENSIONS:
        return False
    if p.name.endswith(EXCLUDE_SUFFIXES):
        return False
    return not any(part in EXCLUDE_DIRS for part in p.parts)


def find_missing(changed_files: list[str]) -> list[str]:
    """changelog.ts が更新されていないとき、履歴対象になりうる変更ファイルを返す(更新済みなら空)。"""
    if CHANGELOG_PATH in changed_files:
        return []
    return [f for f in changed_files if is_app_source(f)]


def build_report(missing: list[str]) -> str:
    lines = ["## 更新履歴チェック（PR差分）", ""]
    if not missing:
        lines.append("検知事項なし。")
        return "\n".join(lines) + "\n"
    lines += [
        "`family-quest/src` のアプリ本体が変更されていますが、"
        f"`{CHANGELOG_PATH}` は更新されていません。",
        "ユーザーに見える変更であれば、`CHANGELOG` の先頭にエントリを追記してください"
        "（内部リファクタなど履歴に載せない変更ならこのままで問題ありません。"
        "このチェックはブロッキングではありません）。",
        "",
        "変更されたファイル:",
    ]
    lines += [f"- `{f}`" for f in missing]
    return "\n".join(lines) + "\n"


def changed_files(base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", base, head],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff が失敗しました: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--out", help="レポートの書き出し先。省略時は標準出力。")
    args = parser.parse_args()

    report = build_report(find_missing(changed_files(args.base, args.head)))
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
