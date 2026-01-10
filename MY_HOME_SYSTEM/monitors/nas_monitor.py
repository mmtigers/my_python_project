import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime

# 自作モジュール
import config
# import common <-- 削除
from core.logger import setup_logging
from services.notification_service import send_push

# ロガー設定
logger = setup_logging("nas_monitor")

class NasMonitor:
    def __init__(self):
        self.ip = getattr(config, "NAS_IP", "192.168.1.20")
        self.mount_point = getattr(config, "NAS_MOUNT_POINT", "/mnt/nas")
        self.timeout = getattr(config, "NAS_CHECK_TIMEOUT", 5)
        # デバイス名 (configになければデフォルト値)
        self.device_name = "BUFFALO LS720D"

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

    def get_disk_usage(self):
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

    def save_to_db(self, ping_ok: bool, mount_ok: bool, usage: dict):
        """状態をDBに保存"""
        # 今回はDB保存は省略、または core.database.save_log_async を使う形に改修可能
        # 必要であればここも from core.database import save_log_generic 等を追加
        pass 

    def run(self):
        logger.info("Checking NAS status...")
        
        # 1. Ping Check
        ping_ok = self.check_ping()
        if not ping_ok:
            logger.error(f"❌ Ping Check Failed: {self.ip}")
            send_push(
                config.LINE_USER_ID, 
                [{"type": "text", "text": f"🚨 【NAS障害】\nPing応答がありません。\nIP: {self.ip}"}],
                target="discord", channel="error"
            )
            return

        # 2. Mount Check
        mount_ok = self.check_mount()
        if not mount_ok:
            logger.error(f"❌ Mount Check Failed: {self.mount_point}")
            send_push(
                config.LINE_USER_ID, 
                [{"type": "text", "text": f"⚠️ 【NAS警告】\nマウントが外れています。\nPath: {self.mount_point}"}],
                target="discord", channel="error"
            )
            # マウント復旧コマンドをここに書くことも可能
            return

        # 3. Disk Usage
        usage = self.get_disk_usage()
        if not usage:
            return

        # 4. DB保存 (正常系)
        self.save_to_db(ping_ok, mount_ok, usage)

        # 5. 通知判定 (容量不足または定期レポート)
        is_full = usage['percent'] > 90
        
        now = datetime.now()
        is_report_time = (now.hour == 8)

        if not is_full and not is_report_time:
            # 正常かつ報告時間外ならログのみで終了
            logger.info("⏳ 正常稼働中 - 定時報告(8時)ではないため通知をスキップします")
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
        
        # Discordに見やすく送信
        send_push(
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}],
            target="discord", channel=channel
        )

if __name__ == "__main__":
    monitor = NasMonitor()
    monitor.run()