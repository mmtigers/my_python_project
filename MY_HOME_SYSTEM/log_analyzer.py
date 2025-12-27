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
    """ログディレクトリ内のファイルを走査し、システムエラーを集計・通知するクラス。

    Attributes:
        days_back (int): 分析対象とする過去の日数。
        log_dir (str): ログファイルが格納されているディレクトリパス。
        report_data (Dict[str, Dict[str, Any]]): 分析結果を保持する辞書。
        now (datetime.datetime): 分析実行時の現在時刻。
        start_date (datetime.datetime): 分析対象期間の開始日時。
    """

    # 監視対象のキーワード (大文字小文字区別なしでチェック)
    ERROR_KEYWORDS: List[str] = ["ERROR", "CRITICAL", "Traceback", "Exception"]
    WARN_KEYWORDS: List[str] = ["WARNING"]
    
    # ノイズ対策: 無視するキーワードリスト (部分一致)
    IGNORE_PATTERNS: List[str] = [
        "Connection reset by peer",      # よくある通信切断
        "InsecureRequestWarning",        # SSL警告
        "warnings.warn",                 # ライブラリ内部の警告
        "Retrying...",                   # 想定内のリトライ
        "log_analyzer",                  # 自分自身のログ
    ]

    def __init__(self, days_back: int = 7) -> None:
        """LogAnalyzerを初期化します。

        Args:
            days_back (int, optional): 何日前までのログを対象にするか。Defaults to 7.
        """
        self.days_back = days_back
        self.log_dir = config.LOG_DIR
        # Structure: {filename: {"errors": int, "warnings": int, "last_error": str}}
        self.report_data: Dict[str, Dict[str, Any]] = {}
        
        # 基準日時の計算
        self.now = datetime.datetime.now()
        self.start_date = self.now - datetime.timedelta(days=self.days_back)
        self.start_date_str = self.start_date.strftime('%Y-%m-%d')

    def _is_recent_file(self, filepath: str) -> bool:
        """ファイルの最終更新日時が対象期間内かチェックします。

        Args:
            filepath (str): 対象ファイルのパス。

        Returns:
            bool: 対象期間内に更新されていればTrue。
        """
        try:
            mtime = os.path.getmtime(filepath)
            mod_time = datetime.datetime.fromtimestamp(mtime)
            return mod_time >= self.start_date
        except OSError:
            return False

    def _parse_timestamp(self, line: str) -> Optional[datetime.datetime]:
        """ログ行頭のタイムスタンプ (YYYY-MM-DD HH:MM:SS) を解析します。

        Args:
            line (str): ログの1行。

        Returns:
            Optional[datetime.datetime]: 解析できた場合はdatetimeオブジェクト、不可ならNone。
        """
        # 一般的なフォーマット: 2023-01-01 12:00:00 ...
        match = re.match(r'^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})', line)
        if match:
            try:
                return datetime.datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
        return None

    def _analyze_file(self, filepath: str) -> None:
        """1つのログファイルを解析し、エラー・警告を集計して self.report_data に格納します。

        Args:
            filepath (str): 解析対象のログファイルパス。
        """
        filename = os.path.basename(filepath)
        error_count = 0
        warn_count = 0
        last_error_snippet: Optional[str] = None
        
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    # 1. 除外キーワードチェック
                    if any(ignore in line for ignore in self.IGNORE_PATTERNS):
                        continue

                    # 2. 日付チェック
                    # (日付がある行は日付判定、ない行は常にチェック対象とする既存ロジックを維持)
                    dt = self._parse_timestamp(line)
                    if dt and dt < self.start_date:
                        continue
                    
                    # 3. キーワード検知
                    line_upper = line.upper()
                    
                    # ERROR系
                    if any(k.upper() in line_upper for k in self.ERROR_KEYWORDS):
                        error_count += 1
                        # 長すぎる行は切り詰める (最大100文字)
                        last_error_snippet = line.strip()[:100]
                    
                    # WARNING系
                    elif any(k.upper() in line_upper for k in self.WARN_KEYWORDS):
                        warn_count += 1

            # 集計結果を保存 (エラーか警告があった場合のみ)
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
        """全ログファイルの解析を実行し、レポートを送信します。"""
        logger.info(f"🔍 ログ分析開始 (期間: 過去{self.days_back}日間, 基準: {self.start_date_str}以降)")
        
        target_files = glob.glob(os.path.join(self.log_dir, "*.log"))
        count_checked = 0
        
        for filepath in target_files:
            if self._is_recent_file(filepath):
                self._analyze_file(filepath)
                count_checked += 1
        
        logger.info(f"✅ 解析完了: {count_checked}/{len(target_files)} ファイルをチェックしました")
        self._send_report()

    def _send_report(self) -> None:
        """集計結果に基づき、Discordへ通知を送信します。"""
        target_period = f"{self.start_date.strftime('%m/%d')}～{self.now.strftime('%m/%d')}"
        
        # 異常なしの場合
        if not self.report_data:
            msg = (
                f"📊 **週間ログ分析レポート ({target_period})**\n\n"
                f"✅ **異常なし**\nすべてのシステムが正常に稼働しています✨"
            )
            common.send_push(
                config.LINE_USER_ID, 
                [{"type": "text", "text": msg}], 
                target="discord", 
                channel="report"
            )
            return

        # 異常ありの場合
        total_errors = sum(d["errors"] for d in self.report_data.values())
        total_warns = sum(d["warnings"] for d in self.report_data.values())
        
        msg = f"📊 **週間ログ分析レポート ({target_period})**\n"
        msg += f"⚠️ **{total_errors}件のエラー**、{total_warns}件の警告を検知しました。\n"
        msg += "━━━━━━━━━━━━━━━━━━━\n"
        
        # ファイルごとの詳細
        for filename, data in self.report_data.items():
            e_cnt = data['errors']
            w_cnt = data['warnings']
            
            # エラーがあるファイルを優先表示
            icon = "🚨" if e_cnt > 0 else "⚠️"
            msg += f"**{icon} {filename}** (Err: {e_cnt}, Warn: {w_cnt})\n"
            
            if data['last_error']:
                # コードブロックで抜粋を表示 (マークダウン崩れ防止)
                snippet = data['last_error'].replace("`", "'")
                msg += f"└ 最新: `{snippet}...`\n"
            msg += "\n"

        msg += "━━━━━━━━━━━━━━━━━━━\n"
        msg += "※ 詳細はサーバーの `logs/` ディレクトリを確認してください。"

        # 通知送信 (Discordのレポートチャンネルへ)
        common.send_push(
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}], 
            target="discord", 
            channel="report"
        )

if __name__ == "__main__":
    analyzer = LogAnalyzer(days_back=7)
    analyzer.run_analysis()