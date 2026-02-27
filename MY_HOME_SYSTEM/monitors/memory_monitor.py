import os
import sys
import time
from typing import List, Dict, Any, Tuple

import psutil

# プロジェクトルートへのパス解決
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import config
from core.logger import setup_logging
from services.notification_service import send_push

logger = setup_logging("memory_monitor")

def is_target_process(cmdline: List[str]) -> bool:
    """MY_HOME_SYSTEM関連のPythonプロセスかどうかを判定する"""
    if not cmdline:
        return False
    cmd_str = " ".join(cmdline).lower()
    # pythonプロセスであり、かつ対象のディレクトリ/ファイルが含まれているか
    if "python" in cmd_str and ("my_home_system" in cmd_str or "unified_server.py" in cmd_str or "monitors/" in cmd_str):
        return True
    return False

def check_cooldown() -> bool:
    """クールダウンタイムを過ぎているかチェックする（Trueなら通知可能）"""
    last_notify_file = getattr(config, "MEMORY_ALERT_LAST_NOTIFY_FILE", os.path.join(config.FALLBACK_ROOT, "last_memory_alert.txt"))
    cooldown_sec = getattr(config, "MEMORY_ALERT_COOLDOWN_SEC", 7200)

    if not os.path.exists(last_notify_file):
        return True

    try:
        with open(last_notify_file, "r") as f:
            last_time_str = f.read().strip()
            if not last_time_str:
                return True
            last_time = float(last_time_str)
            if time.time() - last_time > cooldown_sec:
                return True
    except Exception as e:
        logger.warning(f"クールダウン確認中にエラー発生: {e}")
        return True # エラー時は通知を優先
    
    return False

def record_notification() -> None:
    """現在の時刻を最終通知時刻として記録する"""
    last_notify_file = getattr(config, "MEMORY_ALERT_LAST_NOTIFY_FILE", os.path.join(config.FALLBACK_ROOT, "last_memory_alert.txt"))
    try:
        os.makedirs(os.path.dirname(last_notify_file), exist_ok=True)
        with open(last_notify_file, "w") as f:
            f.write(str(time.time()))
    except Exception as e:
        logger.error(f"通知時刻の記録に失敗しました: {e}")

def get_top_memory_processes(limit: int = 5) -> str:
    """
    現在メモリを大量に消費している上位プロセスを取得し、フォーマットされた文字列を返す。
    
    Args:
        limit (int): 取得する上位プロセスの数。デフォルトは5。
        
    Returns:
        str: 上位プロセスのPID、プロセス名、メモリ使用量(MB)を含むログ用文字列。
    """
    process_list: List[Tuple[int, str, float]] = []
    
    for p in psutil.process_iter(['pid', 'name', 'memory_info']):
        try:
            # RSS (Resident Set Size: 物理メモリ使用量) を MB 単位で計算
            rss_mb = p.info['memory_info'].rss / (1024 * 1024)
            process_list.append((p.info['pid'], p.info['name'], rss_mb))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # プロセスが既に終了している、またはアクセス権がない場合はスキップ
            continue
            
    # メモリ消費量（タプルの3番目の要素）の降順でソート
    process_list.sort(key=lambda x: x[2], reverse=True)
    
    top_procs = process_list[:limit]
    lines = ["【Top Memory Consuming Processes】"]
    for pid, name, rss in top_procs:
        # 視認性を高めるためにフォーマットを調整
        lines.append(f"  - PID: {pid:<6} | Name: {name:<20} | Memory: {rss:.1f} MB")
        
    return "\n".join(lines)

def main() -> None:
    # [Silence Policy遵守] 定常ログをINFOからDEBUGへ変更
    logger.debug("メモリ監視を開始します...")
    
    alert_messages: List[str] = []
    
    # 1. システム全体のメモリチェック
    mem = psutil.virtual_memory()
    mem_percent = mem.percent
    sys_threshold = getattr(config, "MEMORY_ALERT_PERCENT", 85.0)
    
    if mem_percent >= sys_threshold:
        msg = f"⚠️ [システムメモリ警告] 全体使用率が {mem_percent}% に達しています。(閾値: {sys_threshold}%)"
        logger.warning(msg)
        alert_messages.append(msg)
        
        # [プロファイリング強化] 閾値超過時に上位プロセスの情報を取得・記録
        top_procs_msg = get_top_memory_processes(limit=5)
        logger.warning(f"システムメモリ逼迫時の詳細情報:\n{top_procs_msg}")
        alert_messages.append(top_procs_msg)

    # 2. プロセスごとのメモリチェック
    proc_limit_mb = getattr(config, "PROCESS_MEMORY_LIMIT_MB", 500.0)
    
    try:
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if is_target_process(p.info.get('cmdline', [])):
                    rss_bytes = p.memory_info().rss
                    rss_mb = rss_bytes / (1024 * 1024)
                    
                    if rss_mb >= proc_limit_mb:
                        cmd_short = " ".join(p.info['cmdline'])[:100]
                        msg = f"⚠️ [プロセス肥大化警告] PID: {p.info['pid']} ({cmd_short}) が {rss_mb:.1f} MB のメモリを消費しています。(閾値: {proc_limit_mb} MB)"
                        logger.warning(msg)
                        alert_messages.append(msg)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
                # Fail-Soft: プロセスが既に存在しない・アクセス権がない場合はスキップ
                continue
    except Exception as e:
         logger.error(f"プロセス情報の取得中に予期せぬエラーが発生しました: {e}")

    # 3. 異常検知時の通知処理 (Notification Guard)
    if alert_messages:
        if check_cooldown():
            logger.error("メモリ異常を検知しました。Discordへエラー通知を送信します。")
            messages_to_send = [{"type": "text", "text": "🚨 **メモリリーク/リソース枯渇の兆候**\n" + "\n".join(alert_messages)}]
            
            # 運用介入が必要なエラーとして送信
            target = getattr(config, "NOTIFICATION_TARGET", "discord")
            success = send_push(
                user_id=getattr(config, "LINE_PARENTS_GROUP_ID", ""),
                messages=messages_to_send,
                target=target,
                channel="error"
            )
            
            if success:
                record_notification()
        else:
            logger.info("メモリ異常を検知しましたが、クールダウン中のため通知をスキップしました。")
    else:
        logger.debug("メモリ使用量は正常範囲内です。")

if __name__ == "__main__":
    main()