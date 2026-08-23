import os
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, Optional, Any, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 自作モジュール
import config
from core.logger import setup_logging
from core.database import save_log_generic
from core.utils import get_now_iso
from services.notification_service import send_push

# ロガー設定
logger = setup_logging("nas_monitor")

class NasMonitor:
    """NASの状態監視、ディスク使用量の確認、および障害復旧時の自動切り戻しを行うクラス"""
    
    def __init__(self) -> None:
        self.ip: str = getattr(config, "NAS_IP", "192.168.1.20")
        self.mount_point: str = getattr(config, "NAS_MOUNT_POINT", "/mnt/nas")
        self.fallback_dir: str = getattr(config, "FALLBACK_ROOT", "/tmp/temp_fallback")
        self.timeout: int = getattr(config, "NAS_CHECK_TIMEOUT", 5)
        self.device_name: str = "BUFFALO LS720D"
        self.state_file: str = "/tmp/nas_monitor_state.json"

    def _load_state(self) -> Dict[str, bool]:
        """前回の監視状態をファイルから読み込む"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"State load error: {e}")
        return {"is_healthy": True}  # デフォルトは正常とみなす

    def _save_state(self, state: Dict[str, bool]) -> None:
        """現在の監視状態をファイルへ保存する"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f)
        except Exception as e:
            logger.error(f"State save error: {e}")

    def check_ping(self) -> bool:
        """NASへのPing疎通確認"""
        try:
            cmd = ["ping", "-c", "1", "-W", str(self.timeout), self.ip]
            res = subprocess.run(
                cmd, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            return res.returncode == 0
        except Exception as e:
            logger.error(f"Ping check error: {e}")
            return False

    def check_mount(self) -> bool:
        """マウントポイントが正しくマウントされているか確認"""
        if not os.path.exists(self.mount_point):
            return False
        return os.path.ismount(self.mount_point)

    def check_write_permission(self) -> bool:
        """NASへの実際の書き込み・削除が可能かテストする"""
        test_file = os.path.join(self.mount_point, '.write_test')
        try:
            with open(test_file, 'w') as f:
                f.write('health_check')
            os.remove(test_file)
            return True
        except IOError as e:
            logger.error(f"Write permission check error: {e}")
            return False

    def sync_fallback_data(self) -> None:
        """フォールバックディレクトリのデータをNASへ安全に同期・移動する"""
        if not os.path.exists(self.fallback_dir) or not os.listdir(self.fallback_dir):
            logger.debug("フォールバックディレクトリに同期対象のデータはありません。")
            return

        logger.info(f"Starting fallback data sync from {self.fallback_dir} to {self.mount_point}")
        
        # rsyncを使用して安全に転送。--remove-source-filesで転送完了したファイルのみ元から削除
        cmd = [
            "rsync", "-av", "--remove-source-files", 
            f"{self.fallback_dir}/", 
            f"{self.mount_point}/"
        ]
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                logger.info("✅ NAS restored and fallback data synced.")

                # 通知（復旧および同期完了）
                send_push(
                    config.LINE_USER_ID,
                    [{"type": "text", "text": f"🟢 【NAS復旧】\nNASの復旧と、ローカルからのデータ同期が完了しました。\nPath: {self.mount_point}"}],
                    target="discord", channel="report"
                )

                # rsync --remove-source-files は空ディレクトリを残すため、クリーンアップ
                self._cleanup_empty_dirs(self.fallback_dir)
            else:
                logger.error(f"Sync failed with rsync error: {res.stderr}")
        except subprocess.TimeoutExpired:
            logger.error("Sync process exception: rsync timed out after 120s (NAS mount unresponsive)")
        except Exception as e:
            logger.error(f"Sync process exception: {e}")

    def _cleanup_empty_dirs(self, path: str) -> None:
        """指定パス配下の空ディレクトリを再帰的に削除する"""
        for root, dirs, files in os.walk(path, topdown=False):
            for d in dirs:
                dir_path = os.path.join(root, d)
                try:
                    os.rmdir(dir_path)
                except OSError:
                    pass  # 中身があるディレクトリは無視

    def get_disk_usage(self) -> Optional[Dict[str, float]]:
        """ディスク使用量を取得 (GB単位)"""
        try:
            total, used, free = shutil.disk_usage(self.mount_point)
            return {
                "total_gb": round(total / (2**30), 2),
                "used_gb": round(used / (2**30), 2),
                "free_gb": round(free / (2**30), 2),
                "percent": round(used / total * 100, 1)
            }
        except Exception as e:
            logger.error(f"Disk usage check error: {e}")
            return None

    def cleanup_old_files(self, directory: str, retention_days: int, extensions: Tuple[str, ...]) -> Dict[str, Any]:
        """指定ディレクトリ配下を再帰的に走査し、保持日数を超えた対象拡張子のファイルを削除する。"""
        result: Dict[str, Any] = {"deleted_count": 0, "freed_gb": 0.0}

        if not directory or not os.path.isdir(directory):
            return result

        cutoff = time.time() - (retention_days * 86400)
        freed_bytes = 0

        for root, _dirs, files in os.walk(directory):
            for name in files:
                if not name.lower().endswith(extensions):
                    continue
                path = os.path.join(root, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        size = os.path.getsize(path)
                        os.remove(path)
                        result["deleted_count"] += 1
                        freed_bytes += size
                except OSError as e:
                    logger.warning(f"Cleanup skip (error): {path}: {e}")

        result["freed_gb"] = round(freed_bytes / (2**30), 2)
        return result

    def run_retention_cleanup(self) -> None:
        """保持期間を超えたNVR録画・カメラスナップショット・DBバックアップを削除する。"""
        targets = [
            ("NVR録画", getattr(config, "NVR_RECORD_DIR", None),
             getattr(config, "RECORDING_RETENTION_DAYS", 30), (".mp4",)),
            ("スナップショット", os.path.join(getattr(config, "ASSETS_DIR", ""), "snapshots"),
             getattr(config, "RECORDING_RETENTION_DAYS", 30), (".jpg", ".jpeg")),
            ("タイムラプス動画", os.path.join(getattr(config, "ASSETS_DIR", ""), "timelapse"),
             getattr(config, "RECORDING_RETENTION_DAYS", 30), (".mp4", ".jpg")),
            ("DBバックアップ", getattr(config, "DB_BACKUPS_DIR", None),
             getattr(config, "DB_BACKUP_RETENTION_DAYS", 30), (".db",)),
        ]

        summary_lines = []
        for label, directory, retention_days, extensions in targets:
            if not directory:
                continue
            result = self.cleanup_old_files(directory, retention_days, extensions)
            if result["deleted_count"] > 0:
                logger.info(
                    f"🗑️ Cleanup {label} ({directory}): "
                    f"removed {result['deleted_count']} files, freed {result['freed_gb']} GB"
                )
                summary_lines.append(f"- {label}: {result['deleted_count']}件 / {result['freed_gb']}GB")

        if summary_lines:
            send_push(
                config.LINE_USER_ID,
                [{"type": "text", "text": "🗑️ **古いファイルの自動削除**\n" + "\n".join(summary_lines)}],
                target="discord", channel="report"
            )

    def save_to_db(self, ping_ok: bool, mount_ok: bool, usage: Optional[Dict[str, float]]) -> None:
        """状態をDBに保存"""
        percent = usage['percent'] if usage else 0
        save_log_generic(
            config.SQLITE_TABLE_SENSOR,
            ["timestamp", "device_name", "device_id", "device_type", "contact_state", "nas_usage_percent"],
            (
                get_now_iso(),
                "NAS_Monitor",
                self.ip,
                "Server",
                "mounted" if mount_ok else "unmounted",
                percent
            )
        )

    def run(self) -> None:
        """NASの状態監視、復旧検知、およびディスク使用量の確認を実行する。"""
        
        ping_ok = self.check_ping()
        mount_ok = self.check_mount() if ping_ok else False
        write_ok = self.check_write_permission() if mount_ok else False
        
        is_currently_healthy = ping_ok and mount_ok and write_ok
        previous_state = self._load_state()
        was_healthy = previous_state.get("is_healthy", True)

        # 1. 状態遷移の検知（正常 -> 異常：フォールバック移行時）
        if not is_currently_healthy and was_healthy:
            logger.error(f"❌ NAS connection lost or write failed. Falling back to local storage. (Ping: {ping_ok}, Mount: {mount_ok}, Write: {write_ok})")
            send_push(
                config.LINE_USER_ID, 
                [{"type": "text", "text": f"🚨 【NAS障害】\nNASへのアクセスが失われました。\nローカルフォールバックへ移行します。\nIP: {self.ip}"}],
                target="discord", channel="error"
            )
            self._save_state({"is_healthy": False})

        # 2. 状態遷移の検知（異常 -> 正常：NAS復旧時）
        elif is_currently_healthy and not was_healthy:
            logger.debug("NAS recovery detected. Initiating fallback data sync...")
            self.sync_fallback_data()
            self._save_state({"is_healthy": True})

        # DB記録
        usage = self.get_disk_usage() if is_currently_healthy else None
        self.save_to_db(ping_ok, mount_ok, usage)

        # 異常継続中の場合はここで処理終了（ログ汚染を防ぐ）
        if not is_currently_healthy:
            return

        # 3. 正常継続時の定常チェック
        logger.debug("NAS mount and write permissions are normal.")

        if not usage:
            return

        # 通知判定 (容量不足または定期レポート)
        is_full = usage['percent'] > 90
        now = datetime.now()
        is_report_time = (now.hour == 8)

        # 保持期間を超えた録画・バックアップの自動削除（1日1回、レポート時刻に合わせて実行）
        if is_report_time:
            self.run_retention_cleanup()

        if not is_full and not is_report_time:
            return
        
        status_icon = "🔴" if is_full else "🟢"
        title = "容量不足警告" if is_full else "NAS稼働レポート"
        
        msg = (
            f"{status_icon} **{title}**\n"
            f"デバイス: {self.device_name} ({self.ip})\n"
            f"状態: 正常\n\n"
            f"💾 **ディスク使用率: {usage['percent']:.1f}%**\n"
            f"使用: {usage['used_gb']} GB / 全体: {usage['total_gb']} GB\n"
            f"(残り: {usage['free_gb']} GB)"
        )
        
        channel = "error" if is_full else "report"
        send_push(
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}],
            target="discord", channel=channel
        )

if __name__ == "__main__":
    monitor = NasMonitor()
    monitor.run()