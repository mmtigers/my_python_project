#!/usr/bin/env python3
"""静的解析(ruff / bandit)の「新規指摘のみブロック」ラチェットチェック。

背景: test.yml では ruff のフルレポートと bandit の Medium/Low が非ブロッキングで、
「指摘が 0 件になったらブロッキング化する」方針だった。しかし既存の指摘
(MY_HOME_SYSTEM の ruff 約90件など)を一括整形で消すことは、仕様書が行番号で
ソースを引用する規約のため禁止されており(ruff.toml 冒頭のコメント参照)、
手作業で減らすしかない。その間も PR が新しい指摘を持ち込むのは止めたいので、
**merge-base 時点の指摘集合と比較して増えた分だけ失敗** させる。

仕組み:
  1. `git worktree add` で base コミットを一時ディレクトリへ展開し、同じツールを
     同じ引数で実行して JSON を得る(ruff.toml 等の設定は base 側ツリーのものが使われる)。
  2. 現在のワーキングツリー(PR の head / merge ref)でも同じツールを実行する。
  3. 指摘を「相対パス + ルールID + メッセージ」のキーで多重集合として数え、
     head の件数が base の件数を上回るキーを「新規指摘」として報告する。
     行番号はキーに含めないため、無関係な行の追加・削除で既存指摘が移動しても
     新規扱いにならない。

使い方:
  check_lint_ratchet.py --tool ruff   --dir MY_HOME_SYSTEM --base <merge-base sha>
  check_lint_ratchet.py --tool bandit --dir MY_HOME_SYSTEM --base <sha> -- -x ./tests -ll -ii

`--` 以降はツールへそのまま渡す追加引数(bandit の除外・重大度フィルタ等)。

終了コード: 0 = 新規指摘なし、1 = 新規指摘あり、2 = 実行エラー
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

Finding = tuple[str, str, str]  # (相対パス, ルールID, メッセージ)


def _relpath(filename: str, base_dir: Path) -> str:
    """ツールが出力するパス(絶対 / ./相対)を base_dir 相対の POSIX 形式に正規化する。"""
    p = Path(filename)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(base_dir.resolve())
        except ValueError:
            pass
    return p.as_posix().removeprefix("./")


def parse_ruff(payload: str, base_dir: Path) -> list[tuple[Finding, int]]:
    """ruff --output-format json の出力を (Finding, 行番号) の列にする。"""
    out = []
    for item in json.loads(payload or "[]"):
        key = (_relpath(item["filename"], base_dir), item["code"] or "", item["message"])
        out.append((key, int(item.get("location", {}).get("row") or 0)))
    return out


def parse_bandit(payload: str, base_dir: Path) -> list[tuple[Finding, int]]:
    """bandit -f json の出力を (Finding, 行番号) の列にする。"""
    data = json.loads(payload or "{}")
    out = []
    for item in data.get("results", []):
        key = (
            _relpath(item["filename"], base_dir),
            f"{item['test_id']}[{item['issue_severity']}/{item['issue_confidence']}]",
            item["issue_text"],
        )
        out.append((key, int(item.get("line_number") or 0)))
    return out


def run_tool(tool: str, cwd: Path, extra_args: Iterable[str]) -> str:
    """ツールを cwd で実行し、JSON 文字列を返す(指摘ありの非0終了は正常扱い)。"""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = Path(tmp.name)
    try:
        if tool == "ruff":
            cmd = ["ruff", "check", ".", "--output-format", "json", "--exit-zero", "-o", str(out_path), *extra_args]
        elif tool == "bandit":
            cmd = ["bandit", "-r", ".", "-f", "json", "-q", "-o", str(out_path), *extra_args]
        else:
            raise ValueError(f"unknown tool: {tool}")
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise RuntimeError(
                f"{tool} が結果ファイルを出力しませんでした (exit {res.returncode}, cwd={cwd})\n{res.stderr}"
            )
        return out_path.read_text(encoding="utf-8")
    finally:
        out_path.unlink(missing_ok=True)


def collect(tool: str, root: Path, subdir: str, extra_args: Iterable[str]) -> list[tuple[Finding, int]]:
    target = root / subdir
    if not target.is_dir():
        # base 側に対象ディレクトリが無い(新規追加)なら指摘ゼロとして扱う
        return []
    payload = run_tool(tool, target, extra_args)
    return parse_ruff(payload, target) if tool == "ruff" else parse_bandit(payload, target)


def new_findings(
    base: list[tuple[Finding, int]], head: list[tuple[Finding, int]]
) -> list[tuple[Finding, int]]:
    """head にあって base に無い(件数が増えた)指摘を、head 側の行番号つきで返す。"""
    base_counts = collections.Counter(k for k, _ in base)
    head_counts = collections.Counter(k for k, _ in head)
    result = []
    for key, count in sorted(head_counts.items()):
        extra = count - base_counts.get(key, 0)
        if extra <= 0:
            continue
        # 同じキーの指摘が複数ある場合、行番号の大きい方(新しく追加された可能性が高い)から報告する
        rows = sorted((row for k, row in head if k == key), reverse=True)[:extra]
        result.extend((key, row) for row in sorted(rows))
    return result


def with_base_worktree(base_sha: str):
    """base コミットを一時 worktree として展開するコンテキストマネージャ。"""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        tmp = Path(tempfile.mkdtemp(prefix="lint-ratchet-base-"))
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", "-q", str(tmp), base_sha],
                cwd=REPO_ROOT, check=True, capture_output=True, text=True,
            )
            yield tmp
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(tmp)],
                cwd=REPO_ROOT, check=False, capture_output=True, text=True,
            )
            shutil.rmtree(tmp, ignore_errors=True)

    return _ctx()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tool", choices=["ruff", "bandit"], required=True)
    parser.add_argument("--dir", required=True, help="リポジトリルートからの対象ディレクトリ(例: MY_HOME_SYSTEM)")
    parser.add_argument("--base", required=True, help="比較元コミット(通常は base と head の merge-base)")
    parser.add_argument("tool_args", nargs="*", help="`--` 以降: ツールへ渡す追加引数")
    args = parser.parse_args(argv)

    if shutil.which(args.tool) is None:
        print(f"::error::[lint-ratchet] {args.tool} が PATH にありません")
        return 2

    try:
        with with_base_worktree(args.base) as base_root:
            base = collect(args.tool, base_root, args.dir, args.tool_args)
        head = collect(args.tool, REPO_ROOT, args.dir, args.tool_args)
    except (subprocess.CalledProcessError, RuntimeError, json.JSONDecodeError, OSError) as e:
        detail = getattr(e, "stderr", "") or ""
        print(f"::error::[lint-ratchet] {args.tool} ({args.dir}) の実行に失敗しました: {e}\n{detail}")
        return 2

    added = new_findings(base, head)
    label = f"{args.tool} ({args.dir})"
    print(f"[lint-ratchet] {label}: base {len(base)}件 → head {len(head)}件、新規 {len(added)}件")
    if not added:
        return 0
    for (rel, code, message), row in added:
        path = f"{args.dir}/{rel}"
        print(f"::error file={path},line={row},title=lint-ratchet {code}::{message}")
        print(f"  {path}:{row}: {code} {message}")
    print(
        f"::error::[lint-ratchet] {label}: merge-base に無い新規指摘が {len(added)} 件あります。"
        "既存の指摘は対象外です(件数を増やさないでください)。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
