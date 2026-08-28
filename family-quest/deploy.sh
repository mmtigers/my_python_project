#!/usr/bin/env bash
# family-quest を本番ビルドして dist/ を更新する。
# unified_server は dist/ をディスク直読みで配信するため、ビルド完了 = デプロイ完了(再起動不要)。
# 通常は git pull 時に post-merge フックから自動実行される。手動実行も可: ./deploy.sh
set -euo pipefail

cd "$(dirname "$0")"

echo "[deploy] family-quest: npm install..."
npm install --no-audit --no-fund

echo "[deploy] family-quest: build..."
npm run build

# ビルド成果物の最低限の検証
if [[ ! -f dist/index.html ]] || ! ls dist/assets/index-*.js >/dev/null 2>&1; then
    echo "[deploy] ERROR: dist/ にビルド成果物が見つかりません" >&2
    exit 1
fi

echo "[deploy] family-quest: 完了 ($(date '+%Y-%m-%d %H:%M:%S'))"
