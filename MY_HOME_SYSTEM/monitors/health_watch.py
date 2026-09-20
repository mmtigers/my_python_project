# MY_HOME_SYSTEM/monitors/health_watch.py
"""ラズパイ一次ヘルスチェック(毎時cron想定)。

docs/runbooks/raspi_claude_log_monitoring.md の「層1: 検知」の実装。
scheduler_boot.py 配下の監視群(server_watchdog等)は home_system.service と
同じプロセスツリーで動くためサービスごと落ちると一緒に停止するが、
本スクリプトはcron駆動でサービスから独立しており、その穴を塞ぐ。

チェック内容(いずれも決定論的でLLMは使わない):
  1. home_system.service が active か
  2. unified_server が HTTP に応答し、DBまで到達する経路も 200 を返すか
     (Issue #735: 「プロセスは生きているが全APIが500」を検知する唯一の経路)
  3. journalctl (home_system.service) に前回マーカー以降の err..emerg 出力があるか
  4. logs/*.log に前回マーカー以降の ERROR/CRITICAL 行があるか
  5. ルートディスク使用率が閾値超過していないか
  6. メモリ使用率が閾値超過していないか
  7. NASがマウントされているか
  8. 実機構成(crontab / systemdユニット / logrotate設定)がリポジトリの deploy/ 配下と
     一致しているか(構成ドリフト検知。各READMEの「実機を変更したらこのファイルにも
     反映してコミットすること」を人手に頼らず機械的に検知する)
  9. `quest_data.QUESTS` と実機DBの `quest_master` が一致しているか
     (マスタデータのドリフト検知。Issue #700)
  10. カメラごとの常時録画(NVR)が止まっていないか(最新の録画ファイルが古すぎないか)
  11. 参照整合性が破れていないか(親が存在しない子行=孤児行。Issue #747)

異常があれば notification_service 経由でDiscordのerrorチャンネルへ要約を通知する。
自動復旧(systemctl restart等)は行わない(ランブックのガードレール参照)。

層2(Issue #339): config.HEALTH_WATCH_INVESTIGATE_HOOK にスクリプトパスが
設定されている場合のみ、通知と同じ抑制の内側で自動調査フック
(scripts/claude_investigate.sh)を fire-and-forget 起動する。未設定なら
従来どおり検知・通知のみ。
"""

import datetime
import difflib
import fcntl
import glob
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import quest_data
from core import state_file
from core.database import get_ro_connection
from core.logger import setup_logging
from core.utils import get_now_jst
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

# === 実機構成のドリフト検知 (チェック8) ===
# リポジトリで管理している実機構成と、実機に実際に導入されている内容の対応。
# 導入先パスは各READMEの導入手順(deploy/cron/README.md、
# MY_HOME_SYSTEM/deploy/systemd/README.md、同 logrotate/README.md)と一致させること。
REPO_ROOT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOME_SYSTEM_DIR: str = os.path.join(REPO_ROOT, "MY_HOME_SYSTEM")
# リポジトリ管理の crontab (crontab -l と突き合わせる)
TRACKED_CRONTAB: str = os.path.join(REPO_ROOT, "deploy", "cron", "crontab")
# (リポジトリ側ディレクトリ, globパターン, 実機側の導入先ディレクトリ)。
# リポジトリ側ディレクトリ内の各ファイルは、実機側の同名ファイルと突き合わせる。
TRACKED_CONFIG_DIRS: List[Tuple[str, str, str]] = [
    (os.path.join(HOME_SYSTEM_DIR, "deploy", "systemd"), "*.service", "/etc/systemd/system"),
    (os.path.join(HOME_SYSTEM_DIR, "deploy", "logrotate"), "*", "/etc/logrotate.d"),
]
# 構成ファイル比較で無視するファイル名(READMEは導入対象ではない)
CONFIG_IGNORE_BASENAMES: Tuple[str, ...] = ("README.md",)
# 通知に載せる差分行(+/-)の最大本数(ファイルごと)
DIFF_LINES_LIMIT: int = 3

# === マスタデータのドリフト検知 (チェック9) ===
# 通知に載せる quest_id の最大件数(これを超えた分は「ほかN件」に畳む)
QUEST_ID_LIST_LIMIT: int = 8

