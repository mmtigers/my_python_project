#!/usr/bin/env python3
"""docs/specifications/ 配下の仕様書と実ソースの対応をチェックするツール。

このリポジトリの docs/specifications/ には、ソースファイル1つにつき
Markdown仕様書が1つ対応する規約がある（例外: 全体設計書.md）。
2026-08の仕様書一斉監査で「ソース更新後に仕様書が古いまま残る」ドリフトが
98件中44件見つかったことを受け、再発防止のために作成した。

サブコマンド:
  pr    - PRの差分ファイルだけを対象に、仕様書の更新漏れ・未文書化を検知する
          （GitHub Actionsの pull_request イベントから軽量に呼び出す想定）
  full  - リポジトリ全体を対象に、コミット日時ベースでドリフト・孤立ドキュメント・
          未文書化ファイルを洗い出す（週次の定期監査用）

いずれも exit code は常に 0 固定（非ブロッキング運用のため）。
検知結果はMarkdownレポートとして標準出力、または --out 指定先に書き出す。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = REPO_ROOT / "docs" / "specifications"

# 仕様書化の対象とするソースディレクトリと拡張子。
# tests/ や設定ファイル等、そもそも仕様書の対象外にしているものはここで除外する。
PY_SOURCE_DIRS = ["MY_HOME_SYSTEM", "DDD"]
PY_EXTENSIONS = {".py", ".sh"}
FQ_SOURCE_ROOT = "family-quest/src"
FQ_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}

EXCLUDE_PARTS = {"tests", "__pycache__", "node_modules", "migrations"}
EXCLUDE_SUFFIXES = {".d.ts"}


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        # 黙って空文字を返すと「差分ゼロ=ドリフトなし」と誤解してしまうため、
        # (shallow cloneでの参照エラー等) 必ず気付けるように失敗させる。
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\n{result.stderr}"
        )
    return result.stdout


def is_excluded(rel_path: Path) -> bool:
    if any(part in EXCLUDE_PARTS for part in rel_path.parts):
        return True
    return any(rel_path.name.endswith(suf) for suf in EXCLUDE_SUFFIXES)


def is_tracked_source(rel_path: Path) -> bool:
    """このスクリプトが「仕様書があるべき」と見なす対象かどうか。"""
    if is_excluded(rel_path):
        return False
    if rel_path.name == "__init__.py":
        # パッケージマーカーで実体を持たないことが大半のため対象外。
        return False
    parts = rel_path.parts
    if not parts:
        return False
    if parts[0] in PY_SOURCE_DIRS and rel_path.suffix in PY_EXTENSIONS:
        return True
    if str(rel_path).startswith(FQ_SOURCE_ROOT + "/") and rel_path.suffix in FQ_EXTENSIONS:
        return True
    return False


def source_to_doc_candidates(rel_path: Path) -> list[Path]:
    """ソースの相対パス(repo root基準)から、対応しうる仕様書パス候補を返す。

    候補は「最有力の規約」を先頭にして複数返す。既存ファイルの有無は
    呼び出し側でチェックする。
    """
    parts = rel_path.parts
    stem = rel_path.stem

    if parts[0] in PY_SOURCE_DIRS:
        # MY_HOME_SYSTEM/**/name.py 、DDD/name.py はいずれもフラットに
        # docs/specifications/<dir>/name.md へ対応する規約。
        return [SPEC_ROOT / parts[0] / f"{stem}.md"]

    if str(rel_path).startswith(FQ_SOURCE_ROOT + "/"):
        sub = rel_path.relative_to(FQ_SOURCE_ROOT)  # 例: App.tsx / hooks/useGameData.ts
        candidates = []
        # 通常規約: src/ 配下の構造をそのままミラーする
        candidates.append(SPEC_ROOT / "family-quest" / "src" / sub.with_suffix(".md"))
        # 歴史的な例外: src直下の App.tsx / main.tsx は src/ を省いた場所にある
        if len(sub.parts) == 1:
            candidates.append(SPEC_ROOT / "family-quest" / f"{stem}.md")
        return candidates

    return []


def doc_to_source_candidates(doc_path: Path) -> list[Path]:
    """仕様書の相対パス(docs/specifications/ 配下)から、対応しうるソースパス候補を返す。"""
    rel = doc_path.relative_to(SPEC_ROOT)
    parts = rel.parts
    stem = rel.stem

    if parts and parts[0] in PY_SOURCE_DIRS:
        # フラット命名なので、ディレクトリ内のどこにあるか探索が必要。
        matches = list((REPO_ROOT / parts[0]).rglob(f"{stem}.py"))
        matches += list((REPO_ROOT / parts[0]).rglob(f"{stem}.sh"))
        return [m.relative_to(REPO_ROOT) for m in matches]

    if parts and parts[0] == "family-quest":
        candidates = []
        if len(parts) == 2:
            # family-quest/App.md 等、src/ を省いた歴史的パターン
            for ext in FQ_EXTENSIONS:
                candidates.append(Path(FQ_SOURCE_ROOT) / f"{stem}{ext}")
        else:
            sub = Path(*parts[1:]).with_suffix("")  # src/hooks/useGameData 等
            if sub.parts and sub.parts[0] == "src":
                sub = Path(*sub.parts[1:])
            for ext in FQ_EXTENSIONS:
                candidates.append(Path(FQ_SOURCE_ROOT) / sub.with_suffix(ext))
        return candidates

    return []


@dataclass
class Report:
    stale: list[str] = field(default_factory=list)
    undocumented: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.stale or self.undocumented or self.orphaned)


def cmd_pr(base: str, head: str) -> Report:
    diff_output = run(["git", "diff", "--name-status", base, head])
    changed: dict[str, str] = {}
    for line in diff_output.splitlines():
        if not line.strip():
            continue
        status, *paths = line.split("\t")
        changed[paths[-1]] = status[0]  # rename時は新パスを使う

    changed_paths = set(changed.keys())
    report = Report()

    for path_str, status in changed.items():
        rel_path = Path(path_str)

        # 仕様書側が消された場合は「ソースが残っているのに対応する仕様書が消えた」
        # という別種の異常なので、ここでは検知しない(chore/削除PRは別途人間が判断)。
        if not is_tracked_source(rel_path):
            continue
        if status == "D":
            continue  # ソース削除PRは、別途「仕様書を廃止notice化してください」を促すのみ次段で

        candidates = source_to_doc_candidates(rel_path)
        existing = [c for c in candidates if c.exists()]

        if not existing:
            report.undocumented.append(path_str)
            continue

        doc_rel = existing[0].relative_to(REPO_ROOT)
        if str(doc_rel) not in changed_paths:
            report.stale.append(f"{path_str} → {doc_rel} が未更新")

    # ソース削除だけ別枠で拾い、仕様書側の廃止notice化を促す
    for path_str, status in changed.items():
        rel_path = Path(path_str)
        if status != "D" or not is_tracked_source(rel_path):
            continue
        candidates = source_to_doc_candidates(rel_path)
        existing = [c for c in candidates if c.exists()]
        if existing:
            doc_rel = existing[0].relative_to(REPO_ROOT)
            if str(doc_rel) not in changed_paths:
                report.orphaned.append(
                    f"{path_str} が削除されましたが {doc_rel} が未更新です"
                    "（廃止noticeへの書き換えを検討してください）"
                )

    return report


def git_last_commit_epoch(rel_path: Path) -> int | None:
    out = run(["git", "log", "-1", "--format=%ct", "--", str(rel_path)]).strip()
    return int(out) if out else None


def cmd_full() -> Report:
    report = Report()

    # 1. ソース起点: 未文書化 + ドリフト(ソースの方が新しい)
    for base_dir in PY_SOURCE_DIRS:
        for ext in PY_EXTENSIONS:
            for src in (REPO_ROOT / base_dir).rglob(f"*{ext}"):
                rel = src.relative_to(REPO_ROOT)
                if not is_tracked_source(rel):
                    continue
                candidates = source_to_doc_candidates(rel)
                existing = [c for c in candidates if c.exists()]
                if not existing:
                    report.undocumented.append(str(rel))
                    continue
                doc = existing[0]
                src_t = git_last_commit_epoch(rel)
                doc_t = git_last_commit_epoch(doc.relative_to(REPO_ROOT))
                if src_t and doc_t and src_t > doc_t:
                    days = (src_t - doc_t) / 86400
                    report.stale.append(
                        f"{rel} (ソースが{days:.1f}日新しい) → {doc.relative_to(REPO_ROOT)}"
                    )

    fq_src_dir = REPO_ROOT / FQ_SOURCE_ROOT
    for ext in FQ_EXTENSIONS:
        for src in fq_src_dir.rglob(f"*{ext}"):
            rel = src.relative_to(REPO_ROOT)
            if not is_tracked_source(rel):
                continue
            candidates = source_to_doc_candidates(rel)
            existing = [c for c in candidates if c.exists()]
            if not existing:
                report.undocumented.append(str(rel))
                continue
            doc = existing[0]
            src_t = git_last_commit_epoch(rel)
            doc_t = git_last_commit_epoch(doc.relative_to(REPO_ROOT))
            if src_t and doc_t and src_t > doc_t:
                days = (src_t - doc_t) / 86400
                report.stale.append(
                    f"{rel} (ソースが{days:.1f}日新しい) → {doc.relative_to(REPO_ROOT)}"
                )

    # 2. 仕様書起点: 孤立ドキュメント(対応ソースが存在しない)
    for doc in SPEC_ROOT.rglob("*.md"):
        rel = doc.relative_to(REPO_ROOT)
        if rel.name == "全体設計書.md":
            continue
        candidates = doc_to_source_candidates(doc)
        if candidates and not any((REPO_ROOT / c).exists() for c in candidates):
            report.orphaned.append(str(rel))

    return report


def render_report(report: Report, title: str) -> str:
    if report.is_empty():
        return f"## {title}\n\n検知事項なし。すべての仕様書がソースと整合しています。\n"

    lines = [f"## {title}", ""]
    if report.undocumented:
        lines.append("### 📄 仕様書が見つからないファイル")
        lines.append("")
        for item in report.undocumented:
            lines.append(f"- `{item}`")
        lines.append("")
    if report.stale:
        lines.append("### ⚠️ ドリフトの可能性（ソースの方が新しい／PR内で仕様書が未更新）")
        lines.append("")
        for item in report.stale:
            lines.append(f"- {item}")
        lines.append("")
    if report.orphaned:
        lines.append("### 🕸️ 孤立ドキュメント（対応するソースが見つからない）")
        lines.append("")
        for item in report.orphaned:
            lines.append(f"- {item}")
        lines.append("")
    lines.append(
        "> このチェックは非ブロッキングです。意図的な変更（フォーマットのみ等）であれば無視して構いません。"
    )
    return "\n".join(lines)


def main() -> int:
    # --out はサブコマンドの後ろに置く呼び出し方(例: `full --out x.md`)をするため、
    # トップレベルパーサーではなく各サブパーサーに個別に持たせる必要がある
    # (argparseはサブコマンドより前の位置にしかトップレベル引数を許さない)。
    out_parent = argparse.ArgumentParser(add_help=False)
    out_parent.add_argument("--out", help="レポートの書き出し先。省略時は標準出力。")

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    pr_parser = sub.add_parser("pr", help="PR差分のみを対象に検知する", parents=[out_parent])
    pr_parser.add_argument("--base", required=True)
    pr_parser.add_argument("--head", required=True)

    sub.add_parser("full", help="リポジトリ全体を対象に検知する", parents=[out_parent])

    args = parser.parse_args()

    if args.mode == "pr":
        report = cmd_pr(args.base, args.head)
        title = "仕様書ドリフトチェック（PR差分）"
    else:
        report = cmd_full()
        title = "仕様書ドリフト定期監査（リポジトリ全体）"

    output = render_report(report, title)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)

    return 0  # 非ブロッキング運用のため常に成功終了


if __name__ == "__main__":
    sys.exit(main())
