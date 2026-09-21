# MY_HOME_SYSTEM/monitors/server_watchdog.py
import fcntl
import re
import subprocess
import time
import traceback
from pathlib import Path
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core import state_file
from core.logger import setup_logging
from services.notification_service import send_push

# === 設定 ===
WATCH_SERVICE_NAME: str = "home_system.service"
# Issue #651: 外部コマンド(systemctl/pgrep/vcgencmd)の待ち時間上限(秒)。応答しない場合に監視ループを止めない
SUBPROCESS_TIMEOUT_SEC: int = 30
WATCH_PROCESS_NAME: str = "unified_server.py"
REMINDER_INTERVAL_SEC: int = 6 * 3600  # 6時間

LOCK_FILE: Path = Path(config.BASE_DIR) / "watchdog_alert_sent.lock"
# スロットリング履歴の通知済み状態 ("<boot_id> <hex値>" を1行保存)
THROTTLE_STATE_FILE: Path = Path(config.BASE_DIR) / "watchdog_throttle_history.state"
# vcgencmd が使えないことをブート毎1回だけ通知した記録 (boot_id を1行保存)
VCGENCMD_UNAVAILABLE_STATE_FILE: Path = Path(config.BASE_DIR) / "watchdog_vcgencmd_unavailable.state"
# CPU温度。vcgencmd に依存しないようカーネルの thermal_zone を直接読む(単位はミリ度)
THERMAL_ZONE_TEMP_FILE: Path = Path("/sys/class/thermal/thermal_zone0/temp")
# Pi 5 は 85°C でファームウェアが周波数を絞り始める。その手前で知らせる
CPU_TEMP_ALERT_C: float = 80.0
# 高温が続く間の再通知間隔と、その記録 (最後に通知した UNIX 時刻を1行保存)
CPU_TEMP_REALERT_SEC: int = 6 * 3600
CPU_TEMP_ALERT_STATE_FILE: Path = Path(config.BASE_DIR) / "watchdog_cpu_temp_alert.state"
# カーネルの rpi_volt hwmon が出す「現在電圧低下中」フラグ(vcgencmd が使えない時の代替)
HWMON_DIR: Path = Path("/sys/class/hwmon")
logger = setup_logging("watchdog")

# === メッセージ (主婦向け) ===
MSG_STOPPED: str = (
    "あら、サーバーが止まっちゃったみたいです💦\n"
    "パパに確認してもらってくださいね🙇\n"
    "(自動監視システムより)"
)
MSG_RECOVERED: str = (
    "お待たせしました！\n"
    "サーバーが復活しました✨\n"
    "もう大丈夫ですよ😊"
)
MSG_REMINDER: str = (
    "まだサーバーが止まっているようです😢\n"
    "お時間ある時に確認お願いします💦"
)

def get_service_status(service_name: str) -> str:
    """
    systemctlを使ってサービスのステータスを確認する
    
    Returns:
        str: 'active', 'inactive', 'failed', or 'error'
    """
    try:
        res = subprocess.run(
            ["systemctl", "is-active", service_name], 
            capture_output=True, text=True, check=False, timeout=SUBPROCESS_TIMEOUT_SEC
        )
        return res.stdout.strip()
    except Exception:
        return "error"

def is_process_alive(process_keyword: str) -> bool:
    """
    pgrepを使ってプロセスが起動しているか確認する。

    #411 S-L11: 以前は `pgrep -f process_keyword` の単純な部分文字列マッチだったため、
    `cat unified_server.py` や `vim unified_server.py`、`cp unified_server.py .bak` の
    ような無関係なコマンドの引数にキーワードが含まれるだけでヒットしてしまい、本来の
    サーバープロセスが落ちていても誤って「生きている」と判定しうる誤検知の余地があった。
    pythonインタプリタが当該スクリプトを引数として実行しているパターンにのみマッチする
    よう正規表現を絞り込む。
    """
    pattern = rf"python[0-9.]*\s+\S*{re.escape(process_keyword)}(\s|$)"
    try:
        res = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, check=False, timeout=SUBPROCESS_TIMEOUT_SEC
        )
        return res.returncode == 0
    except Exception:
        return False

def _get_boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except Exception:
        return "unknown"

