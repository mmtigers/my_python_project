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
  7. 実機構成(crontab / systemdユニット / logrotate設定)がリポジトリの deploy/ 配下と
     一致しているか(構成ドリフト検知。各READMEの「実機を変更したらこのファイルにも
     反映してコミットすること」を人手に頼らず機械的に検知する)

異常があれば notification_service 経由でDiscordのerrorチャンネルへ要約を通知する。
自動復旧(systemctl restart等)は行わない(ランブックのガードレール参照)。

層2(Issue #339): config.HEALTH_WATCH_INVESTIGATE_HOOK にスクリプトパスが
設定されている場合のみ、通知と同じ抑制の内側で自動調査フック
(scripts/claude_investigate.sh)を fire-and-forget 起動する。未設定なら
従来どおり検知・通知のみ。
"""

import datetime
import difflib
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

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

# === 実機構成のドリフト検知 (チェック7) ===
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
    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
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


def check_deploy_config_drift() -> Optional[str]:
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


def _fire_investigate_hook(anomalies: List[str], now: datetime.datetime) -> None:
    """層2(自動調査)フックを発火する(Issue #339)。

    config.HEALTH_WATCH_INVESTIGATE_HOOK が未設定なら何もしない(既定)。
    設定されている場合、異常サマリを標準入力で渡してスクリプトを
    fire-and-forget のサブプロセスとして起動する(完了は待たない。
    調査は数分かかりうるが、毎時cronの層1を長時間ブロックしないため)。
    多重起動防止(flock)・タイムアウト・--max-turns 等のガードレールは
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
        ("journal", lambda: check_journal_errors(since)),
        ("app_logs", lambda: check_app_logs(since)),
        ("disk", check_disk_usage),
        ("memory", check_memory_usage),
        ("nas", check_nas_mount),
        ("deploy_config", check_deploy_config_drift),
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


if __name__ == "__main__":
    sys.exit(run_checks())