# === 常時録画の停止検知 (チェック10) ===
# 録画は nvr-*.service の ffmpeg が 600 秒ごとに "{YYYYMMDD}_{HHMMSS}.mp4" を新規作成する。
# 最新ファイル名の時刻がこれより古ければ「新しいセグメントが作られていない=録画停止」とみなす
# (分割間隔 10 分 + 再接続の待ち + 毎時実行の余裕)。
# 更新時刻(mtime)ではなくファイル名の時刻を使うのは、この NAS(CIFS)では書き込み中の
# ファイルの mtime が作成時刻のまま進まず、成長中かどうかの判定に使えないため。
RECORDING_STALE_SEC: int = 30 * 60
# 録画ファイル名(ffmpeg -strftime のローカル時刻)の解釈に使うタイムゾーン
JST = ZoneInfo("Asia/Tokyo")


def _read_marker() -> datetime.datetime:
    """前回チェック完了時刻を読む。無ければ既定の遡り時間で補完する。

    Issue #661: 読み書きは core/state_file.py へ寄せた(書き込みは tmp + fsync +
    os.replace の原子的差し替えになる)。ファイル自体が無い/壊れている場合に
    既定の遡り時間へ倒す方針は従来どおり。
    """
    raw = state_file.read_text(MARKER_FILE)
    if raw:
        try:
            return datetime.datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.datetime.now() - datetime.timedelta(seconds=DEFAULT_LOOKBACK_SEC)


def _write_marker(dt: datetime.datetime) -> None:
    state_file.write_text_atomic(MARKER_FILE, dt.isoformat())


# Issue #651: 外部コマンドの待ち時間上限(秒)。systemd/journald が応答しない状況で無限待ちになると、
# 毎時 cron の次回起動と重なって多重起動する(下記 LOCK_FILE と合わせて防ぐ)。
SUBPROCESS_TIMEOUT_SEC: int = 30
# 多重起動防止のロックファイル(cron 起動。他の長時間スクリプトと同じ flock LOCK_NB 方式)
LOCK_FILE: str = os.path.join(config.BASE_DIR, ".health_watch.lock")


def check_service_active() -> Optional[str]:
    """home_system.service の稼働確認。activeでなければ異常。"""
    res = subprocess.run(
        ["systemctl", "is-active", WATCH_SERVICE_NAME],
        capture_output=True, text=True, check=False, timeout=SUBPROCESS_TIMEOUT_SEC,
    )
    status = res.stdout.strip() or "unknown"
    if status != "active":
        return f"{WATCH_SERVICE_NAME} が active ではありません (状態: {status})"
    return None


def check_journal_errors(since: datetime.datetime) -> Optional[str]:
    """journalctlで前回マーカー以降のエラーを確認する。

    次の2種類を見る:

    1. systemd 自身が err..emerg で記録したもの(ユニットの異常終了等)。
    2. サービスの標準出力・標準エラー経由の行。journald はこれらを priority info で
       記録するため 1. の `-p err..emerg` では一切拾えない。2026-09-19 に
       home_system.service を Type=simple(Issue #646)へ移行した後、未捕捉例外の
       トレースバックや basicConfig のままのライブラリログ(`ERROR:zeep...` 等)は
       journal にしか残らなくなった(以前は start_all.sh が logs/server_boot.log へ
       リダイレクトしており、check_app_logs のキーワード判定で拾えていた)。
       ここで内容を見て判定しないと、子プロセス(camera_monitor/scheduler_boot)の
       クラッシュ直前のトレースバック等を層1が検知できない。
       core.logger の書式の行は logs/*.log にも出ていて check_app_logs が判定するので
       二重計上しないよう除外し、それ以外を LogAnalyzer と同じ基準で判定する。
    """
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    res = subprocess.run(
        [
            "journalctl", "-u", WATCH_SERVICE_NAME, "--no-pager",
            "--since", since_str,
            "-p", "err..emerg", "-n", "100",
        ],
        capture_output=True, text=True, check=False, timeout=SUBPROCESS_TIMEOUT_SEC,
    )
    lines = [
        ln for ln in res.stdout.strip().splitlines()
        if ln and not ln.startswith("--")  # "-- No entries --" 等の区切り行を除外
    ]

    stdio_errors = _journal_stdio_errors(since_str)

    parts = []
    if lines:
        snippet = "\n".join(lines[-3:])[:SNIPPET_LIMIT]
        parts.append(f"journalctl に err 以上のログが {len(lines)} 行あります:\n{snippet}")
    if stdio_errors:
        snippet = "\n".join(stdio_errors[-3:])[:SNIPPET_LIMIT]
        parts.append(
            f"サービスの標準出力/標準エラー(journal)にエラー行が {len(stdio_errors)} 行あります:\n{snippet}"
        )
    return "\n".join(parts) if parts else None


