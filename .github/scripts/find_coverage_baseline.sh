#!/bin/bash
# カバレッジのラチェット比較元(master の直近成功 run)を探す。
#
# test.yml の test / frontend の両ジョブから呼ばれる。見つかった run ID を
# $GITHUB_OUTPUT の `run_id` に書き、見つからなければ空にする(呼び出し側は
# run_id が空なら成果物のダウンロードをスキップし、固定閾値だけがゲートになる)。
#
# ## なぜスクリプトに切り出しているか
#
# 2026-09-21(PR #839)に、この処理が**11日前の run を比較元に選ぶ**事故が起きた。
# backend は master の実測 86.00% に対して 74.76%、frontend は 78.64% に対して
# 47.22% と比較しており、「master 比 0.5pt の低下で失敗」というラチェットが
# 実際には 11〜31pt の低下まで通す状態だった。当時は2つのジョブに同じ数行が
# コピーされていて、どちらも同じように壊れていた。
#
# ## 原因について分かっていること
#
# PR #839 では「`?branch=master&status=success&per_page=1` が直近の run を返さない」
# ことが原因だと考えて引き方を変えたが、その後 API を直接叩いて確認したところ
# **この前提は誤り**だった:
#
#   - `?branch=master&status=success&per_page=1` は正しく最新 run を返す
#   - `per_page=100` の並びも created_at の降順で崩れていない
#   - 問題の run 34475282120 はその一覧の 96 番目に位置していた
#   - 5回連続で引いても結果は揺れない
#
# つまりクエリの組み立ての不具合ではなく、事故当時に API が古い結果を返した
# (一過性のもの)と考えるのが妥当である。引き方を変えるだけでは再発を防げない。
#
# ## そのため、原因に依存しない防御を2つ置く
#
#   1. 並びをサーバーに任せず、jq 側で created_at の降順に並べ替えてから先頭を採る
#   2. 選ばれた比較元が古すぎる場合に ::warning:: を出す。黙って誤った値と比較する
#      のではなく、ログを見れば分かる状態にする(事故に気づけたのはログに run ID が
#      出ていたからで、日時と経過日数まで出ていれば一目で分かる)
#
# 詳細な記録は docs/runbooks/ci_coverage_ratchet.md を参照。
set -euo pipefail

# 比較元が何日前までなら正常とみなすか。master が動いていれば通常は当日〜1日前。
STALE_AFTER_DAYS="${STALE_AFTER_DAYS:-3}"
# 取得件数。master が連続で失敗していても遡れるよう、1件ではなく複数取る。
PER_PAGE="${PER_PAGE:-20}"

repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY が未設定です}"

baseline="$(gh api \
  "repos/${repo}/actions/workflows/test.yml/runs?branch=master&status=completed&per_page=${PER_PAGE}" \
  --jq '[.workflow_runs[] | select(.conclusion == "success")]
        | sort_by(.created_at) | reverse | .[0] // empty
        | "\(.id) \(.created_at)"')"

if [ -z "${baseline}" ]; then
  echo "baseline run: (none)"
  echo "run_id=" >> "${GITHUB_OUTPUT:-/dev/null}"
  exit 0
fi

run_id="${baseline%% *}"
created="${baseline#* }"

echo "run_id=${run_id}" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "baseline run: ${run_id} ${created}"

# 経過日数。created_at のパースに失敗したら警告だけ出して続行する
# (比較元が取れている以上、ここで失敗してジョブを落とす価値は無い)。
if ! created_epoch="$(date -u -d "${created}" +%s 2>/dev/null)"; then
  echo "::warning::[coverage-ratchet] 比較元の created_at を解釈できませんでした: ${created}"
  exit 0
fi
age_days=$(( ( $(date -u +%s) - created_epoch ) / 86400 ))
echo "baseline age: ${age_days} 日"

if [ "${age_days}" -gt "${STALE_AFTER_DAYS}" ]; then
  echo "::warning::[coverage-ratchet] 比較元の master run が ${age_days} 日前のものです" \
       "(run ${run_id}, ${created})。master が ${STALE_AFTER_DAYS} 日以上更新されていない" \
       "なら正常ですが、そうでなければ API が古い結果を返している可能性があります" \
       "(2026-09-21 の事故と同じ状況)。比較値が master の実測とかけ離れていないか" \
       "確認してください。詳細: docs/runbooks/ci_coverage_ratchet.md"
fi
