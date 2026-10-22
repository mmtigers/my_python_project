#!/usr/bin/env bash
# 共通Skills(repo-survey / issue-fix / verify-handoff)を ~/.claude/skills へ配置する。
# 既存の同名スキルは上書きしない(差分があれば警告するだけ)。強制更新は FORCE=1。
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)"
dest="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
mkdir -p "$dest"
for d in "$src"/*/; do
  name=$(basename "$d")
  if [ ! -e "$dest/$name" ]; then
    cp -r "$d" "$dest/$name"; echo "installed: $name"
  elif diff -rq -x __pycache__ "$d" "$dest/$name" >/dev/null; then
    echo "up-to-date: $name"
  elif [ "${FORCE:-0}" = 1 ]; then
    rm -rf "$dest/$name" && cp -r "$d" "$dest/$name"; echo "replaced: $name"
  else
    echo "SKIP(differs): $name  (確認して FORCE=1 で上書き可)" >&2
  fi
done
