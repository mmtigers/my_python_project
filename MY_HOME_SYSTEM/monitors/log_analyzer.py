import os
import glob
import re
import datetime
import logging
from typing import List, Dict, Any, Optional

# 自作モジュール
import config
import common

# ロガー設定
logger = common.setup_logging("log_analyzer")

class LogAnalyzer:
    """ログディレクトリおよびシステムログを走査し、システムエラーを集計・通知するクラス。"""

    # 監視対象のキーワード
    ERROR_KEYWORDS: List[str] = ["ERROR", "CRITICAL", "Traceback", "Exception", "Failed password"]
    WARN_KEYWORDS: List[str] = ["WARNING"]
    
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
                # もし未来の日付になってしまった場合（12/31に翌年1/1のログを読んだ場合など）、1年引く処理も必要だが
                # 今回は簡易的に「現在年」とする
                return dt
            except ValueError:
                pass
                
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

            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if any(ignore in line for ignore in self.IGNORE_PATTERNS):
                        continue

                    dt = self._parse_timestamp(line)
                    if dt and dt < self.start_date:
                        continue
                    
                    line_upper = line.upper()
                    
                    if any(k.upper() in line_upper for k in self.ERROR_KEYWORDS):
                        error_count += 1
                        last_error_snippet = line.strip()[:120] # 少し長めに
                    elif any(k.upper() in line_upper for k in self.WARN_KEYWORDS):
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
            common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord", channel="report")
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

        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord", channel="report")

if __name__ == "__main__":
    analyzer = LogAnalyzer(days_back=7)
    analyzer.run_analysis()