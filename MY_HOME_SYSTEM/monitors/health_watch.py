# MY_HOME_SYSTEM/monitors/health_watch.py
"""ラズパイ一次ヘルスチェック(毎時cron想定)。

docs/runbooks/raspi_claude_log_monitoring.md の「層1: 検知」の実装。
scheduler_boot.py 配下の監視群(server_watchdog等)は home_system.service と
同じプロセスツリーで動くためサービスごと落ちると一緒に停止するが、
本スクリプトはcron駆動でサービスから独立しており、その穴を塞ぐ。

チェック内容(いずれも決定論的でLLMは使わない):
  1. home_system.service が active か
  2. journalctl (home_system.service) に前回マーカー以降の err..emerg 出力があるか
  3. logs/*.log に前回マーカー以降の ERROR/CRITICAL 行があるか
  4. ルートディスク使用率が閾値超過していないか
  5. メモリ使用率が閾値超過していないか
  6. NASがマウントされているか

異常があれば notification_service 経由でDiscordのerrorチャンネルへ要約を通知する。
自動復旧(systemctl restart等)・自動調査は行わない(ランブックのガードレール参照)。
"""

import datetime
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging
from services.notification_service import send_push
from monitors.log_analyzer import LogAnalyzer

logger = setup_logging("health_watch")

# === 設定 ===
WATCH_SERVICE_NAME: str = "home_system.service"
DISK_THRESHOLD_PERCENT: float = 90.0
MEMORY_THRESHOLD_PERCENT: float = 90.0
# 前回チェック時刻マーカー(ランブックのマーカーファイル規約)
MARKER_FILE: str = os.path.join(config.LOG_DIR, ".claude_watch_marker")
# 同一内容の異常の再通知抑制状態
NOTIFY_STATE_FILE: str = os.path.join(config.LOG_DIR, ".claude_watch_notify_state")
# 同一の異常セットが継続している場合の再通知間隔(server_watchdogの6時間リマインダーと同思想)
RENOTIFY_INTERVAL_SEC: int = 6 * 3600
# マーカーが無い初回実行時に遡る時間
DEFAULT_LOOKBACK_SEC: int = 3600
# 通知に載せるログ抜粋の最大文字数
SNIPPET_LIMIT: int = 400


def _read_marker() -> datetime.datetime:
    """前回チェック完了時刻を読む。無ければ既定の遡り時間で補完する。"""
    try:
        with open(MARKER_FILE, "r", encoding="utf-8") as f:
            return datetime.datetime.fromisoformat(f.read().strip())
    except (OSError, ValueError):
        return datetime.datetime.now() - datetime.timedelta(seconds=DEFAULT_LOOKBACK_SEC)


def _write_marker(dt: datetime.datetime) -> None:
    with open(MARKER_FILE, "w", encoding="utf-8") as f:
        f.write(dt.isoformat())


def check_service_active() -> Optional[str]:
    """home_system.service の稼働確認。activeでなければ異常。"""
    res = subprocess.run(
        ["systemctl", "is-active", WATCH_SERVICE_NAME],
        capture_output=True, text=True, check=False,
    )
    status = res.stdout.strip() or "unknown"
    if status != "active":
        return f"{WATCH_SERVICE_NAME} が active ではありません (状態: {status})"
    return None


def check_journal_errors(since: datetime.datetime) -> Optional[str]:
    """journalctlで前回マーカー以降の err..emerg ログを確認する。"""
    res = subprocess.run(
        [
            "journalctl", "-u", WATCH_SERVICE_NAME, "--no-pager",
            "--since", since.strftime("%Y-%m-%d %H:%M:%S"),
            "-p", "err..emerg", "-n", "100",
        ],
        capture_output=True, text=True, check=False,
    )
    lines = [
        ln for ln in res.stdout.strip().splitlines()
        if ln and not ln.startswith("--")  # "-- No entries --" 等の区切り行を除外
    ]
    if lines:
        snippet = "\n".join(lines[-3:])[:SNIPPET_LIMIT]
        return f"journalctl に err 以上のログが {len(lines)} 行あります:\n{snippet}"
    return None


def check_app_logs(since: datetime.datetime) -> Optional[str]:
    """logs/*.log の前回マーカー以降の ERROR/CRITICAL 行を確認する。

    キーワード・除外パターン・タイムスタンプ解析は週次の log_analyzer と
    判定基準を揃えるため、LogAnalyzer をそのまま流用する(WARNINGは週次に任せ、
    ここではエラーのみを異常とみなす)。
    """
    analyzer = LogAnalyzer(days_back=0)
    analyzer.start_date = since  # 「過去N日」ではなく前回マーカー以降だけを見る
    # 自分自身のログ行は対象外にする(通知失敗時のERRORログが共通ログファイル
    # home_system.log 経由で翌回の自分のチェックに引っかかる自己発火を防ぐ)
    analyzer.IGNORE_PATTERNS = analyzer.IGNORE_PATTERNS + ["health_watch"]
    for filepath in glob.glob(os.path.join(config.LOG_DIR, "*.log")):
        # run_task.shが書くERROR行(タイムスタンプなし)での自己発火も防ぐ
        if os.path.basename(filepath) == "health_watch.log":
            continue
        analyzer._analyze_file(filepath)

    errors = {f: d for f, d in analyzer.report_data.items() if d["errors"] > 0}
    if errors:
        details = []
        for filename, data in list(errors.items())[:5]:
            line = f"{filename}: {data['errors']}件"
            if data.get("last_error"):
                line += f" (例: {data['last_error'][:120]})"
            details.append(line)
        return "アプリログにエラーがあります:\n" + "\n".join(details)
    return None


