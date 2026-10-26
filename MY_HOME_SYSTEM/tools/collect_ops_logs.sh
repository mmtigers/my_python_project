#!/bin/bash
# 実機の運用ログ調査用に、リポジトリのログ(logs/*.log)だけでは見えない情報をまとめて出力する。
# Issue #1042: nvr-*.service・cloudflared・journalctl(home_system.service・カーネルのOOM)・
# crontab(リポジトリとの差分)・生存ログ(heartbeat)は、従来の調査用ログに含まれておらず判定できなかった。
#
# 使い方(実機で。読み取り専用で、何も変更・再起動しない):
#   tools/collect_ops_logs.sh            # 直近24時間
#   tools/collect_ops_logs.sh "-6h"      # journalctl の --since に渡せる形式(例: "-6h", "today")
#   tools/collect_ops_logs.sh > ~/ops_logs.txt
#
# 認証情報を含みうる行(Webhook URL・RTSP の user:pass@・token/password/secret の値)は
# マスクして出力する。それでも貼り付けや共有の前に目視で確認すること。

SINCE="${1:--24h}"
# --since に使う文字列は journalctl へ引数としてそのまま渡す(シェルには解釈させない)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_DIR/.." && pwd)"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
TAIL_LINES="${TAIL_LINES:-200}"

mask() {
    sed -E \
        -e 's#(https?://[^/[:space:]]*(discord|discordapp)\.com/api/webhooks/[0-9]+/)[^[:space:]"'\'')]+#\1<redacted>#g' \
        -e 's#(rtsp://[^:/@[:space:]]+:)[^@[:space:]]+@#\1***@#g' \
        -e 's#((token|password|passwd|secret|api[_-]?key)[A-Za-z_]*[[:space:]]*[=:][[:space:]]*)[^[:space:]"'\'',]+#\1***#Ig'
}

section() {
    printf '\n===== %s =====\n' "$1"
}

journal() {
    # $1=見出し、残り=journalctl の引数
    local title="$1"
    shift
    section "$title"
    if ! command -v journalctl >/dev/null 2>&1; then
        echo "(journalctl が無い環境)"
        return
    fi
    journalctl "$@" --since "$SINCE" --no-pager 2>&1 | tail -n "$TAIL_LINES" | mask
}

section "collect_ops_logs: $(date '+%Y-%m-%d %H:%M:%S %z') since=$SINCE host=$(hostname)"

# 1. nvr-*.service(常時録画: NAS 書き込み失敗・ffmpeg の再起動)
for cam in entrance garden parking; do
    section "systemctl status nvr-$cam.service"
    systemctl status "nvr-$cam.service" --no-pager 2>&1 | head -n 12 | mask
    journal "journalctl nvr-$cam.service(警告以上と再起動)" -u "nvr-$cam.service" -p warning
done

# 2. cloudflared(エッジ到達性)
journal "journalctl cloudflared(警告以上)" -u cloudflared -p warning

# 3. home_system.service(再起動理由・クラッシュ)
journal "journalctl home_system.service(警告以上)" -u home_system.service -p warning
section "home_system.service の起動履歴(Started/Stopped/Main process exited)"
if command -v journalctl >/dev/null 2>&1; then
    journalctl -u home_system.service --since "$SINCE" --no-pager 2>&1 \
        | grep -E "Started |Stopped |Main process exited|Failed with result|Scheduled restart" | tail -n 30 | mask
fi

# 4. カーネルの OOM・電圧低下(プロセスが黙って落ちた理由)
section "カーネルログ(OOM killer・電圧低下・NASマウントのエラー)"
if command -v journalctl >/dev/null 2>&1; then
    journalctl -k --since "$SINCE" --no-pager 2>&1 \
        | grep -iE "out of memory|oom-kill|killed process|under-voltage|cifs|i/o error" | tail -n 30 | mask
fi

# 5. crontab(実機とリポジトリの差分)
section "crontab -l と deploy/cron/crontab の差分"
if command -v crontab >/dev/null 2>&1 && [ -f "$REPO_ROOT/deploy/cron/crontab" ]; then
    if diff <(crontab -l 2>/dev/null) "$REPO_ROOT/deploy/cron/crontab" >/dev/null 2>&1; then
        echo "同一"
    else
        diff <(crontab -l 2>/dev/null) "$REPO_ROOT/deploy/cron/crontab" | head -n 40 | mask
    fi
else
    echo "(crontab コマンドまたは deploy/cron/crontab が無い環境)"
fi

# 6. 生存ログ(heartbeat)と、無音で動く監視の最終実行(ローテーション済みも含める)
section "生存ログ(heartbeat)の直近"
if ls "$LOG_DIR"/home_system.log* >/dev/null 2>&1; then
    zgrep -h "heartbeat" "$LOG_DIR"/home_system.log* 2>/dev/null | sort | tail -n 12 | mask
else
    echo "(ログが無い: $LOG_DIR)"
fi
section "生存ログの状態ファイル(data/heartbeat/*.txt)"
for f in "$PROJECT_DIR"/data/heartbeat/*.txt; do
    [ -e "$f" ] || { echo "(状態ファイルなし)"; break; }
    printf '%s: %s\n' "$(basename "$f")" "$(cat "$f")"
done

# 7. health_watch の直近
section "health_watch.log の末尾"
tail -n 12 "$LOG_DIR/health_watch.log" 2>/dev/null | mask || echo "(ログなし)"