def _journal_stdio_errors(since_str: str) -> list[str]:
    """journal に残ったサービスの標準出力/標準エラーのうち、エラーと判定される行を返す。"""
    res = subprocess.run(
        [
            "journalctl", "-u", WATCH_SERVICE_NAME, "--no-pager",
            "--since", since_str, "-o", "cat", "-n", "2000",
        ],
        capture_output=True, text=True, check=False, timeout=SUBPROCESS_TIMEOUT_SEC,
    )
    analyzer = LogAnalyzer(days_back=0)
    core_logger_line = LogAnalyzer.LEVEL_PATTERNS[0]
    errors = []
    for ln in res.stdout.splitlines():
        if not ln.strip() or ln.startswith("--"):
            continue
        # core.logger の書式の行は logs/*.log 側で check_app_logs が見る(二重計上しない)
        if core_logger_line.match(ln):
            continue
        if any(ignore in ln for ignore in analyzer.IGNORE_PATTERNS):
            continue
        if analyzer._classify_line(ln) == "error":
            errors.append(ln.strip())
    return errors


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
        # run_task.shが書くERROR行(タイムスタンプなし)での自己発火も防ぐ。
        # claude_investigate.log は層2フック(_run_investigation_hook)自身の出力先で、
        # 調査結果の本文に "ERROR"/"Traceback" 等の語が含まれるのが常態のため、これを
        # 読むと翌回のチェックが「新規エラー」として再発報→再度フック起動→さらに出力、
        # という自己増殖ループになる(runbook/コメントは除外済みと記していたが未実装だった)。
        if os.path.basename(filepath) in ("health_watch.log", "claude_investigate.log"):
            continue
        # Issue #339層2調査で発覚: pip_install.log等、行に一切タイムスタンプが
        # 無いファイルは LogAnalyzer._analyze_file 内の effective_dt が常に None に
        # なり、start_date によるフィルタ(121行目)が効かないため、ファイルの中身が
        # 更新されなくても毎回無条件に「新規エラー」として再カウントされてしまう
        # (恒久的な誤検知・再通知抑制期間明けの誤通知)。週次の LogAnalyzer.run_analysis
        # と同じ mtime 足切りをここでも適用し、前回マーカー以降に更新されていない
        # ファイルは解析対象から除外する。
        if not analyzer._is_recent_file(filepath):
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
    res = subprocess.run(["free", "-m"], capture_output=True, text=True, check=False, timeout=SUBPROCESS_TIMEOUT_SEC)
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


def check_api_responsive() -> Optional[str]:
    """unified_server に実際に HTTP を投げ、「機能している」ことを確認する。

    Issue #735 (AUDIT-005): check_service_active(systemctl is-active)も
    server_watchdog(systemctl + pgrep)も「プロセスが生きているか」しか見ていない。
    マイグレーション失敗(unified_server.py の lifespan が意図的にこの状態を作る)・
    DBファイルの破損や権限異常・SQLite の恒久ロック・イベントループの停止は、いずれも
    「プロセスは生存しているが全APIが500」という状態になり、どの監視も緑を報告していた。

    health_watch は cron 駆動でサーバーのプロセスツリーから完全に独立した唯一の監視
    であるため、ここに HTTP プローブを置くのが最も到達範囲が広い。

    2本叩く:
      - GET /health       … readiness。migration 失敗時は 503 を返す(同 Issue で対応)
      - GET /api/quest/data … DB まで到達する経路(/health は DB を触らないため)
    """
    base = config.HEALTH_WATCH_PROBE_BASE_URL.rstrip("/")
    probes = (
        ("/health", config.HEALTH_WATCH_PROBE_TIMEOUT_SEC),
        ("/api/quest/data", config.HEALTH_WATCH_PROBE_DB_TIMEOUT_SEC),
    )
    for path, timeout in probes:
        try:
            res = requests.get(f"{base}{path}", timeout=timeout)
        except requests.exceptions.RequestException as e:
            return f"GET {path} へのHTTPプローブが失敗しました: {type(e).__name__}: {e}"
        if res.status_code != 200:
            # 本文はそのまま通知に載るため、長すぎる HTML 等を切り詰める
            body = (res.text or "").strip().replace("\n", " ")[:200]
            return f"GET {path} が {res.status_code} を返しました: {body}"
    return None