def check_disk_usage() -> Optional[str]:
    """ルートディスク使用率の閾値チェック(analysis_serviceと同じ取得方法)。"""
    total, used, _free = shutil.disk_usage("/")
    percent = (used / total) * 100
    if percent >= DISK_THRESHOLD_PERCENT:
        return f"ディスク使用率が {percent:.1f}% です (閾値 {DISK_THRESHOLD_PERCENT:.0f}%)"
    return None


def check_memory_usage() -> Optional[str]:
    """メモリ使用率の閾値チェック(analysis_serviceと同じ free -m 方式)。"""
    res = subprocess.run(["free", "-m"], capture_output=True, text=True, check=False)
    lines = res.stdout.strip().split("\n")
    if len(lines) < 2:
        raise RuntimeError("free -m の出力を解析できません")
    parts = lines[1].split()
    total = int(parts[1])
    used = int(parts[2])
    percent = (used / total) * 100 if total > 0 else 0
    if percent >= MEMORY_THRESHOLD_PERCENT:
        return f"メモリ使用率が {percent:.1f}% です (閾値 {MEMORY_THRESHOLD_PERCENT:.0f}%)"
    return None


def check_nas_mount() -> Optional[str]:
    """NASマウントの確認。"""
    if not os.path.ismount(config.NAS_MOUNT_POINT):
        return f"NAS ({config.NAS_MOUNT_POINT}) がマウントされていません"
    return None


def _should_notify(anomaly_keys: List[str], now: datetime.datetime) -> bool:
    """同一の異常セットが継続している間の再通知を抑制する。

    異常のセット(チェック名の組)が前回通知時と同じなら RENOTIFY_INTERVAL_SEC
    が経過するまで再通知しない。セットが変化したら即座に通知する。
    """
    fingerprint = hashlib.sha256("|".join(sorted(anomaly_keys)).encode()).hexdigest()
    try:
        with open(NOTIFY_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("fingerprint") == fingerprint:
            last = datetime.datetime.fromisoformat(state["last_notified"])
            if (now - last).total_seconds() < RENOTIFY_INTERVAL_SEC:
                return False
    except (OSError, ValueError, KeyError):
        pass

    with open(NOTIFY_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fingerprint, "last_notified": now.isoformat()}, f)
    return True


def run_checks() -> int:
    """全チェックを実行し、異常があれば通知する。戻り値はプロセスの終了コード。"""
    now = datetime.datetime.now()
    since = _read_marker()
    logger.info(f"一次ヘルスチェック開始 (前回マーカー: {since.isoformat()})")

    checks = [
        ("service", check_service_active),
        ("journal", lambda: check_journal_errors(since)),
        ("app_logs", lambda: check_app_logs(since)),
        ("disk", check_disk_usage),
        ("memory", check_memory_usage),
        ("nas", check_nas_mount),
    ]

    anomalies: List[str] = []
    anomaly_keys: List[str] = []
    internal_errors: List[str] = []
    for key, func in checks:
        try:
            result = func()
        except Exception as e:
            # チェック自体の失敗は異常通知には含めず、ログに残して終了コードで知らせる
            # (run_task.shがERROR行を記録し、週次のlog_analyzerが拾う)
            logger.error(f"チェック実行エラー ({key}): {e}")
            internal_errors.append(key)
            continue
        if result:
            anomalies.append(result)
            anomaly_keys.append(key)

    exit_code = 0
    if anomalies:
        logger.warning(f"異常検知: {anomaly_keys}")
        if _should_notify(anomaly_keys, now):
            msg = (
                f"🚨 **ラズパイ一次チェック異常** ({now.strftime('%m/%d %H:%M')})\n"
                + "\n".join(f"・{a}" for a in anomalies)
                + "\n\n※ 検知のみで自動対処はしていません。状況を確認してください。"
            )
            if not send_push([{"type": "text", "text": msg}], target="discord", channel="error"):
                logger.error("異常通知の送信に失敗しました")
                exit_code = 1
        else:
            logger.info("同一の異常が継続中のため再通知を抑制しました")
    else:
        logger.info("異常なし")

    if internal_errors:
        exit_code = 1

    # マーカーは通知の成否に関わらず更新する(同じログ行の重複検知を防ぐ)
    _write_marker(now)
    return exit_code


if __name__ == "__main__":
    sys.exit(run_checks())
