#!/usr/bin/env bash
# repo-survey 補助: リポジトリの事実を安全・低コストに収集する(読み取りのみ。再帰は深さ2まで)。
# 使い方: survey.sh [dir]
set -u
cd "${1:-.}" || exit 1
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "gitリポジトリではありません"; exit 1; }
cd "$root"
echo "## git"; git branch --show-current; git status -sb | head -15; git log --oneline | head -5
echo "## top-level"; ls -1 | head -40
echo "## manifests (depth<=2)"
find . -maxdepth 2 \( -name package.json -o -name pyproject.toml -o -name requirements*.in -o -name requirements.txt -o -name go.mod -o -name Cargo.toml -o -name Makefile -o -name Dockerfile -o -name pytest.ini -o -name tsconfig.json -o -name '*.toml' \) -not -path '*/node_modules/*' -not -path './.git/*' | sort | head -40
echo "## CI"; ls .github/workflows 2>/dev/null
echo "## claude assets"; for d in CLAUDE.md .claude/settings.json .claude/skills .claude/hooks .claude/agents .claude/verify.json; do [ -e "$d" ] && echo "present: $d"; done
echo "## size (tracked files by ext)"; git ls-files | sed -n 's/.*\.\([A-Za-z0-9]*\)$/\1/p' | sort | uniq -c | sort -rn | head -8
echo "## entrypoints (heuristic)"; git ls-files | grep -E '(^|/)(main|app|server|unified_server|index|cli)\.(py|ts|tsx|js)$' | head -10
echo "## tests"; git ls-files | grep -Ec '(^|/)(tests?|__tests__)/|\.(test|spec)\.(ts|tsx|js)$|(^|/)test_.*\.py$'
echo "## debt markers"; git grep -nEI 'TODO|FIXME|HACK|XXX' -- ':!*.md' ':!*.lock' ':!*package-lock.json' | wc -l
