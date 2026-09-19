import os
import glob
import re
import datetime
from typing import List, Dict, Any, Optional

# 自作モジュール
import config
from core.logger import setup_logging
from services.notification_service import send_push

# ロガー設定
logger = setup_logging("log_analyzer")

class LogAnalyzer:
    """ログディレクトリおよびシステムログを走査し、システムエラーを集計・通知するクラス。"""

    # 監視対象のキーワード
    ERROR_KEYWORDS: List[str] = ["ERROR", "CRITICAL", "Traceback", "Exception", "Failed password"]
    WARN_KEYWORDS: List[str] = ["WARNING"]

    # ログレベルが明示された行の判定(2026-09-19)。以前は全行を上記キーワードの部分一致だけで
    # 判定していたため、`[WARNING] ... Unknown error: ... NewConnectionError(...)` のように
    # 本文に "error"/"Exception" を含む警告行がエラーとして数えられていた。実機の2日分の
    # ログでは「エラー」728件のうち711件(97.7%)がこの誤検知で、health_watch の app_logs が
    # 常時「異常」になり、同一異常の再通知抑制で本物の異常が埋もれる原因になっていた。
    # レベル表記を持つ行はレベルで判定し、キーワード判定はレベル表記の無い行
    # (トレースバック継続行・syslog・uvicorn 等)のフォールバックとしてだけ使う。
    #   1. core/logger の書式: '2026-09-19 10:05:02 [WARNING] camera: ...'
    #   2. logging の既定書式(ライブラリが basicConfig のまま出す行): 'ERROR:zeep.xsd...:...'
    LEVEL_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r'^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]'),
        re.compile(r'^(DEBUG|INFO|WARNING|ERROR|CRITICAL):'),
    )
    ERROR_LEVELS: tuple[str, ...] = ("ERROR", "CRITICAL")
    
    # ノイズ対策: 無視するキーワード
    IGNORE_PATTERNS: List[str] = [
        "Connection reset by peer",      # 通信切断
        "InsecureRequestWarning",        # SSL警告
        "warnings.warn",                 # ライブラリ警告
        "Retrying...",                   # リトライ
        "log_analyzer",                  # 自分自身
        "sudo:",                         # sudo使用履歴(通常ログ)
        "CRON",                          # CRON実行履歴
    ]

    # 追加: 監視したい外部システムログの絶対パス
    SYSTEM_LOGS: List[str] = [
        "/var/log/syslog",
        "/var/log/auth.log"
    ]

    def __init__(self, days_back: int = 7) -> None:
        self.days_back = days_back
        self.log_dir = config.LOG_DIR
        self.report_data: Dict[str, Dict[str, Any]] = {}
        
        # 基準日時
        self.now = datetime.datetime.now()
        self.start_date = self.now - datetime.timedelta(days=self.days_back)
        self.start_date_str = self.start_date.strftime('%Y-%m-%d')

    def _is_recent_file(self, filepath: str) -> bool:
        """ファイルの更新日時チェック"""
        if not os.path.exists(filepath):
            return False
        try:
            mtime = os.path.getmtime(filepath)
            mod_time = datetime.datetime.fromtimestamp(mtime)
            # ログローテーションされている場合もあるため、ファイル自体が古くても
            # 中身に新しいログがある可能性があるが、ここではファイル更新日時で足切りする
            return mod_time >= self.start_date
        except (OSError, PermissionError):
            return False

    def _parse_timestamp(self, line: str) -> Optional[datetime.datetime]:
        """
        ログ行頭のタイムスタンプを解析します。
        対応フォーマット:
        1. '2025-12-27 10:00:00' (Python Logger)
        2. 'Dec 27 10:00:00' (Syslog / auth.log)
        """
        # Pattern 1: ISO Like (YYYY-MM-DD HH:MM:SS)
        match_iso = re.match(r'^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})', line)
        if match_iso:
            try:
                return datetime.datetime.strptime(match_iso.group(1), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass

        # Pattern 2: Syslog (Mmm DD HH:MM:SS) -> 年情報がないため現在年を補完
        match_sys = re.match(r'^([A-Z][a-z]{2}\s+\d{1,2}\s\d{2}:\d{2}:\d{2})', line)
        if match_sys:
            try:
                ts_str = match_sys.group(1)
                # 'Dec 27 10:00:00' -> datetime obj (年は1900になる)
                dt = datetime.datetime.strptime(ts_str, '%b %d %H:%M:%S')
                # 年を補正 (現在年)
                dt = dt.replace(year=self.now.year)
                # syslog 形式には年が無いため、年明け直後に前年12月のログを読むと「現在年の12月」
                # (=未来)になり、start_date のフィルタを素通りして必ずカウントされていた。
                # 現在時刻より1日以上未来なら前年のログとみなして1年戻す。
                if dt > self.now + datetime.timedelta(days=1):
                    dt = dt.replace(year=self.now.year - 1)
                return dt
            except ValueError:
                pass
                
        return None

    def _classify_line(self, line: str) -> str | None:
        """1行の重大度を "error" / "warning" / None で返す。

        ログレベルの表記がある行はレベルで判定する(本文の語句には依存しない)。
        表記が無い行だけ、従来どおり ERROR_KEYWORDS / WARN_KEYWORDS の部分一致で判定する。
        """
        for pattern in self.LEVEL_PATTERNS:
            match = pattern.match(line)
            if match:
                level = match.group(1)
                if level in self.ERROR_LEVELS:
                    return "error"
                if level == "WARNING":
                    return "warning"
                return None

        line_upper = line.upper()
        if any(k.upper() in line_upper for k in self.ERROR_KEYWORDS):
            return "error"
        if any(k.upper() in line_upper for k in self.WARN_KEYWORDS):
            return "warning"
        return None

    def _analyze_file(self, filepath: str) -> None:
        """1つのログファイルを解析"""
        filename = os.path.basename(filepath)
        error_count = 0
        warn_count = 0
        last_error_snippet: Optional[str] = None
        
        try:
            # システムログなどで権限エラーが出る可能性を考慮
            if not os.access(filepath, os.R_OK):
                logger.warning(f"⚠️ 読み取り権限がありません: {filepath}")
                return

            # #381: トレースバック継続行("Traceback (most recent call last):" や
            # "    File ..." / "XxxError: ...")にはタイムスタンプが無いため、以前は
            # start_date のフィルタを素通りして必ずカウントされ、1回トレースバックが出ると
            # その日 logrotate されるまで毎時「異常」が立ち続けていた。直前にパースできた
            # タイムスタンプを継続行のタイムスタンプとして引き継いで判定する。
            last_dt = None
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if any(ignore in line for ignore in self.IGNORE_PATTERNS):
                        continue

                    dt = self._parse_timestamp(line)
                    if dt is not None:
                        last_dt = dt
                    effective_dt = dt if dt is not None else last_dt
                    if effective_dt and effective_dt < self.start_date:
                        continue
                    
                    severity = self._classify_line(line)
                    if severity == "error":
                        error_count += 1
                        last_error_snippet = line.strip()[:120] # 少し長めに
                    elif severity == "warning":
                        warn_count += 1

            if error_count > 0 or warn_count > 0:
                self.report_data[filename] = {
                    "errors": error_count,
                    "warnings": warn_count,
                    "last_error": last_error_snippet
                }
                logger.info(f"   📄 {filename}: Errors={error_count}, Warnings={warn_count}")

        except Exception as e:
            logger.error(f"ファイル解析エラー ({filename}): {e}")

    def run_analysis(self) -> None:
        """全ログファイルの解析を実行"""
        logger.info(f"🔍 ログ分析開始 (期間: 過去{self.days_back}日間)")
        
        # 1. アプリケーションログ (logs/*.log)
        target_files = glob.glob(os.path.join(self.log_dir, "*.log"))
        
        # 2. システムログを追加
        target_files.extend(self.SYSTEM_LOGS)

        count_checked = 0
        
        for filepath in target_files:
            if self._is_recent_file(filepath):
                self._analyze_file(filepath)
                count_checked += 1
        
        logger.info(f"✅ 解析完了: {count_checked}/{len(target_files)} ファイルをチェックしました")
        self._send_report()

    def _send_report(self) -> None:
        """集計結果を通知"""
        target_period = f"{self.start_date.strftime('%m/%d')}～{self.now.strftime('%m/%d')}"
        
        if not self.report_data:
            msg = (
                f"📊 **週間ログ分析レポート ({target_period})**\n\n"
                f"✅ **異常なし**\nシステムログ・サーバー含め正常です✨"
            )
            send_push([{"type": "text", "text": msg}], target="discord", channel="report")
            return

        total_errors = sum(d["errors"] for d in self.report_data.values())
        total_warns = sum(d["warnings"] for d in self.report_data.values())
        
        msg = f"📊 **週間ログ分析レポート ({target_period})**\n"
        msg += f"⚠️ **{total_errors}件のエラー**、{total_warns}件の警告\n"
        msg += "━━━━━━━━━━━━━━━━━━━\n"
        
        for filename, data in self.report_data.items():
            e_cnt = data['errors']
            w_cnt = data['warnings']
            icon = "🚨" if e_cnt > 0 else "⚠️"
            
            # ファイル名を目立たせる
            msg += f"**{icon} {filename}** (Err:{e_cnt}, Warn:{w_cnt})\n"
            
            if data['last_error']:
                snippet = data['last_error'].replace("`", "'")
                msg += f"└ `{snippet}...`\n"
            msg += "\n"

        msg += "━━━━━━━━━━━━━━━━━━━\n"
        msg += "※ `logs/` または `/var/log/` を確認してください。"

        send_push([{"type": "text", "text": msg}], target="discord", channel="report")

if __name__ == "__main__":
    analyzer = LogAnalyzer(days_back=7)
    analyzer.run_analysis()