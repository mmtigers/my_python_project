#!/bin/bash

# ==========================================
# MY_HOME_SYSTEM 起動スクリプト (Systemd-Hybrid Fix)
# ==========================================
# 使い方:
#   ./start_all.sh            Phase 0〜4 をすべて実行する(旧プロセス掃除 → 前処理 →
#                             unified_server.py を nohup でバックグラウンド起動。ダッシュボードは
#                             同プロセスが配信するため個別の起動は無い)。
#                             手動運用・開発用の経路。**systemd のユニットが有効な実機では拒否する**
#                             (下の「手動起動ガード」参照。Issue #824)。
#   ./start_all.sh --prepare  Phase 0〜3(前処理)だけを実行し、サーバー本体は起動しない。
#                             deploy/systemd/home_system.service の ExecStartPre から呼ばれる経路で、
#                             unified_server.py 本体は systemd が ExecStart でフォアグラウンド起動する
#                             (Type=simple + Restart=on-failure。クラッシュ時に systemd が自動復旧する。
#                             Issue #646: 以前は oneshot + nohup/disown でサーバーが systemd の管理外に
#                             あり、落ちても通知のみで人手復旧だった)。
#                             ダッシュボードは#829でStreamlit版(別プロセス・別ユニット)を廃止し、
#                             unified_server.py 自身が配信するようになったため、この経路での
#                             個別の起動・掃除は不要になった。
PREPARE_ONLY=false
if [ "${1:-}" = "--prepare" ]; then
    PREPARE_ONLY=true
fi

# --- 手動起動ガード (Issue #824) ---
# systemd がサーバーを管理している実機でフルモード(引数なし)を実行すると、
#   1. 下の Phase 1 の掃除が systemd 管理下のプロセスへ SIGTERM を送る
#   2. SIGTERM による終了は systemd から見て「正常終了」なので Restart=on-failure は再起動せず、
#      home_system.service は inactive になる
#   3. Phase 4 が nohup で起動し直し、プロセスは systemd の管理外(親 PID 1 の孤児)になる
# という経路で **#646 の自動復旧が黙って止まる**。サイトは孤児が応答するので普通に動いて見え、
# 誰も気づけない(2026-09-21 09:54 に実際に起き、約1時間半 NRestarts=344 のまま放置された)。
# runbook にも「引数なしで実行しないこと」と書いてあったが防げなかったため、コードで拒否する。
#
# 判定は `is-enabled`(= このホストは systemd が面倒を見る設定になっている)で行う。
# `is-active` だと、既にこの事故で inactive に落ちた状態から再度叩いたときに素通りしてしまう。
# systemd が無い環境(開発機・CI)やユニットを disable した実機では従来どおり動く。
# 意図して手動起動する場合だけ ALLOW_MANUAL_START=1 で上書きできる。
# ここは掃除(Phase 1)より前に置くこと。後ろに置くと拒否した時点で既にプロセスを止めている。
if [ "$PREPARE_ONLY" != true ] && command -v systemctl >/dev/null 2>&1; then
    managed_unit="home_system.service"
    if systemctl is-enabled --quiet "$managed_unit" 2>/dev/null; then
        if [ "${ALLOW_MANUAL_START:-}" = "1" ]; then
            printf '⚠️  %s は systemd で有効ですが、ALLOW_MANUAL_START=1 のため手動起動を続行します。\n' "$managed_unit" >&2
            printf '    起動後のプロセスは systemd の管理外になり、落ちても自動再起動されません。\n' >&2
        else
            printf '❌ %s が systemd で有効なため、start_all.sh のフルモード(引数なし)は実行しません。\n' "$managed_unit" >&2
            printf '   このまま実行すると systemd 管理下のプロセスを止めて nohup で起動し直すため、\n' >&2
            printf '   サービスが inactive になり、以後は落ちても自動再起動されなくなります (Issue #824)。\n' >&2
            printf '\n' >&2
            printf '   再起動したい場合:\n' >&2
            printf '     sudo systemctl restart home_system.service\n' >&2
            printf '\n' >&2
            printf '   それでも手動で起動したい場合(非推奨):\n' >&2
            printf '     ALLOW_MANUAL_START=1 %s\n' "$0" >&2
            exit 1
        fi
    fi
fi
# --- end 手動起動ガード ---

# ★修正1: 親ディレクトリ(develop)も含めないと "No module named 'MY_HOME_SYSTEM'" エラーになる
export PYTHONPATH="/home/masahiro/develop:/home/masahiro/develop/MY_HOME_SYSTEM"