def _is_new_history(history_issues: int) -> bool:
    """
    スロットリング履歴ビットが「このブートで未通知の内容」かどうかを判定する。

    get_throttled の履歴ビット(Bit 16-19)は再起動までクリアされないため、
    毎回 WARNING を出すと10分毎のノイズになる。ブートIDと通知済みビットを
    状態ファイルに記録し、新しいビットが立った時だけ True を返す。

    #449: このスクリプトはcronから定期実行される前提で、通常は逐次実行されるが、
    実行が重なった場合に読み取り→書き込みの間で他プロセスが割り込むと状態が
    上書き競合(lost update)しうる。状態ファイルへのflock(排他ロック)で
    読み取りから書き込みまでを1つの不可分な区間にする。

    Issue #661: 他の監視スクリプトの状態ファイルは core/state_file.py に集約したが、
    ここだけは意図的に独自実装のまま残している。state_file の read/write は
    それぞれ独立にロックを取る2つの操作であり、間に他プロセスが割り込みうる。
    ここで必要なのは「読んで、判定して、書く」までを1つのロック区間に収める
    compare-and-set であり(上の#449がまさにそれを入れた箇所)、
    read_text + write_text_atomic へ機械的に置き換えると#449の競合が再発する。
    """
    boot_id = _get_boot_id()
    notified_bits = 0

    try:
        THROTTLE_STATE_FILE.touch(exist_ok=True)
        with open(THROTTLE_STATE_FILE, "r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    saved_boot_id, saved_hex = f.read().split()
                    if saved_boot_id == boot_id:
                        notified_bits = int(saved_hex, 16)
                except Exception:
                    pass  # 状態ファイルなし・壊れている場合は未通知扱い

                if history_issues & ~notified_bits == 0:
                    return False

                try:
                    f.seek(0)
                    f.write(f"{boot_id} {hex(history_issues | notified_bits)}")
                    f.truncate()
                except OSError as e:
                    logger.debug(f"Failed to save throttle state: {e}")
                return True
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError as e:
        logger.debug(f"Failed to access throttle state file: {e}")
        return history_issues != 0

def _read_undervoltage_alarm() -> bool | None:
    """カーネルの rpi_volt hwmon から「現在電圧低下中」かを読む。読めなければ None。"""
    try:
        for hw in HWMON_DIR.iterdir():
            if (hw / "name").read_text().strip() == "rpi_volt":
                return (hw / "in0_lcrit_alarm").read_text().strip() == "1"
    except OSError:
        return None
    return None


def _check_throttling_without_vcgencmd(reason: str) -> None:
    """vcgencmd が使えない時の代替チェック。

    2026-09 に OS 更新で userland だけが新しくなり、vcgencmd が新カーネル側のデバイス
    (/dev/vcio_gencmd)を要求して失敗し続けた。以前はこれを DEBUG ログで握りつぶして
    いたため、スロットリング・電圧低下の監視が誰にも知られず止まっていた。
    現在の電圧低下だけは hwmon から読めるのでそちらで判定し、過去履歴が見えなく
    なっていることはブート毎に1回 ERROR で知らせる。
    """
    alarm = _read_undervoltage_alarm()
    if alarm:
        logger.error("⚠️ System Alert: Under-voltage detected (hwmon rpi_volt in0_lcrit_alarm=1)")

    boot_id = _get_boot_id()
    if state_file.read_text(str(VCGENCMD_UNAVAILABLE_STATE_FILE)) == boot_id:
        return
    fallback = "電圧低下は hwmon で監視を継続" if alarm is not None else "電圧低下も読み取れません"
    logger.error(
        f"vcgencmd が使えないため、スロットリング履歴の監視ができません({reason})。{fallback}。"
        "カーネルとファームウェア/userland の版ずれ(OS 更新後の再起動漏れ等)を確認してください"
    )
    state_file.write_text_atomic(str(VCGENCMD_UNAVAILABLE_STATE_FILE), boot_id)


def check_throttling_status():
    """
    Raspberry Piのハードウェア健全性（スロットリングや電圧低下）を確認する。
    """
    try:
        # 【修正点1】 check=True を外し、コマンド自体の失敗でPythonをクラッシュさせない
        result = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC)

        # コマンドが失敗した場合は監視が止まったことを知らせ、hwmon で代替判定する
        if result.returncode != 0:
            _check_throttling_without_vcgencmd(f"終了コード {result.returncode}")
            return

        if 'throttled=' in result.stdout:
            val_str = result.stdout.split('=')[1].strip()
            val = int(val_str, 16)
            
            if val == 0:
                return  # 完全に正常

            # ビットマスクの定義
            ACTIVE_MASK = 0x0000F   # 現在発生中 (Bit 0-3)
            HISTORY_MASK = 0xF0000  # 過去の履歴 (Bit 16-19)
            
            active_issues = val & ACTIVE_MASK
            history_issues = val & HISTORY_MASK
            
            # 1. 現在発生中の異常がある場合
            if active_issues != 0:
                msg = f"Active Hardware Throttling/Under-voltage Detected! Code: {hex(val)}"
                # 【修正点2】 core/logger.py の仕様上 logger.error だけでDiscordに自動送信されるため、
                # send_push を削除して二重通知のスパムを防ぎます。
                logger.error(f"⚠️ System Alert: {msg}")
                
            # 2. 過去の履歴のみの場合 (自動復旧済み)
            #    履歴ビットは再起動までスティッキーなので、ブート毎に1回だけ警告する
            elif history_issues != 0:
                if _is_new_history(history_issues):
                    logger.warning(f"Hardware Throttling History (Recovered): {hex(val)}")
                else:
                    logger.debug(f"Throttling history already reported this boot: {hex(val)}")
                
    except FileNotFoundError:
        _check_throttling_without_vcgencmd("コマンドが見つかりません")
    except Exception as e:
        # 万が一の予期せぬエラーも、無限ループを防ぐためにWARNINGに落とす
        logger.warning(f"Throttling Check failed (Non-critical): {e}")


