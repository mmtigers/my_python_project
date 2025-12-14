import sqlite3
import os
import logging
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('HaircutAdvisor')

class HaircutAdvisor:
    """
    過去の散髪履歴を分析し、次回の散髪時期を提案するクラス
    """
    
    DB_NAME = "home_system.db"
    DEFAULT_INTERVAL_DAYS = 50  # データ不足時のデフォルト周期（約1.5ヶ月）
    NOTIFY_DAYS_BEFORE = 7      # 何日前から通知するか
    REQUEST_TIMEOUT = 10        # 秒

    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self._load_environment()

    def _load_environment(self):
        dotenv_path = os.path.join(self.base_dir, '.env')
        load_dotenv(dotenv_path)
        self.line_token = os.getenv("LINE_ACCESS_TOKEN")
        # 修正: monitorと同じ変数名を使用するよう統一
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_NOTIFY")

        if not self.discord_webhook:
            logger.warning("⚠️ Discord Webhook URLが設定されていません。通知は届きません。")

    def _get_history(self):
        """DBから予約履歴を取得して日付順にソートして返す"""
        db_path = os.path.join(self.base_dir, self.DB_NAME)
        if not os.path.exists(db_path):
            logger.error(f"❌ データベースが見つかりません: {db_path}")
            return []

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT reservation_date FROM haircut_history ORDER BY reservation_date ASC")
            rows = cursor.fetchall()
            conn.close()
            
            history = []
            for row in rows:
                try:
                    dt = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
                    history.append(dt)
                except ValueError:
                    continue
            return history
        except Exception as e:
            logger.error(f"❌ DB読み込みエラー: {e}")
            self._send_discord_error(f"DB読み込みエラー: {e}")
            return []

    def calculate_next_date(self):
        """次回の推奨日を計算する"""
        history = self._get_history()
        
        if not history:
            logger.warning("📭 データがないため分析できません。")
            return None, None

        last_date = history[-1]
        
        # 間隔の計算
        if len(history) >= 2:
            intervals = []
            for i in range(len(history) - 1):
                delta = history[i+1] - history[i]
                intervals.append(delta.days)
            
            avg_interval = sum(intervals) / len(intervals)
            logger.info(f"📊 過去{len(history)}回のデータから平均サイクルを算出: {avg_interval:.1f}日")
            
            next_date = last_date + timedelta(days=avg_interval)
            return next_date, int(avg_interval)
        else:
            logger.info(f"ℹ️ データ不足のためデフォルト周期({self.DEFAULT_INTERVAL_DAYS}日)を適用します")
            next_date = last_date + timedelta(days=self.DEFAULT_INTERVAL_DAYS)
            return next_date, self.DEFAULT_INTERVAL_DAYS

    def suggest(self, force_notify=False):
        """分析と提案の実行"""
        logger.info("🧠 散髪サイクルの分析を開始...")
        
        next_date, interval = self.calculate_next_date()
        
        if not next_date:
            return

        today = datetime.now()
        days_until = (next_date - today).days
        
        next_date_str = next_date.strftime('%Y年%m月%d日')
        
        logger.info(f"📅 最新カット: {self._get_history()[-1].strftime('%Y/%m/%d')}")
        logger.info(f"🔮 次回予測日: {next_date_str} (あと{days_until}日)")

        if days_until <= self.NOTIFY_DAYS_BEFORE or force_notify:
            self._send_suggestion(next_date_str, interval, days_until)
        else:
            logger.info("✨ まだ通知時期ではありません。")

    def _send_suggestion(self, next_date_str, interval, days_until):
        """通知メッセージの送信"""
        
        if days_until > 0:
            msg_status = f"そろそろご予約の時期が近づいています✂️\n(目安: あと{days_until}日)"
        elif days_until == 0:
            msg_status = "今日が散髪の目安日です！✂️"
        else:
            msg_status = f"散髪の目安日から{abs(days_until)}日経過しています😮\nお時間ある時にいかがですか？"

        message = (
            f"こんにちは🌿\n"
            f"{msg_status}\n\n"
            f"📅 次回の目安: {next_date_str}\n"
            f"🔄 平均ペース: 約{interval}日ごと\n\n"
            f"リフレッシュしてきてくださいね😊"
        )

        self._send_line(message)
        self._send_discord(message)

    def _send_line(self, message):
        if not self.line_token: return
        try:
            requests.post(
                "https://notify-api.line.me/api/notify",
                headers={"Authorization": f"Bearer {self.line_token}"},
                data={"message": "\n" + message},
                timeout=self.REQUEST_TIMEOUT
            )
            logger.info("✅ LINEで提案を送りました")
        except Exception as e:
            logger.error(f"❌ LINE送信エラー: {e}")

    def _send_discord(self, message):
        if not self.discord_webhook: return
        try:
            requests.post(
                self.discord_webhook, 
                json={"content": message},
                timeout=self.REQUEST_TIMEOUT
            )
            logger.info("✅ Discordで提案を送りました")
        except Exception as e:
            logger.error(f"❌ Discord送信エラー: {e}")
            
    def _send_discord_error(self, error_message: str):
        if not self.discord_webhook: return
        try:
            requests.post(
                self.discord_webhook, 
                json={"content": f"🚨 **エラー発生(Advisor)** 🚨\n```\n{error_message}\n```"},
                timeout=self.REQUEST_TIMEOUT
            )
        except Exception: pass

if __name__ == "__main__":
    advisor = HaircutAdvisor()
    # テスト実行: 強制通知モードON
    advisor.suggest(force_notify=True)