def _latest_recording_time(folder: str, now: datetime.datetime) -> datetime.datetime | None:
    """録画フォルダの最新セグメントの開始時刻(ファイル名から、JST)を返す。無ければNone。

    CIFS 越しに保持期間(30日)分を毎回列挙しないよう、当日と前日(日付の変わり目用)に絞る。
    ファイル名は ffmpeg の -strftime によるローカル時刻(実機のTZはAsia/Tokyo)。
    """
    names: list[str] = []
    for day in (now, now - datetime.timedelta(days=1)):
        pattern = os.path.join(folder, f"{day.strftime('%Y%m%d')}_*.mp4")
        names.extend(os.path.basename(p) for p in glob.glob(pattern))
    for name in sorted(names, reverse=True):
        try:
            return datetime.datetime.strptime(name[:15], "%Y%m%d_%H%M%S").replace(tzinfo=JST)
        except ValueError:
            continue
    return None


def check_recording_stalled() -> str | None:
    """カメラごとの常時録画(NVR)が止まっていないかを確認する。

    録画の ffmpeg は systemd が落ちるたびに再起動するが、カメラに繋がらない間の
    再試行ループや、応答の無いまま固まった状態は、systemd からは区別できず
    これまで誰にも通知されなかった(2026-08〜09 に parking で計12日ほど発生)。
    NAS 未マウント時はチェック7が報告するためここでは判定しない。
    """
    if not os.path.ismount(config.NAS_MOUNT_POINT):
        return None
    now = get_now_jst()
    stalled: list[str] = []
    for cam in config.CAMERAS:
        if not cam.get("enabled", True):
            continue
        folder_name = cam.get("nas_folder") or cam["name"]
        latest = _latest_recording_time(os.path.join(config.NVR_RECORD_DIR, folder_name), now)
        label = f"{cam['name']}({folder_name})"
        if latest is None:
            stalled.append(f"{label}: 今日・昨日の録画ファイルがありません")
        elif (now - latest).total_seconds() > RECORDING_STALE_SEC:
            minutes = int((now - latest).total_seconds() // 60)
            stalled.append(f"{label}: 最新の録画が {latest.strftime('%m/%d %H:%M')} 開始({minutes}分前)")
    if stalled:
        return "常時録画が止まっている可能性があります:\n" + "\n".join(stalled)
    return None


def _normalize_config_lines(text: str) -> List[str]:
    """構成ファイルの比較用に正規化する。

    行末の空白を落とし、空行とコメント行(#始まり)を除く。crontab -l は
    導入時のファイルをそのまま返すが、crontab -e で編集した場合や環境によって
    先頭にコメントヘッダが付く場合があるため、実行内容(ジョブ行・設定行)だけを
    比較対象にする。
    """
    lines = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return lines


def _config_diff_summary(label: str, expected: str, actual: str) -> Optional[str]:
    """リポジトリ側(expected)と実機側(actual)の正規化後の差分を1行要約にする。差分が無ければNone。"""
    exp_lines = _normalize_config_lines(expected)
    act_lines = _normalize_config_lines(actual)
    if exp_lines == act_lines:
        return None
    changes = [
        ln for ln in difflib.unified_diff(exp_lines, act_lines, lineterm="", n=0)
        if (ln.startswith("+") or ln.startswith("-"))
        and not ln.startswith("+++") and not ln.startswith("---")
    ]
    shown = "; ".join(ln[:80] for ln in changes[:DIFF_LINES_LIMIT])
    more = f" ほか{len(changes) - DIFF_LINES_LIMIT}行" if len(changes) > DIFF_LINES_LIMIT else ""
    return f"{label}: 差分 {len(changes)}行 ({shown}{more})"


def _check_crontab_drift() -> Optional[str]:
    """crontab -l とリポジトリ管理の deploy/cron/crontab の差分を返す。"""
    if not os.path.isfile(TRACKED_CRONTAB):
        return None  # リポジトリ側に無ければ比較対象外(チェックの失敗ではない)
    with open(TRACKED_CRONTAB, "r", encoding="utf-8") as f:
        expected = f.read()
    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False, timeout=SUBPROCESS_TIMEOUT_SEC)
    if res.returncode != 0:
        # "no crontab for <user>" は未登録。それ以外の失敗も未登録相当として報告する
        detail = (res.stderr or res.stdout).strip().splitlines()
        reason = detail[0][:80] if detail else f"exit {res.returncode}"
        return f"crontab: 実機に未登録です ({reason})"
    return _config_diff_summary("crontab", expected, res.stdout)


