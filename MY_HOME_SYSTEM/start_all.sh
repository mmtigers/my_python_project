#!/bin/bash

# ==========================================
# MY_HOME_SYSTEM 起動スクリプト (Systemd-Hybrid Fix)
# ==========================================

# ★修正1: 親ディレクトリ(develop)も含めないと "No module named 'MY_HOME_SYSTEM'" エラーになる
export PYTHONPATH="/home/masahiro/develop:/home/masahiro/develop/MY_HOME_SYSTEM"

PROJECT_DIR="/home/masahiro/develop/MY_HOME_SYSTEM"
QUEST_DIR="/home/masahiro/develop/family-quest"
cd "$PROJECT_DIR" || exit 1

# Pythonパス
if [ -f ".venv/bin/python3" ]; then
    PYTHON_EXEC=".venv/bin/python3"
else
    PYTHON_EXEC="python3"
fi

# ログディレクトリ
mkdir -p logs

# --- Phase 0: 徹底的なクリーンアップ ---
echo "--- Cleanup Old Processes ---"

# 停止対象プロセスのパターン一覧
# (旧 'scheduler.py' は実体 'scheduler_boot.py' と一致せず、再起動のたびに
#  古いschedulerが生き残って二重起動する原因になっていた。
#  存在しない 'bluetooth_monitor.py' の行は削除。)
# (#360: scheduler が起動した監視スクリプト(monitors/*.py)と、ライブ配信/VOD生成の
#  ffmpeg は旧世代が孤児化して残ると、新世代と同じ HLS パスへ二重書き込みしたり
#  古い設定で DB 書き込み・保持期間削除を続けたりするため、停止対象に含める。)
CLEANUP_TARGETS=(
  "unified_server.py"
  "camera_monitor.py"
  "scheduler_boot.py"
  "streamlit run"
  "python.*monitors/[a-z_]*\.py"
  "ffmpeg.*hls_streams"
)

# まずは優しく停止 (SIGTERM)
for target in "${CLEANUP_TARGETS[@]}"; do
  pkill -f "$target"
done

# プロセスが消えるまで最大5秒待機 (10秒は長いので短縮)
for i in {1..5}; do
  still_running=false
  for target in "${CLEANUP_TARGETS[@]}"; do
    if pgrep -f "$target" > /dev/null; then
      still_running=true
      break
    fi
  done
  if [ "$still_running" = false ]; then
    echo "✅ Old processes stopped."
    break
  fi
  echo "⏳ Waiting for shutdown... ($i/5)"
  sleep 1
done

# まだ生きていたら対象ごとに強制終了 (SIGKILL)
for target in "${CLEANUP_TARGETS[@]}"; do
  if pgrep -f "$target" > /dev/null; then
    echo "💀 Force killing: $target ..."
    pkill -9 -f "$target"
  fi
done

# --- Phase 1: NASマウント確認 ---
echo "--- Check NAS Mount ---"
MOUNT_POINT="/mnt/nas"
if command -v mountpoint >/dev/null 2>&1; then
  # autofsのアイドルアンマウント直後は、起動直後にアクセスしても自動マウントの
  # トリガーからマウント完了までに数秒かかることがある(config.pyの
  # verify_and_initialize_storageが遭遇するENOENTと同種の一過性の遅延)。
  # 1回チェックして即座に諦めるのではなく、パスへのアクセスで自動マウントを
  # トリガーしつつExponential Backoffで数回リトライする。
  MOUNT_WAIT=1
  mounted=false
  for i in 1 2 3 4 5; do
    ls "$MOUNT_POINT" >/dev/null 2>&1  # autofsの自動マウントをトリガー
    if mountpoint -q "$MOUNT_POINT"; then
      mounted=true
      break
    fi
    echo "⏳ NAS not mounted yet (attempt $i/5). Retrying in ${MOUNT_WAIT}s..."
    sleep "$MOUNT_WAIT"
    MOUNT_WAIT=$((MOUNT_WAIT * 2))
  done
  if [ "$mounted" = true ]; then
    echo "✅ NAS Mounted."
  else
    echo "⚠️ NAS is still NOT mounted after retries. Continuing anyway (app-level backoff/fallback will handle it)."
  fi
fi

# --- Phase 2: family-quest フロントエンドの鮮度チェック ---
# git pull 以外の経路(git reset --hard 等)でチェックアウトが更新されると
# post-merge フックが発火せず、dist/ が旧世代のままサーバーだけ新コードで
# 起動してAPIスキーマ不整合を起こすことがある(2026-09-01の障害)。
# サーバー起動前に必ず冪等チェックを通し、ビルド漏れをここで回収する。
# ビルド失敗でもサーバー起動は続行する(旧distを配信し続ける方がマシなため)。
echo "--- Ensure family-quest dist is fresh ---"
if ! bash "$QUEST_DIR/deploy.sh" --if-stale > logs/quest_deploy.log 2>&1; then
    echo "⚠️ family-quest build failed. Serving existing dist/. See logs/quest_deploy.log"
fi

# --- Phase 3: 初期化 & Webhook修正 ---
echo "--- Check & Fix Webhooks (Cloudflare Tunnel) ---"
$PYTHON_EXEC switchbot_webhook_fix.py > logs/webhook_fix.log 2>&1

# --- Phase 4: サーバー起動 (ここだけにする) ---
echo "--- Start Home System Server ---"
# unified_server.py が内部で scheduler_boot.py を起動します
# ★修正: '&'のみのバックグラウンド化はSSHログアウト時にシェルからSIGHUPが
# 送られて死ぬ余地があるため、nohupでSIGHUPを無視しdisownでジョブ管理からも外す
nohup $PYTHON_EXEC unified_server.py < /dev/null > logs/server_boot.log 2>&1 &
disown
echo "🚀 System started. Check logs/server_boot.log for details."

# ★修正: ダッシュボードは認証なしのため、外部公開せずローカルホストのみに限定する
# (必要な場合は信頼できるリバースプロキシ経由でアクセスすること)
nohup $PYTHON_EXEC -m streamlit run dashboard.py --server.port 8501 --server.address 127.0.0.1 < /dev/null > logs/dashboard_boot.log 2>&1 &
disown
echo "📊 Dashboard started."

echo "✅ All systems go!"