DEVELOP_ROOT="/home/masahiro/develop"
PROJECT_DIR="$DEVELOP_ROOT/MY_HOME_SYSTEM"
QUEST_DIR="$DEVELOP_ROOT/family-quest"
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
# 監視スクリプトのパターンは scheduler_boot.py(TASKS)が起動するものだけを列挙する。以前の
# "python.*monitors/[a-z_]*\.py" は、systemd の network_logger.service や cron 起動の
# health_watch.py / daily_timelapse_job.py(ffmpeg を伴い数分〜数十分走る) / log_analyzer.py
# まで巻き添えで SIGTERM していた(サービス再起動のたびにタイムラプス生成が途中で殺され、
# network_logger は Restart=always で1周期分欠損)。scheduler_boot.py の TASKS を変更したら
# ここも合わせて更新すること(tests/test_start_all_sh.py が両者の整合を検証する)。
CLEANUP_TARGETS=(
  "unified_server.py"
  "camera_monitor.py"
  "scheduler_boot.py"
  "python.*monitors/(switchbot_power_monitor|nature_remo_monitor|server_watchdog|tv_lock_monitor|memory_monitor|nas_monitor|routine_deadline_job)\.py"
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

# --- Phase 1.5: Python依存関係の鮮度チェック ---
# requirements.txt が変わったのに .venv が追従していないと ImportError で
# 起動に失敗する。family-quest の deploy.sh --if-stale と同じ冪等チェックを
# バックエンドにも適用する。ビルド失敗でもサーバー起動は続行する
# (旧依存で動かす方が停止よりマシ。deploy.sh と同じ判断)。
#
# Issue #736 (AUDIT-006): 対象は MY_HOME_SYSTEM/requirements.txt だけでなく
# DDD/requirements.txt も含める。deploy/cron/crontab は DDD のスクリプトも
# MY_HOME_SYSTEM/.venv の python で実行している(run_task.sh は
# "${PROJECT_ROOT}/.venv/bin/python3" を使い、newface_monitor.py の行は
# .../MY_HOME_SYSTEM/.venv/bin/python を明示している)のに、この鮮度チェックは
# MY_HOME_SYSTEM 側しか見ていなかった。そのため yt-dlp / curl_cffi は
# 「過去に誰かが手で pip install した」痕跡としてしか .venv に存在せず、
# .venv を作り直す・新しいホストへ移る と DDD のバッチが無音で失敗する状態だった
# (失敗は run_task.sh のログに残るだけで通知されない。Issue #751 も参照)。
# 単一 venv を共有しているという実態をここで明示する。
echo "--- Check Python dependencies freshness ---"
REQ_HASH_FILE=".venv/.requirements-sha256"
REQ_FILES=("requirements.txt" "$DEVELOP_ROOT/DDD/requirements.txt")
existing_reqs=()
for req in "${REQ_FILES[@]}"; do
  [ -f "$req" ] && existing_reqs+=("$req")
done
if [ ${#existing_reqs[@]} -gt 0 ]; then
  # 複数ファイルを連結した内容のハッシュ。どれか1つでも変われば再インストールする。
  current_req="$(cat "${existing_reqs[@]}" | sha256sum | cut -d' ' -f1)"
  recorded_req="$(cat "$REQ_HASH_FILE" 2>/dev/null || true)"
  if [ "$current_req" != "$recorded_req" ]; then
    echo "--- requirements changed: updating .venv ---"
    : > logs/pip_install.log
    install_ok=true
    for req in "${existing_reqs[@]}"; do
      echo "=== pip install -r $req ===" >> logs/pip_install.log
      if ! "$PYTHON_EXEC" -m pip install -r "$req" >> logs/pip_install.log 2>&1; then
        install_ok=false
      fi
    done
    if [ "$install_ok" = true ]; then
      # 全ファイルの install が成功したときだけハッシュを記録する
      # (片方だけ成功した状態を「追従済み」にすると、次回以降リトライされない)。
      echo "$current_req" > "$REQ_HASH_FILE"
      echo "✅ Dependencies updated."
    else
      echo "⚠️ pip install failed. Starting with existing .venv. See logs/pip_install.log" >&2
    fi
  fi
fi

# --- Phase 1.6: git フックの登録 (core.hooksPath) ---
# family-quest の post-merge フック(git pull 後の deploy.sh --if-stale 自動実行)は
# 以前 .git/hooks/post-merge にローカル設置されていて git 管理外だったため、
# リポジトリを clone し直すたびに手で再設置が必要だった。リポジトリ管理の
# deploy/git-hooks/ を core.hooksPath として登録し(冪等)、再設置作業をなくす。
# 注意: core.hooksPath を設定すると .git/hooks/ 配下のフックは実行されなくなる
# (追加のフックは deploy/git-hooks/ に置いてコミットすること)。
echo "--- Register git hooks (core.hooksPath) ---"
HOOKS_DIR="$DEVELOP_ROOT/deploy/git-hooks"
if [ -d "$HOOKS_DIR" ] && git -C "$DEVELOP_ROOT" rev-parse --git-dir > /dev/null 2>&1; then
  if [ "$(git -C "$DEVELOP_ROOT" config --get core.hooksPath)" != "$HOOKS_DIR" ]; then
    if git -C "$DEVELOP_ROOT" config core.hooksPath "$HOOKS_DIR"; then
      echo "✅ core.hooksPath = $HOOKS_DIR"
    else
      echo "⚠️ Failed to set core.hooksPath. post-merge hook may not run on git pull." >&2
    fi
  fi
else
  echo "⚠️ $HOOKS_DIR not found or $DEVELOP_ROOT is not a git repository. Skipping hook registration." >&2
fi

# --- Phase 2: family-quest フロントエンドの鮮度チェック ---
# git pull 以外の経路(git reset --hard 等)でチェックアウトが更新されると
# post-merge フックが発火せず、dist/ が旧世代のままサーバーだけ新コードで
# 起動してAPIスキーマ不整合を起こすことがある(2026-09-01の障害)。
# サーバー起動前に必ず冪等チェックを通し、ビルド漏れをここで回収する。
# ビルド失敗でもサーバー起動は続行する。deploy.sh はビルドを dist.next/ で行い成功時だけ
# dist/ と入れ替える(Issue #650)ため、失敗時・ビルド中も旧 dist/ がそのまま配信され続ける。
echo "--- Ensure family-quest dist is fresh ---"
if ! bash "$QUEST_DIR/deploy.sh" --if-stale > logs/quest_deploy.log 2>&1; then
    echo "⚠️ family-quest build failed. Serving existing dist/. See logs/quest_deploy.log"
fi

# --- Phase 2.5: クエストマスタ(quest_master/reward_master)の鮮度チェック ---
# quest_data.py を編集したPRをマージしても、マスタ同期は POST /api/quest/sync_master か
# sync_strict.py を手で叩いたときにしか走らないため、実機DBが古いまま残る
# (2026-09-19: 退役済みクエスト6件がDBに残り、移設先のすごろくステップ報酬と
#  二重取得になっていた障害。Issue #700)。post-merge フックからも同じコマンドを
# 呼ぶが、git pull 以外の経路(git reset --hard 等)で更新された場合の回収として
# サーバー起動前にも必ず通す(family-quest の dist 鮮度チェックと同じ思想)。
# --if-stale は quest_data.py / routine_data.py に差分があるときだけ同期するため、
# DELETE を含む破壊的操作が毎起動で走ることはない。失敗してもサーバー起動は続行する
# (旧マスタで動かす方が停止よりマシ。Phase 1.5/2 と同じ判断)。
echo "--- Ensure quest master data is in sync ---"
if ! $PYTHON_EXEC sync_strict.py --if-stale > logs/quest_master_sync.log 2>&1; then
    echo "⚠️ quest master sync failed. Continuing with existing master data. See logs/quest_master_sync.log"
fi

# --- Phase 3: 初期化 & Webhook修正 ---
echo "--- Check & Fix Webhooks (Cloudflare Tunnel) ---"
$PYTHON_EXEC switchbot_webhook_fix.py > logs/webhook_fix.log 2>&1

if [ "$PREPARE_ONLY" = true ]; then
  # systemd 経路: サーバー本体(unified_server.py)は home_system.service の ExecStart が
  # フォアグラウンドで起動する。ダッシュボードは#829でStreamlit版を廃止し、
  # unified_server.py 自身が配信するようになったため、別ユニットの起動は不要。
  echo "✅ Preparation finished (--prepare). unified_server.py は systemd (ExecStart) が起動します。"
  exit 0
fi

# --- Phase 4: サーバー起動 (手動運用・開発用の経路。実機の systemd 経路では上の --prepare で終了する) ---
echo "--- Start Home System Server ---"
# unified_server.py が内部で scheduler_boot.py を起動します。ダッシュボード
# (routers/dashboard_router.py)もこのプロセス自身が ${DASHBOARD_BASE_PATH} 配下で配信する。
# ★修正: '&'のみのバックグラウンド化はSSHログアウト時にシェルからSIGHUPが
# 送られて死ぬ余地があるため、nohupでSIGHUPを無視しdisownでジョブ管理からも外す
nohup $PYTHON_EXEC unified_server.py < /dev/null > logs/server_boot.log 2>&1 &
disown
echo "🚀 System started. Check logs/server_boot.log for details."

echo "✅ All systems go!"