def _check_host_files_drift() -> List[str]:
    """systemdユニット・logrotate設定など、リポジトリ管理ファイルと実機側ファイルの差分一覧を返す。"""
    findings: List[str] = []
    for repo_dir, pattern, host_dir in TRACKED_CONFIG_DIRS:
        for repo_path in sorted(glob.glob(os.path.join(repo_dir, pattern))):
            name = os.path.basename(repo_path)
            if name in CONFIG_IGNORE_BASENAMES or not os.path.isfile(repo_path):
                continue
            host_path = os.path.join(host_dir, name)
            with open(repo_path, "r", encoding="utf-8") as f:
                expected = f.read()
            try:
                with open(host_path, "r", encoding="utf-8") as f:
                    actual = f.read()
            except FileNotFoundError:
                findings.append(f"{host_path}: 実機に未導入です")
                continue
            except OSError as e:
                findings.append(f"{host_path}: 読み取れません ({e.__class__.__name__})")
                continue
            summary = _config_diff_summary(host_path, expected, actual)
            if summary:
                findings.append(summary)
    return findings


def check_deploy_config_drift() -> str | None:
    """実機構成(crontab / systemd / logrotate)がリポジトリの deploy/ 配下と一致しているか。

    各READMEは「実機の設定を変更した場合は、このファイルにも反映してコミットすること」
    と人手の同期を前提にしているが、忘れると故障時の復旧手順(リポジトリからの再導入)が
    実機の実態と食い違う。比較はコメント・空行を除いた実行内容のみで行い、
    差分・未導入・未登録があれば異常として報告する(自動で書き戻しはしない)。
    """
    findings: List[str] = []
    crontab_finding = _check_crontab_drift()
    if crontab_finding:
        findings.append(crontab_finding)
    findings.extend(_check_host_files_drift())
    if findings:
        return (
            "実機構成がリポジトリ(deploy/)と一致しません:\n"
            + "\n".join(f"  - {f}" for f in findings)
            + "\n  → 実機側が正なら deploy/ 配下へ反映してコミット、リポジトリ側が正なら各READMEの導入手順で再導入してください"
        )
    return None


def _format_quest_ids(quest_ids: list[int]) -> str:
    """quest_id の一覧を通知用に整形する(多すぎる場合は件数に畳む)。"""
    shown = ", ".join(str(q) for q in quest_ids[:QUEST_ID_LIST_LIMIT])
    if len(quest_ids) > QUEST_ID_LIST_LIMIT:
        shown += f" ほか{len(quest_ids) - QUEST_ID_LIST_LIMIT}件"
    return shown


