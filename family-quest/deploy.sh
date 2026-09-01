#!/usr/bin/env bash
# family-quest を本番ビルドして dist/ を更新する。
# unified_server は dist/ をディスク直読みで配信するため、ビルド完了 = デプロイ完了(再起動不要)。
# 通常は git pull 時に post-merge フックから、およびサーバー起動時に start_all.sh から
# 自動実行される。手動実行も可: ./deploy.sh
#
# 使い方:
#   ./deploy.sh             常にビルドする
#   ./deploy.sh --if-stale  dist/ が現在のHEADの family-quest ツリーからビルド済みなら
#                           何もせず終了する(冪等チェック)。
#
# 冪等チェックの仕組み:
#   ビルド成功時に「git rev-parse HEAD:family-quest」(family-quest ディレクトリの
#   ツリーハッシュ)を dist/.built-tree に記録する。--if-stale 時はこれと現在の
#   ハッシュを比較し、一致すればスキップする。git pull だけでなく git reset --hard
#   等どんな経路でチェックアウトが更新されても、次のサーバー起動/フック実行時に
#   ビルド漏れを検知できる(2026-09-01: reset --hard 経由の更新で post-merge フックが
#   発火せず、旧バンドルが新APIスキーマと不整合を起こした障害の再発防止)。
#   注意: 未コミットのローカル変更はツリーハッシュに反映されないため、開発中の
#   動作確認には従来どおり npm run dev または引数なしの ./deploy.sh を使うこと。
set -euo pipefail

cd "$(dirname "$0")"

# 現在のHEADにおける family-quest ディレクトリのツリーハッシュを返す。
# 取得できない場合(gitが無い等)は空文字を返し、呼び出し側で「常にビルド」に倒す。
current_tree_hash() {
    git rev-parse "HEAD:$(git rev-parse --show-prefix 2>/dev/null || echo family-quest)" 2>/dev/null || true
}

BUILT_TREE_FILE="dist/.built-tree"

if [[ "${1:-}" == "--if-stale" ]]; then
    current="$(current_tree_hash)"
    recorded="$(cat "$BUILT_TREE_FILE" 2>/dev/null || true)"
    if [[ -n "$current" && -n "$recorded" && "$current" == "$recorded" && -f dist/index.html ]]; then
        echo "[deploy] family-quest: dist/ は最新 (tree ${current:0:12})。ビルドをスキップします。"
        exit 0
    fi
    echo "[deploy] family-quest: dist/ が古いか未記録 (built='${recorded:-none}' head='${current:-unknown}')。再ビルドします..."
fi

echo "[deploy] family-quest: npm install..."
npm install --no-audit --no-fund

echo "[deploy] family-quest: build..."
npm run build

# ビルド成果物の最低限の検証
if [[ ! -f dist/index.html ]] || ! ls dist/assets/index-*.js >/dev/null 2>&1; then
    echo "[deploy] ERROR: dist/ にビルド成果物が見つかりません" >&2
    exit 1
fi

# ビルド元ツリーハッシュを記録 (--if-stale の判定材料)
built="$(current_tree_hash)"
if [[ -n "$built" ]]; then
    echo "$built" > "$BUILT_TREE_FILE"
else
    echo "[deploy] WARNING: gitツリーハッシュを取得できず、$BUILT_TREE_FILE を更新しません (--if-staleは常にビルドになります)" >&2
fi

echo "[deploy] family-quest: 完了 ($(date '+%Y-%m-%d %H:%M:%S'))"
