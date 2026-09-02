#!/bin/bash
# ==========================================
# ログ監視・自動調査スクリプト (claude_log_watchdog.sh)
# ==========================================
# ラズパイ上のcron/systemdタイマーから定期実行することを想定した雛形。
# 詳細設計は docs/runbooks/raspi_claude_log_monitoring.md を参照。
#
# ★重要: --permission-mode / --allowedTools の値は実装時点の想定であり未検証。
#   実機で `claude --help` / `claude -p --help` を実行し、実際に使える
#   フラグ名・値を確認してから使うこと。ここを確認せずに運用すると、
#   「ガードレールが効いていない」状態のまま動かしてしまう恐れがある。
#
# 動作:
#   1. journalctl・logs/*.log・ディスク/メモリ使用率をシェルのみでチェックする
#      一次チェックを行う（Claude Code CLIは呼ばない。APIコストゼロ）。
#   2. 異常を検知した場合のみ、Claude Code CLI (`claude -p`) をヘッドレス起動し、
#      本リポジトリのソースと突き合わせた原因調査・改修案の提示を行わせる。
#      --allowedTools は読み取り系コマンドと `gh issue create`/`gh pr create --draft`
#      のみに限定し、systemctl・rm・git push --force 等は一切許可しない。
#   3. 自動適用・自動再起動は行わない（GitHub Issue/Draft PR起票と通知のみ）。
#
# 前提（docs/runbooks/raspi_claude_log_monitoring.md の「必要な準備」参照）:
#   - Claude Code CLIがインストール済み
#   - 認証用の環境変数(CLAUDE_CODE_OAUTH_TOKEN または ANTHROPIC_API_KEY)が設定済み
#   - `gh` CLIがインストール・認証済み(Issue/PR作成用の最小権限トークン)
#   - WATCHDOG_NOTIFY_WEBHOOK_URL に通知先WebhookのURLが設定済み(任意)

set -euo pipefail

# ★ラズパイの実際の配置パスに合わせて変更すること(start_all.shのPROJECT_DIRと同様)
PROJECT_DIR="/home/masahiro/develop/my_python_project"
HOME_SYSTEM_DIR="$PROJECT_DIR/MY_HOME_SYSTEM"
MARKER_FILE="$HOME_SYSTEM_DIR/logs/.claude_watch_marker"
DISK_THRESHOLD_PERCENT=90
MEM_THRESHOLD_PERCENT=90

cd "$HOME_SYSTEM_DIR"
mkdir -p logs

# --- 前回チェック時刻の取得(初回はマーカーなし。1時間前を起点にする) ---
if [ -f "$MARKER_FILE" ]; then
  SINCE=$(cat "$MARKER_FILE")
else
  SINCE="1 hour ago"
fi
NOW_ISO=$(date -Iseconds)

# --- 一次チェック1: journalctlのERROR以上 ---
JOURNAL_ERRORS=$(journalctl -u home_system.service --since "$SINCE" -p err..emerg --no-pager 2>/dev/null || true)

# --- 一次チェック2: logs/*.log のERROR/CRITICAL(マーカー更新後のファイルのみ) ---
LOG_ERRORS=""
if [ -f "$MARKER_FILE" ]; then
  LOG_ERRORS=$(find logs -maxdepth 1 -name '*.log' -newer "$MARKER_FILE" -exec grep -E 'ERROR|CRITICAL' {} + 2>/dev/null || true)
fi

# --- 一次チェック3: ディスク/メモリ使用率 ---
DISK_PERCENT=$(df / --output=pcent | tail -1 | tr -dc '0-9')
MEM_PERCENT=$(free | awk '/Mem:/ {printf "%d", $3/$2*100}')

ANOMALY=false
REASON=""

if [ -n "$JOURNAL_ERRORS" ]; then
  ANOMALY=true
  REASON="${REASON}journalctlにERROR以上のログあり"$'\n'
fi
if [ -n "$LOG_ERRORS" ]; then
  ANOMALY=true
  REASON="${REASON}logs/*.logにERROR/CRITICALあり"$'\n'
fi
if [ "$DISK_PERCENT" -ge "$DISK_THRESHOLD_PERCENT" ]; then
  ANOMALY=true
  REASON="${REASON}ディスク使用率${DISK_PERCENT}%が閾値(${DISK_THRESHOLD_PERCENT}%)超過"$'\n'
fi
if [ "$MEM_PERCENT" -ge "$MEM_THRESHOLD_PERCENT" ]; then
  ANOMALY=true
  REASON="${REASON}メモリ使用率${MEM_PERCENT}%が閾値(${MEM_THRESHOLD_PERCENT}%)超過"$'\n'
fi

# --- マーカー更新(異常有無に関わらず、ここまでのチェック完了時刻として記録) ---
echo "$NOW_ISO" > "$MARKER_FILE"

if [ "$ANOMALY" = false ]; then
  echo "[$(date)] 異常なし。Claude Code CLIは呼び出さない。"
  exit 0
fi

echo "[$(date)] 異常を検知。詳細調査をClaude Code CLIに依頼します。"
echo "$REASON"

# --- 詳細調査: Claude Code CLIをヘッドレス起動 ---
PROMPT=$(cat <<EOF
以下の一次チェックで異常を検知した。原因を調査し、改修案を提示してほしい。

【検知内容】
${REASON}

【journalctlの該当ログ】
${JOURNAL_ERRORS}

【logs/*.logの該当行】
${LOG_ERRORS}

【ディスク使用率】${DISK_PERCENT}%
【メモリ使用率】${MEM_PERCENT}%

このリポジトリ(MY_HOME_SYSTEM以下)のソースコードと突き合わせて原因を特定し、
改修が必要であれば具体的な修正案(diff)を作成した上で、
'gh issue create' または 'gh pr create --draft' でGitHub上に起票せよ。
絶対にsystemctl restart等のサービス再起動や、コードの自動適用(直接pushでの反映)は行わないこと。
提案のみに留めること。
最後に、起票したIssue/PRのURLを1行で出力せよ。
EOF
)

# ★以下の --permission-mode / --allowedTools は未検証。実機で確認・修正すること。
RESULT=$(claude -p "$PROMPT" \
  --permission-mode dontAsk \
  --allowedTools "Read,Grep,Glob,Bash(git log*),Bash(git diff*),Bash(git status*),Bash(gh issue create*),Bash(gh pr create*),Bash(gh pr diff*)" \
  --output-format json 2>&1 | jq -r '.result // .')

echo "$RESULT"

# --- 通知(Discord/LINE Webhookはnotification_service.pyと同じURLを再利用する想定) ---
if [ -n "${WATCHDOG_NOTIFY_WEBHOOK_URL:-}" ]; then
  curl -fsS -X POST "$WATCHDOG_NOTIFY_WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "{\"content\": \"[ラズパイ監視] 異常検知・調査完了:\n${RESULT}\"}" \
    || echo "[$(date)] 通知送信に失敗しました" >&2
fi