def read_cpu_temp_c() -> float | None:
    """CPU温度(°C)をカーネルの thermal_zone から読む。読めなければ None。"""
    try:
        return int(THERMAL_ZONE_TEMP_FILE.read_text().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def check_cpu_temperature(now: float | None = None) -> None:
    """CPU温度が閾値以上なら ERROR(=Discord)で知らせる。高温が続く間は6時間おきに再通知。

    以前は起動時の post_boot_health_check が1回測るだけで、常時の温度監視が無かった。
    """
    temp = read_cpu_temp_c()
    if temp is None:
        logger.warning(f"CPU温度を読み取れません: {THERMAL_ZONE_TEMP_FILE}")
        return
    if temp < CPU_TEMP_ALERT_C:
        return
    now = time.time() if now is None else now
    last = state_file.read_text(str(CPU_TEMP_ALERT_STATE_FILE))
    try:
        if last and now - float(last) < CPU_TEMP_REALERT_SEC:
            logger.warning(f"CPU temperature still high: {temp:.1f}°C (re-alert suppressed)")
            return
    except ValueError:
        pass
    logger.error(f"⚠️ System Alert: CPU temperature {temp:.1f}°C (閾値 {CPU_TEMP_ALERT_C:.0f}°C)")
    state_file.write_text_atomic(str(CPU_TEMP_ALERT_STATE_FILE), str(now))


def check_health() -> None:
    """
    サービスの生存確認を行い、異常があれば通知を送信する
    """
    try:
        logger.debug("🔍 Watchdog check started...")
        
        status = get_service_status(WATCH_SERVICE_NAME)
        process_alive = is_process_alive(WATCH_PROCESS_NAME)
        
        is_healthy = (status in ["active", "activating"]) and process_alive
        process_status_str = 'OK' if process_alive else 'NG'

        if is_healthy:
            logger.debug("Health Check: Service=%s, Process=%s", status, process_status_str)
            
            if LOCK_FILE.exists():
                send_push([{"type": "text", "text": MSG_RECOVERED}], target="discord", channel="notify")
                LOCK_FILE.unlink()
                logger.info("Recovery notification sent.")
        else:
            logger.warning("⚠️ Unhealthy State Detected: Service=%s, Process=%s", status, process_status_str)

            current_time = time.time()
            should_notify = False
            
            if not LOCK_FILE.exists():
                should_notify = True
                send_push([{"type": "text", "text": MSG_STOPPED}], target="discord", channel="error")
                logger.info("Stop alert sent.")
            else:
                if current_time - LOCK_FILE.stat().st_mtime > REMINDER_INTERVAL_SEC:
                    should_notify = True
                    send_push([{"type": "text", "text": MSG_REMINDER}], target="discord", channel="error")
                    logger.info("Reminder alert sent.")

            if should_notify:
                LOCK_FILE.touch()

    except Exception:
        err = traceback.format_exc()
        logger.error("Watchdog Crashed: %s", err)

if __name__ == "__main__":
    # ハードウェアの健全性確認（スロットリング監視）
    check_throttling_status()
    check_cpu_temperature()
    # ソフトウェアの健全性確認（プロセス死活監視）
    check_health()