def check_quest_master_drift() -> str | None:
    """`quest_data.QUESTS` と実機DBの `quest_master` の乖離を検知する(Issue #700)。

    `GameSystem.sync_master_data()` はどのデプロイ経路からも自動実行されず、
    `POST /api/quest/sync_master` か `sync_strict.py` を叩いたときにしか走らない
    設計だった。そのため「`quest_data.py` を編集するPR」をマージして実機に
    `git pull` してもDBは古いままになり、2026-09-19の棚卸しで退役済みクエスト
    6件が `quest_master` に残って二重報酬になっていたことが判明した
    (同時に、コードにある `id=1023` が実機に未登録という逆方向の乖離もあった)。

    **このチェックは検知のみで自動修正はしない**。同期の実行は
    `sync_strict.py --if-stale`(デプロイ経路から自動実行)の責務であり、
    毎時cronのヘルスチェックから破壊的操作を走らせない。

    比較対象を `quest_master` の quest_id 集合だけに絞っているのは:

    - `reward_master` は「`user_inventory` から参照が残っている報酬はマスタから
      消えても削除をスキップする」という正しい挙動があり、退役後も残る行が
      恒常的に存在しうる。差分として報告すると恒久的な誤検知になる。
    - `routine_data.py` は対応するマスタテーブルを持たない定数のため比較対象外。

    DBは読み取り専用接続(`core.database.get_ro_connection`)で開く。DBファイルや
    テーブルがまだ無い環境(初回セットアップ中など)、および一時的なロックでは
    「異常」ではなく「今回は判定できない」として警告ログのみを残しスキップする
    — 毎時cronで走るため、DB不在の環境で恒久的に失敗し続けるのは避ける
    (サービス停止そのものはチェック1・3が検知する)。
    """
    master_ids = {q["id"] for q in quest_data.QUESTS}
    try:
        with get_ro_connection() as conn:
            db_ids = {row["quest_id"] for row in conn.execute("SELECT quest_id FROM quest_master")}
    except sqlite3.OperationalError as e:
        logger.warning(f"⚠️ quest_master を読めないためマスタ同期チェックをスキップします: {e}")
        return None

    if not master_ids:
        # quest_data.py のimportミス等。全件が「DBに残っている」と報告されると
        # ノイズになるうえ、この状態自体が同期してはいけない状態である。
        return "quest_data.QUESTS が空です(マスタ定義の読み込み失敗の可能性)。同期は実行しないでください"

    stale_ids = sorted(db_ids - master_ids)
    missing_ids = sorted(master_ids - db_ids)
    if not stale_ids and not missing_ids:
        return None

    findings: list[str] = []
    if stale_ids:
        findings.append(
            f"退役済みだがDBに残っているクエスト {len(stale_ids)}件: {_format_quest_ids(stale_ids)}"
        )
    if missing_ids:
        findings.append(
            f"コードにあるがDBに未登録のクエスト {len(missing_ids)}件: {_format_quest_ids(missing_ids)}"
        )
    return (
        "quest_master が quest_data.py と一致しません(退役クエストの二重報酬の原因):\n"
        + "\n".join(f"  - {f}" for f in findings)
        + "\n  → MY_HOME_SYSTEM で `python sync_strict.py --dry-run` で影響を確認のうえ同期してください"
    )


# Issue #747 (AUDIT-018): 参照整合性の検査対象。
#
# `PRAGMA foreign_keys=ON` を設定しているのに外部キー宣言は1つ
# (`user_inventory.reward_id`)しかなく、「本来DBが防げる不整合」への防御が
# サービス層の個別の None チェックとして散在している(根本原因 RC-5)。
# FK を足すには SQLite ではテーブル再作成が必要で、その前に「今どれだけ
# 孤児行があるか」を知る必要がある。まず測る。
#
# 2層に分けているのは、**一方が通常運用の設計どおりの結果**だから:
#
# - `_ORPHAN_CHECKS_STRICT`: 親は `quest_users`。`DELETE FROM quest_users` は
#   実行コードのどこにも存在せず(`reset_user_data` はリセットで削除しない)、
#   ここの孤児は手動SQL・将来のスクリプトの事故しかありえない → **異常として通知**。
# - `_ORPHAN_CHECKS_EXPECTED`: 親は `quest_master` / `reward_master`。
#   `sync_master_data` の `DELETE ... WHERE quest_id NOT IN (...)` は
#   クエストを退役させると **設計どおりマスタ行を消し、履歴行は残す**
#   (#700 で退役6件を実際に消している)。これを毎時「異常」として報告すると
#   恒久的な誤検知になる → **件数をログに残すだけで通知しない**。
#   `check_quest_master_drift` が `reward_master` を比較対象から外したのと同じ判断。
#
# `quest_id = 0` はアイテム使用ログ(`inventory_service`)でマスタを参照しない
# 疑似IDのため、孤児判定から除外する。
_ORPHAN_CHECKS_STRICT: tuple[tuple[str, str], ...] = (
    ("quest_history.user_id", """
        SELECT COUNT(*) FROM quest_history h
        LEFT JOIN quest_users u ON h.user_id = u.user_id WHERE u.user_id IS NULL"""),
    ("reward_history.user_id", """
        SELECT COUNT(*) FROM reward_history r
        LEFT JOIN quest_users u ON r.user_id = u.user_id WHERE u.user_id IS NULL"""),
    ("user_inventory.user_id", """
        SELECT COUNT(*) FROM user_inventory i
        LEFT JOIN quest_users u ON i.user_id = u.user_id WHERE u.user_id IS NULL"""),
    ("routine_progress.user_id", """
        SELECT COUNT(*) FROM routine_progress p
        LEFT JOIN quest_users u ON p.user_id = u.user_id WHERE u.user_id IS NULL"""),
    ("routine_step_events.user_id", """
        SELECT COUNT(*) FROM routine_step_events e
        LEFT JOIN quest_users u ON e.user_id = u.user_id WHERE u.user_id IS NULL"""),
    # 自己参照。取消時に張られるリンクで、参照先が消えていれば整合性の破れ。
    ("quest_history.linked_history_id", """
        SELECT COUNT(*) FROM quest_history h
        LEFT JOIN quest_history p ON h.linked_history_id = p.id
        WHERE h.linked_history_id IS NOT NULL AND p.id IS NULL"""),
)

