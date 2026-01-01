import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime

# 自作モジュール
import config
import common

# ロガー設定
logger = common.setup_logging("nas_monitor")

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
                "total_gb": total // (2**30),
                "used_gb": used // (2**30),
                "free_gb": free // (2**30),
                "percent": (used / total) * 100
            }
        except Exception as e:
            logger.error(f"Disk usage check error: {e}")
            return None

    def save_to_db(self, ping_ok, mount_ok, usage=None):
        """結果をDBに保存"""
        try:
            # カラムリスト
            cols = [
                "timestamp", "device_name", "ip_address", 
                "status_ping", "status_mount", 
                "total_gb", "used_gb", "free_gb", "percent"
            ]
            
            # 値の準備 (失敗時はNoneや0を入れる)
            vals = (
                common.get_now_iso(),
                self.device_name,
                self.ip,
                "OK" if ping_ok else "NG",
                "OK" if mount_ok else "NG",
                usage['total_gb'] if usage else 0,
                usage['used_gb'] if usage else 0,
                usage['free_gb'] if usage else 0,
                usage['percent'] if usage else 0.0
            )

            common.save_log_generic(config.SQLITE_TABLE_NAS, cols, vals)
            logger.info(f"💾 DB記録: Ping={vals[3]}, Mount={vals[4]}, Use={vals[8]:.1f}%")

        except Exception as e:
            logger.error(f"DB保存エラー: {e}")

    def run(self):
        logger.info(f"🚀 NAS監視を開始します (Target: {self.ip})")

        # 1. Ping Check
        ping_ok = self.check_ping()
        if not ping_ok:
            msg = f"🚨 **NAS 接続エラー**\nIPアドレス ({self.ip}) へのPing応答がありません。"
            self._notify_error(msg)
            # 接続できなくても記録は残す
            self.save_to_db(ping_ok, False, None)
            return

        # 2. Mount Check
        mount_ok = self.check_mount()
        if not mount_ok:
            msg = f"⚠️ **NAS マウントエラー**\nネットワークは正常ですが、 `{self.mount_point}` がマウントされていません。"
            self._notify_error(msg)
            # マウントできなくても記録は残す
            self.save_to_db(ping_ok, mount_ok, None)
            return

        # 3. Disk Usage
        usage = self.get_disk_usage()
        if not usage:
            msg = f"⚠️ **NAS 容量取得エラー**\nマウントされていますが、容量情報の取得に失敗しました。"
            self._notify_error(msg)
            self.save_to_db(ping_ok, mount_ok, None)
            return

        # 4. DB保存 (正常系)
        self.save_to_db(ping_ok, mount_ok, usage)

        # 5. 通知判定 (容量不足または定期レポート)
        is_full = usage['percent'] > 90
        
        # 【修正】通知頻度の抑制
        # 異常時(is_full)は即時通知。
        # 正常時は「朝8時台」のみ通知する (スケジューラが1時間おきなので1日1回だけヒットする想定)
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
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord", channel=channel)
        
        logger.info("✅ NAS監視・記録完了")

    def _notify_error(self, message):
        """エラー通知ヘルパー"""
        logger.error(message)
        common.send_push(
            config.LINE_USER_ID, 
            [{"type": "text", "text": message}], 
            target="discord", 
            channel="error"
        )

if __name__ == "__main__":
    monitor = NasMonitor()
    monitor.run()