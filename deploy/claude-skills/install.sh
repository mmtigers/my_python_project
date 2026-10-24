#!/usr/bin/env bash
# 共通Skills(repo-survey / issue-fix / verify-handoff)を ~/.claude/skills へ配置する。
# 既存の同名スキルは上書きしない(差分があれば警告するだけ)。強制更新は FORCE=1。
#
# 正本は deploy/claude-skills/(git管理)。~/.claude/skills はここから配布した先で、
# 直接編集しない(編集はリポジトリで行い、FORCE=1 で反映する)。Issue #1021
#
# --check       : 何も変更せず、正本と配布先のずれだけを報告する(ずれ・未配置があれば終了コード1)。
# --only-drift  : --check と併用。未配置は報告せず、「配置済みなのに内容が違う」ものだけを報告する
#                 (クラウド環境のように ~/.claude が毎回空の場合の雑音を避ける)。
set -euo pipefail
rc=0
mode=install
only_drift=0
for arg in "$@"; do
  case "$arg" in
    --check) mode=check ;;
    --only-drift) only_drift=1 ;;
    *) echo "usage: $0 [--check [--only-drift]]" >&2; exit 2 ;;
  esac
done
src="$(cd "$(dirname "$0")" && pwd)"
dest="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
if [ "$mode" = install ]; then mkdir -p "$dest"; fi
for d in "$src"/*/; do
  name=$(basename "$d")
  if [ "$mode" = check ]; then
    if [ ! -e "$dest/$name" ]; then
      if [ "$only_drift" = 0 ]; then echo "MISSING: $name (未配置。install.sh で配置)" >&2; rc=1; fi
    elif diff -rq -x __pycache__ "$d" "$dest/$name" >/dev/null; then
      echo "up-to-date: $name"
    else
      echo "DRIFT: $name  (正本 $d と $dest/$name が違う。diff -r で確認し、正本が正しければ FORCE=1 ./install.sh)" >&2; rc=1
    fi
    continue
  fi
  if [ ! -e "$dest/$name" ]; then
    cp -r "$d" "$dest/$name"; echo "installed: $name"
  elif diff -rq -x __pycache__ "$d" "$dest/$name" >/dev/null; then
    echo "up-to-date: $name"
  elif [ "${FORCE:-0}" = 1 ]; then
    tmp=$(mktemp -d "$dest/.install.XXXXXX")
    cp -r "$d" "$tmp/new" && mv "$dest/$name" "$tmp/old" && mv "$tmp/new" "$dest/$name" && rm -rf "$tmp"; echo "replaced: $name"
  else
    echo "SKIP(differs): $name  (差分は diff -r で確認。FORCE=1 で上書き)" >&2; rc=1
  fi
done
exit $rc