_ORPHAN_CHECKS_EXPECTED: tuple[tuple[str, str], ...] = (
    ("quest_history.quest_id", """
        SELECT COUNT(*) FROM quest_history h
        LEFT JOIN quest_master q ON h.quest_id = q.quest_id
        WHERE q.quest_id IS NULL AND h.quest_id != 0"""),
    ("reward_history.reward_id", """
        SELECT COUNT(*) FROM reward_history r
        LEFT JOIN reward_master m ON r.reward_id = m.reward_id WHERE m.reward_id IS NULL"""),
)


def check_orphaned_rows() -> str | None:
    """親が存在しない子行(孤児行)を検知する (Issue #747 / AUDIT-018)。

    **検知のみで自動修正はしない。** 孤児行の削除は不可逆で、どちらを消すべきか
    (子行か、親を復活させるか)は中身を見ないと決められない。

    DBは読み取り専用接続で開き、DBやテーブルが無い環境・一時的なロックでは
    「異常」ではなく「今回は判定できない」として警告ログのみ残しスキップする
    (`check_quest_master_drift` と同じ方針。毎時cronで走るため、DB不在の環境で
    恒久的に失敗し続けるのは避ける)。
    """
    strict_findings: list[str] = []
    expected_counts: list[str] = []
    try:
        with get_ro_connection() as conn:
            for label, sql in _ORPHAN_CHECKS_STRICT:
                count = conn.execute(sql).fetchone()[0]
                if count:
                    strict_findings.append(f"{label}: {count}行")
            for label, sql in _ORPHAN_CHECKS_EXPECTED:
                count = conn.execute(sql).fetchone()[0]
                expected_counts.append(f"{label}={count}")
    except sqlite3.OperationalError as e:
        logger.warning(f"⚠️ DBを読めないため参照整合性チェックをスキップします: {e}")
        return None

    # 通知しない側も、FK を張れるか(=ステップ3に進めるか)の判断材料として
    # ログには必ず残す。運用者は logs/ を追えば推移が分かる。
    if expected_counts:
        logger.info(
            "参照整合性(マスタ退役により想定内): " + " / ".join(expected_counts)
            + " ※ sync_master_data がマスタ行を消しても履歴は残る設計のため通知しません"
        )

    if not strict_findings:
        return None
    return (
        "参照整合性が破れています(quest_users に存在しないユーザーを参照する行があります):\n"
        + "\n".join(f"  - {f}" for f in strict_findings)
        + "\n  → 通常運用で quest_users の行が消えることはありません。"
        "手動SQL等で親が消えた可能性があります(自動修正はしていません)"
    )


def _should_notify(anomaly_keys: List[str], now: datetime.datetime) -> bool:
    """同一の異常セットが継続している間の再通知を抑制する。

    異常のセット(チェック名の組)が前回通知時と同じなら RENOTIFY_INTERVAL_SEC
    が経過するまで再通知しない。セットが変化したら即座に通知する。
    """
    fingerprint = hashlib.sha256("|".join(sorted(anomaly_keys)).encode()).hexdigest()
    # Issue #661: 状態ファイルの読み書きは core/state_file.py に集約した。
    # 壊れている/読めない場合に「通知する」側へ倒す方針は従来どおり
    # (異常の通知を取りこぼすより、重複して通知する方が安全側)。
    state = state_file.read_json(NOTIFY_STATE_FILE)
    if isinstance(state, dict) and state.get("fingerprint") == fingerprint:
        try:
            last = datetime.datetime.fromisoformat(state["last_notified"])
            if (now - last).total_seconds() < RENOTIFY_INTERVAL_SEC:
                return False
        except (KeyError, TypeError, ValueError):
            pass

    state_file.write_json_atomic(
        NOTIFY_STATE_FILE, {"fingerprint": fingerprint, "last_notified": now.isoformat()}
    )
    return True


