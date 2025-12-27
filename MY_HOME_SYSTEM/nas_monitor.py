# MY_HOME_SYSTEM/nas_monitor.py
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

    def check_ping(self) -> bool:
        """NASへのPing疎通確認"""
        try:
            # -c 1: 1回だけ送信, -W: タイムアウト(秒)
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
        # os.path.ismount はバインドマウント等で誤判定することがあるため、
        # マウントポイント自体が存在し、かつPingが通っている前提でチェックする
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

    def run(self):
        logger.info(f"🚀 NAS監視を開始します (Target: {self.ip})")

        # 1. Ping Check (ネットワーク生存確認)
        if not self.check_ping():
            msg = f"🚨 **NAS 接続エラー**\nIPアドレス ({self.ip}) へのPing応答がありません。\n電源が入っているか確認してください。"
            self._notify_error(msg)
            return

        # 2. Mount Check (ファイルシステム確認)
        if not self.check_mount():
            msg = f"⚠️ **NAS マウントエラー**\nネットワークは生きていますが、 `{self.mount_point}` がマウントされていません。\n再マウントを試みてください。"
            self._notify_error(msg)
            return

        # 3. Disk Usage (容量確認)
        usage = self.get_disk_usage()
        if not usage:
            msg = f"⚠️ **NAS 容量取得エラー**\nマウントされていますが、容量情報の取得に失敗しました。"
            self._notify_error(msg)
            return

        # 4. 正常時のレポート (Discordのレポートチャンネルへ)
        # 容量が90%を超えていたら警告、それ以外は定期レポート
        is_full = usage['percent'] > 90
        
        status_icon = "🔴" if is_full else "🟢"
        title = "容量不足警告" if is_full else "NAS稼働レポート"
        
        msg = (
            f"{status_icon} **{title}**\n"
            f"デバイス: BUFFALO LS720D ({self.ip})\n"
            f"状態: 正常にマウント中\n\n"
            f"💾 **ディスク使用率: {usage['percent']:.1f}%**\n"
            f"使用: {usage['used_gb']} GB / 全体: {usage['total_gb']} GB\n"
            f"(残り: {usage['free_gb']} GB)"
        )
        
        # 容量不足ならErrorチャンネル、通常ならReportチャンネル
        channel = "error" if is_full else "report"
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord", channel=channel)
        logger.info("✅ NAS正常確認完了")

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