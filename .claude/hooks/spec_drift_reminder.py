#!/usr/bin/env python3
"""PostToolUse/Stopフック: ソース変更に対応する仕様書更新をClaudeに強制的に思い出させる。

MY_HOME_SYSTEM/*.py, DDD/*.py, family-quest/src/**/*.{ts,tsx,js,jsx} を編集したら、
対応する docs/specifications/ 配下の仕様書も同じセッション内で触れたかどうかを追跡する。

- "record" モード (PostToolUse): 編集/新規作成したファイルをセッションごとの一時状態ファイルに
  記録するだけで、何も出力しない。
- "report" モード (Stop): セッション内で編集した対象ソースのうち、対応する仕様書がまだ
  一度も編集されていないものが残っていれば、セッションの終了を一度だけブロックして
  .claude/skills/spec-drift-sync/SKILL.md の手順を踏むよう促す。同じファイルについて
  二度目以降はブロックしない(無限ループ防止)。

対象ファイルの判定・対応する仕様書パスの算出は .github/scripts/check_spec_drift.py の
is_tracked_source / source_to_doc_candidates をそのまま再利用する(判定ロジックの二重管理を避ける)。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

DEFAULT_STATE = {"sources": [], "specs": [], "reminded": []}


def load_check_spec_drift(repo_root: Path):
    module_path = repo_root / ".github" / "scripts" / "check_spec_drift.py"
    spec = importlib.util.spec_from_file_location("check_spec_drift", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclassデコレータがsys.modulesから自クラスを探すため登録が必要
    spec.loader.exec_module(module)
    return module


def resolve_repo_root(payload: dict) -> Path:
    cwd = payload.get("cwd")
    start = Path(cwd).resolve() if cwd else Path.cwd()
    probe = start
    while not (probe / ".git").exists() and probe != probe.parent:
        probe = probe.parent
    return probe if (probe / ".git").exists() else start


def state_path(session_id: str) -> Path:
    base = Path(tempfile.gettempdir()) / "claude-spec-drift-sync"
    base.mkdir(exist_ok=True)
    return base / f"{session_id}.json"


def load_state(path: Path) -> dict:
    if not path.exists():
        return dict(DEFAULT_STATE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_STATE)
    for key, default in DEFAULT_STATE.items():
        data.setdefault(key, list(default))
    return data


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state), encoding="utf-8")


def record(payload: dict, repo_root: Path) -> None:
    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return
    try:
        rel = Path(file_path).resolve().relative_to(repo_root)
    except ValueError:
        return  # リポジトリ外のファイルは対象外

    session_id = payload.get("session_id", "unknown")
    path = state_path(session_id)
    state = load_state(path)
    rel_str = str(rel)

    if rel_str.startswith("docs/specifications/") and rel.suffix == ".md":
        if rel_str not in state["specs"]:
            state["specs"].append(rel_str)
            save_state(path, state)
        return

    mod = load_check_spec_drift(repo_root)
    if mod.is_tracked_source(rel) and rel_str not in state["sources"]:
        state["sources"].append(rel_str)
        save_state(path, state)


def report(payload: dict, repo_root: Path) -> None:
    session_id = payload.get("session_id", "unknown")
    path = state_path(session_id)
    state = load_state(path)

    if not state["sources"]:
        return

    mod = load_check_spec_drift(repo_root)
    specs_touched = set(state["specs"])
    reminded = set(state["reminded"])

    pending = []
    for src in state["sources"]:
        candidates = mod.source_to_doc_candidates(Path(src))
        candidate_strs = [str(c.relative_to(repo_root)) for c in candidates]
        if specs_touched & set(candidate_strs):
            continue  # このセッション内で対応する仕様書も編集済み
        pending.append((src, candidate_strs))

    new_pending = [(s, c) for s, c in pending if s not in reminded]
    if not new_pending:
        return

    lines = [
        "このセッションで以下のソースファイルを編集しましたが、対応する仕様書 (docs/specifications/) を"
        "このセッション内で更新した形跡がありません。",
        ".claude/skills/spec-drift-sync/SKILL.md の手順に従って仕様書ドリフトが残っていないか確認し、"
        "必要なら更新してください（フォーマットのみの変更など仕様書更新が不要な場合はそのままで構いません）。",
        "",
    ]
    for src, candidates in new_pending:
        target = candidates[0] if candidates else "(対応する仕様書パスを規約から特定してください)"
        lines.append(f"- {src} → {target}")
    reason = "\n".join(lines)

    state["reminded"] = sorted(reminded | {s for s, _ in new_pending})
    save_state(path, state)

    print(json.dumps({"decision": "block", "reason": reason}))


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("record", "report"):
        return 0

    mode = sys.argv[1]
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    repo_root = resolve_repo_root(payload)

    if mode == "record":
        record(payload, repo_root)
    else:
        report(payload, repo_root)

    return 0


if __name__ == "__main__":
    sys.exit(main())
