import contextlib
import os
import sys
import time
import socket
import subprocess
import shutil
import requests
import sqlite3
from typing import List
from dataclasses import dataclass
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- パス設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

try:
    import config
    import common
    from services import switchbot_service
except ImportError as e:
    print(f"Error: Failed to import config or common modules. {e}", file=sys.stderr)
    sys.exit(1)

# ロガー設定
logger = common.setup_logging("health_check")

# ==========================================
# ユーザー設定
# ==========================================
def resolve_target_bluetooth_mac():
    """BT運用が有効(config.ENABLE_BLUETOOTH=True)な場合のみスピーカーMACを返す。

    無効時はNoneを返し、Speakerチェックはサウンドカード確認にフォールバックする
    (bluetooth.serviceが停止した環境でBT WARNを出し続けないため)。
    """
    if not getattr(config, "ENABLE_BLUETOOTH", False):
        return None
    return getattr(config, "SPEAKER_BLUETOOTH_MAC", None)

TARGET_BLUETOOTH_MAC = resolve_target_bluetooth_mac()
# ==========================================

# ステータスレベル定数
STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_ERR = "ERR"

@dataclass
class CheckResult:
    name: str
    status: str
    message: str

class PostBootHealthCheck:
    def __init__(self):
        self.max_retries = 12       
        self.retry_interval = 10    
        self.results: List[CheckResult] = []
        
        log_dir = getattr(config, 'LOG_DIR', os.path.join(BASE_DIR, 'logs'))
        self.log_file_path = os.path.join(log_dir, "home_system.log")

    # --- Utility Methods ---
    def _check_port(self, host: str, port: int, timeout=3) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def _check_http(self, url: str, timeout=5, headers=None) -> bool:
        try:
            res = requests.get(url, headers=headers, timeout=timeout)
            return 200 <= res.status_code < 400
        except Exception:
            return False

    def _get_uptime(self) -> str:
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                if uptime_seconds < 60:
                    return f"{int(uptime_seconds)}秒"
                elif uptime_seconds < 3600:
                    return f"{int(uptime_seconds // 60)}分"
                else:
                    return f"{int(uptime_seconds // 3600)}時間{int((uptime_seconds % 3600) // 60)}分"
        except Exception:
            return "不明"

    # --- 1. System & Network ---
    def check_system_resources(self):
        # 温度
        try:
            res = subprocess.check_output(["vcgencmd", "measure_temp"], timeout=10).decode("utf-8")
            temp = float(res.replace("temp=", "").replace("'C\n", ""))
            if temp >= 85:
                temp_status = STATUS_ERR
            elif temp >= 75:
                temp_status = STATUS_WARN
            else:
                temp_status = STATUS_OK
            temp_msg = f"{temp:.1f}°C"
        except Exception:
            temp_status = STATUS_WARN
            temp_msg = "Unknown"

        # ディスク
        try:
            total, used, free = shutil.disk_usage("/")
            disk_percent = (used / total) * 100
            if disk_percent > 95:
                disk_status = STATUS_ERR
            elif disk_percent > 90:
                disk_status = STATUS_WARN
            else:
                disk_status = STATUS_OK
            disk_msg = f"{disk_percent:.1f}%"
        except Exception:
            disk_status = STATUS_WARN
            disk_msg = "Unknown"

        if temp_status == STATUS_ERR or disk_status == STATUS_ERR:
            final_status = STATUS_ERR
        elif temp_status != STATUS_OK or disk_status != STATUS_OK:
            final_status = STATUS_WARN
        else:
            final_status = STATUS_OK

        self.results.append(CheckResult(
            "System Resource", final_status, f"CPU: {temp_msg} / Disk: {disk_msg}"
        ))

    def check_network_and_apis(self):
        # Ping
        try:
            subprocess.check_call(["ping", "-c", "1", "-W", "2", "8.8.8.8"], stdout=subprocess.DEVNULL, timeout=10)
        except Exception:
            self.results.append(CheckResult("Network", STATUS_ERR, "Offline (Ping NG)"))
            return 

        # API
        # #411 品質: NATURE_REMO_ACCESS_TOKEN未設定時、以前は f-string展開で
        # "Authorization: Bearer None" という実在しないトークンをそのまま送信し、
        # 「未設定」ではなく「API NG」として誤報告していた。未設定のAPIは
        # チェック自体をスキップする。
        api_targets = [
            ("SwitchBot", "https://api.switch-bot.com/v1.0/devices", switchbot_service.create_switchbot_auth_headers()),
        ]
        if config.NATURE_REMO_ACCESS_TOKEN:
            api_targets.append((
                "NatureRemo",
                "https://api.nature.global/1/users/me",
                {"Authorization": f"Bearer {config.NATURE_REMO_ACCESS_TOKEN}"},
            ))
        api_ngs = []
        for name, url, headers in api_targets:
            if not self._check_http(url, headers=headers):
                api_ngs.append(name)

        if not api_ngs:
            self.results.append(CheckResult("Network & API", STATUS_OK, "All Connected"))
        else:
            self.results.append(CheckResult("Network & API", STATUS_WARN, f"API NG: {','.join(api_ngs)}"))

    # --- 2. Database Integrity ---
    def check_database(self):
        db_path = getattr(config, "SQLITE_DB_PATH", "home_system.db")
        if not os.path.isabs(db_path):
            db_path = os.path.join(BASE_DIR, db_path)

        if not os.path.exists(db_path):
            self.results.append(CheckResult("Database", STATUS_ERR, "File Not Found"))
            return

        try:
            # #411 S-L8: 以前はconn.close()を成功パスの末尾でしか呼んでおらず、
            # cursor.execute/fetchoneが例外を送出するとexcept節には到達するが
            # 接続はcloseされずリークしていた。contextlib.closingでどの終了経路
            # でも確実にcloseする。
            with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)) as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA quick_check;")
                result = cursor.fetchone()[0]

            if result == "ok":
                self.results.append(CheckResult("Database", STATUS_OK, "Integrity OK"))
            else:
                self.results.append(CheckResult("Database", STATUS_ERR, f"Corrupt: {result}"))
        except Exception as e:
            self.results.append(CheckResult("Database", STATUS_ERR, f"Error: {str(e)}"))

    # --- 3. Services (Wait & Retry) ---
    def check_services(self):
        frontend_url = getattr(config, "FRONTEND_URL", "http://localhost:8000/quest/")
        
        targets = [
            {"name": "Backend Server", "type": "port", "val": 8000, "critical": True},
            {"name": "Family Quest",   "type": "http", "val": frontend_url, "critical": True},
            {"name": "Dashboard",      "type": "port", "val": 8501, "critical": True},
        ]

        logger.info("⏳ Waiting for services to startup...")

        # 各サービスの起動待ちリトライループを直列に回すと、全滅時に
        # 待ち時間が積み上がってしまう(3サービス x 最大2分 = 最大6分)ため、
        # ターゲットごとに独立したスレッドで並列に待ち、通知までの最悪時間を
        # 単一サービスのリトライ時間(最大2分)まで縮める。
        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            self.results.extend(executor.map(self._wait_for_service, targets))

    def _wait_for_service(self, target: dict) -> CheckResult:
        is_ok = False
        for i in range(self.max_retries):
            if target["type"] == "port":
                is_ok = self._check_port("localhost", target["val"])
            elif target["type"] == "http":
                is_ok = self._check_http(target["val"])

            if is_ok:
                break
            time.sleep(self.retry_interval)

        if is_ok:
            status = STATUS_OK
            msg = "Running"
        else:
            if target["critical"]:
                status = STATUS_ERR
                msg = "Failed"
            else:
                status = STATUS_WARN
                msg = "Not Running (Optional)"

        return CheckResult(target["name"], status, msg)

    # --- 4. Peripherals ---
    def check_peripherals(self) -> None:
        """NASの書き込み権限を含む周辺機器のチェックを行う [cite: 438]"""
        nas_ip = getattr(config, "NAS_IP", "192.168.1.20")
        mount_point = getattr(config, "NAS_MOUNT_POINT", "/mnt/nas")
        is_mounted = os.path.ismount(mount_point)
        
        nas_status, nas_msg = STATUS_ERR, "Disconnected"

        if is_mounted:
            test_file = os.path.join(mount_point, ".health_check_rw")
            try:
                with open(test_file, "w") as f:
                    f.write("ok")
                os.remove(test_file)
                nas_status, nas_msg = STATUS_OK, f"Mounted & Writable ({nas_ip})"
            except (IOError, PermissionError) as e:
                # 権限エラーは介入が必要なため、ERRORとして即時通知 [cite: 361, 469]
                nas_status, nas_msg = STATUS_ERR, "Permission Denied"
                error_detail = f"NAS書き込み権限エラー: {e}"
                logger.error(error_detail)
                common.send_push(
                    messages=[{"type": "text", "text": f"🚨 [System Alert] NAS権限エラー\n内容: {error_detail}"}],
                    target="discord",
                    channel="report"
                )


        self.results.append(CheckResult("NAS", nas_status, nas_msg))

        # Cameras
        cameras = getattr(config, "CAMERAS", [])
        if cameras:
            ok_cam = 0
            for cam in cameras:
                if self._check_port(cam.get("ip"), 80, timeout=2) or self._check_port(cam.get("ip"), 554, timeout=2):
                    ok_cam += 1
            
            if ok_cam == len(cameras):
                cam_status = STATUS_OK
            elif ok_cam > 0:
                cam_status = STATUS_WARN
            else:
                cam_status = STATUS_ERR
            
            cam_msg = f"{ok_cam}/{len(cameras)} Online"
        else:
            cam_status = STATUS_WARN
            cam_msg = "No Config"

        self.results.append(CheckResult("Cameras", cam_status, cam_msg))

        # Speaker
        spk_status = STATUS_OK
        spk_msg = "OK"
        
        has_card = False
        try:
            if "card" in subprocess.check_output(["aplay", "-l"], stderr=subprocess.DEVNULL, timeout=10).decode():
                has_card = True
        except Exception: pass

        if TARGET_BLUETOOTH_MAC:
            try:
                # bluetoothctlはBluetoothデーモン不調時に応答を返さず無限に待つことが
                # あるため、stdinを閉じてtimeoutで打ち切る(超過時はexceptでBT Error扱い)
                res = subprocess.check_output(
                    ["bluetoothctl", "info", TARGET_BLUETOOTH_MAC],
                    stdin=subprocess.DEVNULL, timeout=15,
                ).decode()
                if "Connected: yes" in res:
                    spk_msg = "Connected (BT)"
                else:
                    spk_status = STATUS_WARN
                    spk_msg = "Disconnected (BT)"
            except Exception:
                spk_status = STATUS_WARN
                spk_msg = "BT Error"
        elif not has_card:
            spk_status = STATUS_WARN
            spk_msg = "No Device"
        else:
            spk_msg = "Sound Card OK"

        self.results.append(CheckResult("Speaker", spk_status, spk_msg))

    # --- 5. Log Analysis (Time Filter Added) ---
    def check_recent_logs(self):
        """直近10分以内のログのみをチェック"""
        if not os.path.exists(self.log_file_path):
            self.results.append(CheckResult("Logs", STATUS_WARN, "No log file yet"))
            return

        error_lines = []
        # 現在時刻の10分前を基準とする
        time_threshold = datetime.now() - timedelta(minutes=10)

        try:
            res = subprocess.check_output(["tail", "-n", "200", self.log_file_path], timeout=10).decode("utf-8", errors="ignore")
        except Exception as e:
            logger.error(f"Log check failed: {e}")
            self.results.append(CheckResult("Logs", STATUS_WARN, f"Check Failed: {e}"))
            return

        for line in res.splitlines():
            if "ERROR" in line or "CRITICAL" in line:
                # タイムスタンプ判定 (例: 2026-01-10 06:54:15 ...)
                try:
                    # 先頭19文字を日付としてパース
                    log_time_str = line[:19]
                    log_time = datetime.strptime(log_time_str, "%Y-%m-%d %H:%M:%S")

                    # 基準時間より古いログはスキップ
                    if log_time < time_threshold:
                        continue
                except ValueError:
                    # 日付パースに失敗した場合（フォーマット違いなど）は安全のためスキップ、
                    # もしくは厳密にチェックしたい場合は含める。ここではノイズ低減のためスキップ。
                    continue

                clean_line = line.strip()[:80] + "..." if len(line) > 80 else line.strip()
                error_lines.append(clean_line)

        if error_lines:
            display_errors = error_lines[-2:]
            error_details = "\n".join([f"> `{l}`" for l in display_errors])
            msg = f"{len(error_lines)} Errors in last 10min\n{error_details}"
            self.results.append(CheckResult("Logs", STATUS_WARN, msg))
        else:
            self.results.append(CheckResult("Logs", STATUS_OK, "Clean (Last 10min)"))

    # --- Execution ---
    def run(self):
        logger.info("Starting checks...")
        self.check_network_and_apis()
        self.check_system_resources()
        self.check_database()
        self.check_peripherals()
        self.check_services()
        self.check_recent_logs()
        self._send_report()

    def _send_report(self):
        has_err = any(r.status == STATUS_ERR for r in self.results)
        has_warn = any(r.status == STATUS_WARN for r in self.results)
        
        if has_err:
            title_icon = "🔴"
        elif has_warn:
            title_icon = "🟡"
        else:
            title_icon = "🟢"

        uptime = self._get_uptime()
        title = f"{title_icon} System Boot Report (Up: {uptime})"
        
        fields = []
        for res in self.results:
            if res.status == STATUS_OK:
                icon = "🟢"
            elif res.status == STATUS_WARN:
                icon = "🟡"
            else:
                icon = "🔴"
            
            fields.append(f"{icon} **{res.name}**: {res.message}")

        body = "\n".join(fields)
        
        logger.info(f"Report:\n{title}\n{body}")
        
        common.send_push(
            messages=[{"type": "text", "text": f"{title}\n\n{body}"}],
            target="discord",
            channel="report"
        )

if __name__ == "__main__":
    checker = PostBootHealthCheck()
    checker.run()