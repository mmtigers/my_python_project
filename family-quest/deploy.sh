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
#
# アトミックな差し替え (Issue #650):
#   vite は既定(build.emptyOutDir=true)で出力先を先に空にするため、以前は dist/ に直接
#   ビルドしていて、ビルド中の数十秒間およびビルド失敗時に dist/index.html が存在せず
#   unified_server が 404 を返す窓があった(post-merge / start_all.sh のコメントにある
#   「失敗時は旧 dist/ を配信し続ける」が成立していなかった)。現在は dist.next/ に
#   ビルドして検証し、成功した場合だけ dist/ と rename で入れ替える。unified_server は
#   dist/ をパスで参照するだけなので、rename 直後から新バンドルが配信される。
set -euo pipefail

cd "$(dirname "$0")"

# 現在のHEADにおける family-quest ディレクトリのツリーハッシュを返す。
# 取得できない場合(gitが無い等)は空文字を返し、呼び出し側で「常にビルド」に倒す。
current_tree_hash() {
    git rev-parse "HEAD:$(git rev-parse --show-prefix 2>/dev/null || echo family-quest)" 2>/dev/null || true
}

DIST_DIR="dist"
NEXT_DIR="dist.next"
PREV_DIR="dist.prev"
BUILT_TREE_FILE="$DIST_DIR/.built-tree"

if [[ "${1:-}" == "--if-stale" ]]; then
    current="$(current_tree_hash)"
    recorded="$(cat "$BUILT_TREE_FILE" 2>/dev/null || true)"
    if [[ -n "$current" && -n "$recorded" && "$current" == "$recorded" && -f "$DIST_DIR/index.html" ]]; then
        echo "[deploy] family-quest: dist/ は最新 (tree ${current:0:12})。ビルドをスキップします。"
        exit 0
    fi
    echo "[deploy] family-quest: dist/ が古いか未記録 (built='${recorded:-none}' head='${current:-unknown}')。再ビルドします..."
fi

# Node.js のバージョン確認 (Issue #650)。CI(test.yml の frontend ジョブ)は .nvmrc の版で
# ビルドしており、実機の Node がそれと異なるメジャーだと CI と異なる成果物になりうる。
# package.json の engines(">=20 <23" 形式)の範囲外なら明示的に失敗させ、.nvmrc と異なる
# メジャーなら警告する。engines を解釈できない場合はチェックをスキップする。
if command -v node >/dev/null 2>&1; then
    node_version="$(node -v 2>/dev/null || true)"   # 例: v20.19.0
    node_major="${node_version#v}"
    node_major="${node_major%%.*}"
    engines="$(node -p "require('./package.json').engines?.node ?? ''" 2>/dev/null || true)"
    if [[ "$node_major" =~ ^[0-9]+$ && "$engines" =~ \>=([0-9]+)[^0-9]*\<([0-9]+) ]]; then
        min_major="${BASH_REMATCH[1]}"
        max_major_exclusive="${BASH_REMATCH[2]}"
        if (( node_major < min_major || node_major >= max_major_exclusive )); then
            echo "[deploy] ERROR: Node.js $node_version は package.json の engines ($engines) の範囲外です。" >&2
            echo "[deploy]        .nvmrc の版 ($(cat .nvmrc 2>/dev/null || echo '?')) に合わせて Node.js を入れ替えてください。" >&2
            exit 1
        fi
    fi
    nvmrc_major="$(tr -d '[:space:]' < .nvmrc 2>/dev/null || true)"
    nvmrc_major="${nvmrc_major#v}"
    nvmrc_major="${nvmrc_major%%.*}"
    if [[ -n "$nvmrc_major" && "$node_major" != "$nvmrc_major" ]]; then
        echo "[deploy] WARNING: Node.js $node_version は .nvmrc (v$nvmrc_major、CI と同じ版) と異なるメジャーです。" >&2
    fi
else
    echo "[deploy] ERROR: node コマンドが見つかりません" >&2
    exit 1
fi

echo "[deploy] family-quest: npm ci..."
# Issue #489: CI(test.ymlのfrontendジョブ)と同じnpm ciを使い、package-lock.jsonを
# 厳密に守る。npm installだとpackage.jsonの範囲指定(^18.3.1等)を再解決して
# lockfileを書き換えてしまい、(1) CIが緑でも実機は別バージョンでビルドされうる、
# (2) 実機のgitツリーがdirtyになり次回のgit pullが失敗する、という2つの問題を招く。
npm ci --no-audit --no-fund

echo "[deploy] family-quest: build (-> $NEXT_DIR/)..."
rm -rf "$NEXT_DIR"
# `npm run build` は "tsc -b && vite build"。`--` 以降の引数は末尾の vite build に渡る。
npm run build -- --outDir "$NEXT_DIR"

# ビルド成果物の最低限の検証 (この時点では dist/ は一切触っていない)
if [[ ! -f "$NEXT_DIR/index.html" ]] || ! ls "$NEXT_DIR"/assets/index-*.js >/dev/null 2>&1; then
    echo "[deploy] ERROR: $NEXT_DIR/ にビルド成果物が見つかりません。dist/ は更新していません。" >&2
    rm -rf "$NEXT_DIR"
    exit 1
fi

# ビルド元ツリーハッシュを新しい成果物側に記録 (--if-stale の判定材料)
built="$(current_tree_hash)"
if [[ -n "$built" ]]; then
    echo "$built" > "$NEXT_DIR/.built-tree"
else
    echo "[deploy] WARNING: gitツリーハッシュを取得できず、.built-tree を記録しません (--if-staleは常にビルドになります)" >&2
fi

# アトミックに入れ替える。旧 dist/ は dist.prev/ に退避し、入れ替え成功後に削除する。
# (途中で失敗した場合は dist.prev/ が残るので手で戻せる: mv dist.prev dist)
rm -rf "$PREV_DIR"
if [[ -d "$DIST_DIR" ]]; then
    mv "$DIST_DIR" "$PREV_DIR"
fi
mv "$NEXT_DIR" "$DIST_DIR"
rm -rf "$PREV_DIR"

echo "[deploy] family-quest: 完了 ($(date '+%Y-%m-%d %H:%M:%S'))"