def _fire_investigate_hook(anomalies: List[str], now: datetime.datetime) -> None:
    """層2(自動調査)フックを発火する(Issue #339)。

    config.HEALTH_WATCH_INVESTIGATE_HOOK が未設定なら何もしない(既定)。
    設定されている場合、異常サマリを標準入力で渡してスクリプトを
    fire-and-forget のサブプロセスとして起動する(完了は待たない。
    調査は数分かかりうるが、毎時cronの層1を長時間ブロックしないため)。
    多重起動防止(flock)・タイムアウト・--max-budget-usd 等のガードレールは
    フックスクリプト側が持つ。本関数は _should_notify と同じ抑制の内側で
    呼ばれるため、同一異常セット継続中の再発火も通知と同じ6時間間隔に収まる。
    フックの起動失敗は層1の検知・通知を巻き込まない(ログのみ)。
    """
    hook = getattr(config, "HEALTH_WATCH_INVESTIGATE_HOOK", None)
    if not hook:
        return
    if not (os.path.isfile(hook) and os.access(hook, os.X_OK)):
        logger.error(f"調査フックが存在しないか実行権限がありません: {hook}")
        return

    summary = (
        f"検知時刻: {now.isoformat()}\n"
        + "\n".join(f"・{a}" for a in anomalies)
    )
    try:
        # フックの出力はrun_task.sh経由の自ログではなく専用ファイルへ残す
        # (check_app_logs はこのファイル名を明示的に除外するため自己発火しない)
        out_path = os.path.join(config.LOG_DIR, "claude_investigate.log")
        with open(out_path, "a", encoding="utf-8") as out:
            proc = subprocess.Popen(
                [hook],
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # 層1(本プロセス)終了後もフックを生かす
            )
        # Issue #761 (AUDIT-032): stdin=subprocess.PIPE を指定しているため None に
        # ならない。Popen の引数を変えたときに「'write' is not a known attribute of
        # 'None'」で落ちるより、ここで意図を表明しておく。
        assert proc.stdin is not None, "Popen に stdin=subprocess.PIPE を指定している"
        proc.stdin.write(summary.encode("utf-8"))
        proc.stdin.close()
        logger.info(f"調査フックを起動しました (pid={proc.pid}): {hook}")
    except Exception as e:
        logger.error(f"調査フックの起動に失敗しました: {e}")


def run_checks() -> int:
    """全チェックを実行し、異常があれば通知する。戻り値はプロセスの終了コード。"""
    now = datetime.datetime.now()
    since = _read_marker()
    logger.info(f"一次ヘルスチェック開始 (前回マーカー: {since.isoformat()})")

    checks = [
        ("service", check_service_active),
        ("api", check_api_responsive),
        ("journal", lambda: check_journal_errors(since)),
        ("app_logs", lambda: check_app_logs(since)),
        ("disk", check_disk_usage),
        ("memory", check_memory_usage),
        ("nas", check_nas_mount),
        ("recording", check_recording_stalled),
        ("deploy_config", check_deploy_config_drift),
        ("quest_master", check_quest_master_drift),
        ("orphaned_rows", check_orphaned_rows),
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
            # 層2フックは通知の成否に関わらず発火する(通知障害時こそ調査が必要)
            _fire_investigate_hook(anomalies, now)
        else:
            logger.info("同一の異常が継続中のため再通知を抑制しました")
    else:
        logger.info("異常なし")

    if internal_errors:
        exit_code = 1

    # マーカーは通知の成否に関わらず更新する(同じログ行の重複検知を防ぐ)
    _write_marker(now)
    return exit_code


def main() -> int:
    """エントリポイント。flock(LOCK_NB)で多重起動を防いでから run_checks() を実行する。

    Issue #651: 以前は多重起動防止が無く、外部コマンド(systemctl/journalctl)の応答待ちで前回の
    実行が残っていると、毎時 cron の次回起動が重なって二重に通知・マーカー更新していた。
    ロックが取れない場合は前回がまだ動いているとみなして 0 で終了する(異常ではない)。
    """
    lock_fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.warning("前回の health_watch がまだ実行中のためスキップします")
            return 0
        return run_checks()
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
