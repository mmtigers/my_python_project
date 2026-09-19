#!/bin/bash
# ==========================================
# アプリポート(8000)の接続元制限 (firewall_apply.sh)
# ==========================================
# 2026-09-19 の実機棚卸しで、ラズパイ本体にファイアウォールが一切無い
# (iptables の INPUT/FORWARD/OUTPUT が全て ACCEPT、ufw 未導入)ことを確認した。
# CLAUDE.md は「外部アクセス制御はエッジの Cloudflare Access に委譲する」設計の前提として
# 「オリジンへの直接到達が Cloudflare 経由に限定されていること(ルーター/FW側の設定)」を
# 挙げているが、実際にはルーター設定だけに依存していた。オーナー判断で、
# unified_server.py(0.0.0.0:8000)への接続元を次に限定する:
#
#   - loopback(127.0.0.0/8): cloudflared はトンネル(外向き接続)で localhost:8000 に
#     中継するため、外部からの Webhook(SwitchBot/LINE/Alexa)もここから来る
#     (uvicorn のアクセスログに外部IPが "<ip>:0" で出るのは、既定で 127.0.0.1 からの
#     転送ヘッダーだけを信用する uvicorn がそれを採用した印で、TCP の接続元は localhost)
#   - 直結サブネット(`ip -4 route show scope link` から実行時に取得): 家族の端末
#   - Tailscale(100.64.0.0/10)
#
# 対象は 8000番だけで、SSH・Samba 等の他のポートには一切触れない。
# LAN のアドレスはリポジトリに書かない方針(Issue #663)のため、実行時に検出する。
# 直結サブネットを1つも検出できない場合(起動直後でネットワークが未確立等)は、
# 家族の端末を締め出す事故を避けるため制限を掛けずに終了する(fail-open)。
#
# 使い方(root で実行。deploy/systemd/home_firewall.service から起動時に呼ばれる):
#   firewall_apply.sh            ルールを適用(何度実行しても同じ状態になる)
#   firewall_apply.sh --remove   ルールを外す(切り戻し)
#
# テスト用に IPTABLES / IP_CMD 環境変数でコマンドを差し替えられる。

set -euo pipefail

IPTABLES="${IPTABLES:-iptables}"
IP_CMD="${IP_CMD:-ip}"
APP_PORT="${APP_PORT:-8000}"
CHAIN="HOME_APP_${APP_PORT}"
TAILSCALE_CIDR="100.64.0.0/10"

log() {
    echo "[firewall_apply] $*"
}

remove_rules() {
    while "$IPTABLES" -C INPUT -p tcp --dport "$APP_PORT" -j "$CHAIN" 2>/dev/null; do
        "$IPTABLES" -D INPUT -p tcp --dport "$APP_PORT" -j "$CHAIN"
    done
    if "$IPTABLES" -L "$CHAIN" -n >/dev/null 2>&1; then
        "$IPTABLES" -F "$CHAIN"
        "$IPTABLES" -X "$CHAIN"
    fi
    log "ポート ${APP_PORT} の接続元制限を外しました"
}

if [ "${1:-}" = "--remove" ]; then
    remove_rules
    exit 0
fi

mapfile -t LAN_CIDRS < <("$IP_CMD" -o -4 route show scope link | awk '{print $1}' | sort -u)
if [ "${#LAN_CIDRS[@]}" -eq 0 ]; then
    # 締め出し事故を避けるため、既存の制限も外して素通しにする
    log "警告: 直結サブネットを検出できませんでした。ポート ${APP_PORT} を制限せずに終了します"
    remove_rules
    exit 0
fi

# チェーンを作り直す(-N は既存なら失敗するので無視し、-F で中身を空にしてから積む)
"$IPTABLES" -N "$CHAIN" 2>/dev/null || true
"$IPTABLES" -F "$CHAIN"
"$IPTABLES" -A "$CHAIN" -s 127.0.0.0/8 -j RETURN
for cidr in "${LAN_CIDRS[@]}"; do
    "$IPTABLES" -A "$CHAIN" -s "$cidr" -j RETURN
done
"$IPTABLES" -A "$CHAIN" -s "$TAILSCALE_CIDR" -j RETURN
# 落とした接続は kernel ログに残す(毎分5件まで)。想定外の遮断の調査用
"$IPTABLES" -A "$CHAIN" -m limit --limit 5/min -j LOG --log-prefix "${CHAIN} DROP: "
"$IPTABLES" -A "$CHAIN" -j DROP

# INPUT からの入口は1本だけにする(再実行しても重複させない)
if ! "$IPTABLES" -C INPUT -p tcp --dport "$APP_PORT" -j "$CHAIN" 2>/dev/null; then
    "$IPTABLES" -I INPUT 1 -p tcp --dport "$APP_PORT" -j "$CHAIN"
fi

log "ポート ${APP_PORT} を loopback / ${LAN_CIDRS[*]} / ${TAILSCALE_CIDR} に限定しました"
