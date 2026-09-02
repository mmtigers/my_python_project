#!/bin/bash
# ==========================================
# 異常時自動調査スクリプト (claude_investigate.sh)
# ==========================================
# ラズパイ監視 層2 (Issue #339)。層1の monitors/health_watch.py が異常を検知した
# ときに、config.HEALTH_WATCH_INVESTIGATE_HOOK 経由で起動される調査専用スクリプト。
# 一次チェックは行わない(層1の責務)。標準入力で受け取った異常サマリをプロンプトに
# 埋めて Claude Code CLI (`claude -p`) をヘッドレス起動し、リポジトリのソースと
# 突き合わせた原因調査と GitHub Issue/Draft PR 起票を行わせる。
# 詳細設計・ガードレールは docs/runbooks/raspi_claude_log_monitoring.md を参照。
#
# ★重要(未検証フラグ): --permission-mode / --allowedTools / --max-turns /
#   --output-format の各フラグ名・値はCLIのバージョンによって変わりうる。
#   運用開始前に必ず実機で `claude --help` / `claude -p --help` を確認し、
#   実際に使えるフラグへ合わせること。ここを確認せずに動かすと
#   「ガードレールが効いていない」状態になりうる。
#
# ガードレール(runbook準拠):
#   - 自動適用・自動デプロイ・systemctl restart は行わない。Issue/Draft PR起票と
#     通知のみ。--allowedTools を読み取り系 + gh issue create / gh pr create --draft
#     に機械的に制限し、--dangerously-skip-permissions は絶対に使わない。
#   - 多重起動防止: flock (DDDバッチと同じロックファイル方式)
#   - 暴走対策: timeout + --max-turns
#
# 入力: 標準入力に異常サマリ(health_watch.pyの検知内容)
# 環境変数:
#   CLAUDE_INVESTIGATE_PROJECT_DIR : リポジトリのパス (既定: /home/masahiro/develop/my_python_project)
#   CLAUDE_INVESTIGATE_TIMEOUT_SEC : claude -p 全体のタイムアウト秒 (既定: 900)
#   CLAUDE_INVESTIGATE_MAX_TURNS   : --max-turns の値 (既定: 30)
#   CLAUDE_INVESTIGATE_DRY_RUN     : 1でドライラン(gh起票なし・調査結果の通知のみ)。
#                                    導入初期はこのモードで様子を見ることを推奨
#   WATCHDOG_NOTIFY_WEBHOOK_URL    : 調査結果の通知先Webhook (任意。notification_service
#                                    と同じDiscord WebhookのURLを再利用し新経路を増やさない)
#
# 前提(runbookの「準備」参照。ラズパイ側で実施):
#   - Claude Code CLI がインストール済みで、CLAUDE_CODE_OAUTH_TOKEN が設定済み
#   - gh CLI が Issue/PR作成の最小権限トークンで認証済み (ドライラン運用中は不要)

set -euo pipefail

PROJECT_DIR="${CLAUDE_INVESTIGATE_PROJECT_DIR:-/home/masahiro/develop/my_python_project}"
HOME_SYSTEM_DIR="$PROJECT_DIR/MY_HOME_SYSTEM"
LOCK_FILE="$HOME_SYSTEM_DIR/logs/.claude_investigate.lock"
TIMEOUT_SEC="${CLAUDE_INVESTIGATE_TIMEOUT_SEC:-900}"
MAX_TURNS="${CLAUDE_INVESTIGATE_MAX_TURNS:-30}"
DRY_RUN="${CLAUDE_INVESTIGATE_DRY_RUN:-0}"

cd "$HOME_SYSTEM_DIR"
mkdir -p logs

# --- 多重起動防止 (flock。前回の調査が走行中なら即終了する) ---
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date)] 前回の調査が実行中のためスキップします。"
  exit 0
fi

# --- 層1から標準入力で渡される異常サマリ ---
ANOMALY_SUMMARY=$(cat)
if [ -z "$ANOMALY_SUMMARY" ]; then
  echo "[$(date)] 異常サマリが空のため終了します(このスクリプトは層1経由で起動される想定)。" >&2
  exit 1
fi

echo "[$(date)] 自動調査を開始します。"
echo "$ANOMALY_SUMMARY"

# --- 調査プロンプトと許可ツール ---
if [ "$DRY_RUN" = "1" ]; then
  # ドライラン: gh起票なし。調査・提案のみ(導入初期の観察運用)
  ALLOWED_TOOLS="Read,Grep,Glob,Bash(git log*),Bash(git diff*),Bash(git status*),Bash(journalctl*),Bash(df*),Bash(free*),Bash(tail*)"
  REPORTING_INSTRUCTION="今回はドライラン運用のため、GitHubへの起票は行わず、調査結果と改修案(diff案)を出力にまとめよ。"
else
  ALLOWED_TOOLS="Read,Grep,Glob,Bash(git log*),Bash(git diff*),Bash(git status*),Bash(journalctl*),Bash(df*),Bash(free*),Bash(tail*),Bash(gh issue create*),Bash(gh pr create --draft*),Bash(gh pr diff*)"
  REPORTING_INSTRUCTION="改修が必要であれば具体的な修正案(diff)を作成した上で、'gh issue create' または 'gh pr create --draft' でGitHub上に起票せよ。最後に、起票したIssue/PRのURLを1行で出力せよ。"
fi

PROMPT=$(cat <<EOF
ラズパイ一次ヘルスチェック(monitors/health_watch.py)が以下の異常を検知した。
このリポジトリ(MY_HOME_SYSTEM以下)のソースコードとログを突き合わせて原因を特定してほしい。

【検知内容】
${ANOMALY_SUMMARY}

必要に応じて journalctl や logs/*.log の読み取りで裏取りをすること。
${REPORTING_INSTRUCTION}
絶対に systemctl restart 等のサービス操作・コードの自動適用(push等)は行わないこと。提案のみに留めること。
EOF
)

# --- Claude Code CLI をヘッドレス起動 ---
# ★フラグは未検証(冒頭の注意参照)。timeoutはSIGTERM後10秒でSIGKILLに昇格させる。
set +e
RESULT=$(timeout --kill-after=10 "$TIMEOUT_SEC" claude -p "$PROMPT" \
  --permission-mode dontAsk \
  --allowedTools "$ALLOWED_TOOLS" \
  --max-turns "$MAX_TURNS" \
  --output-format json 2>&1 | jq -r '.result // .')
CLAUDE_EXIT=$?
set -e

if [ "$CLAUDE_EXIT" -ne 0 ]; then
  RESULT="claude -p の実行に失敗しました (exit=${CLAUDE_EXIT}。タイムアウト${TIMEOUT_SEC}秒 or CLI/認証設定を確認): ${RESULT}"
fi

echo "[$(date)] 調査完了 (exit=${CLAUDE_EXIT}):"
echo "$RESULT"

# --- 通知 (notification_service.pyと同じDiscord WebhookのURLを再利用する想定) ---
if [ -n "${WATCHDOG_NOTIFY_WEBHOOK_URL:-}" ]; then
  # Discordのcontent上限(2000字)に収まるよう調査結果は先頭1500字に切り詰める
  SNIPPET=$(printf '%s' "$RESULT" | head -c 1500)
  PAYLOAD=$(jq -n --arg content "[ラズパイ監視] 異常検知・自動調査の結果:
${SNIPPET}" '{content: $content}')
  curl -fsS -X POST "$WATCHDOG_NOTIFY_WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
    || echo "[$(date)] 通知送信に失敗しました" >&2
fi

exit "$CLAUDE_EXIT"
