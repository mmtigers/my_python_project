# MY_HOME_SYSTEM/monitors/haircut_monitor.py
import imaplib
import email
import re
import sys
import os
import sqlite3
from datetime import datetime
from email.header import decode_header
from typing import Optional
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 自作モジュール
import config
from core.logger import setup_logging
from services.notification_service import send_push



# ログ設定
logger = setup_logging("HaircutMonitor")

class HaircutMonitor:
    """
    Gmailを監視し、HotPepper Beautyの予約完了メールを検知して
    LINE/Discord通知および既存のhome_system.dbへの記録を行うクラス
    """

    # 定数設定
    IMAP_SERVER = "imap.gmail.com"
    TARGET_SENDER = "reserve@beauty.hotpepper.jp"
    TARGET_SUBJECT = "ご予約が確定いたしました"
    DB_NAME = "home_system.db"

    def __init__(self):
        """初期設定: 環境変数ロードとDB準備"""
        # configから読み込む形に変更 (なければ.envから)
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self._load_environment()
        self._init_database()

    def _load_environment(self):
        """環境変数をロードし、必須項目をチェックする"""
        dotenv_path = os.path.join(self.base_dir, '.env')
        load_dotenv(dotenv_path)

        self.gmail_user = os.getenv("GMAIL_USER")
        self.gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        
        # LINE/Discordの設定は config.py や services.notification_service に委譲するが、
        # ここでは環境変数のチェックのみ行う
        if not self.gmail_user or not self.gmail_password:
            error_msg = "❌ 環境変数 (GMAIL_USER, GMAIL_APP_PASSWORD) が設定されていません。"
            logger.error(error_msg)
            # エラー通知も共通サービス経由で
            send_push(config.LINE_USER_ID, [{"type":"text", "text": error_msg}], target="discord", channel="error")
            raise ValueError(error_msg)
        
        logger.info("✅ 設定ロード完了")

    def _init_database(self):
        """データベース接続とテーブル作成"""
        db_path = os.path.join(self.base_dir, self.DB_NAME)
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS haircut_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_date TEXT UNIQUE,
                    created_at TEXT
                )
            ''')
            conn.commit()
            conn.close()
            logger.info(f"✅ データベース接続確認: {self.DB_NAME}")
        except Exception as e:
            logger.error(f"❌ DB初期化エラー: {e}")

    def _save_reservation(self, dt: datetime) -> bool:
        """予約日時をデータベースに保存 (True:新規, False:重複)"""
        db_path = os.path.join(self.base_dir, self.DB_NAME)
        date_str = dt.strftime('%Y-%m-%d %H:%M:%S')
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO haircut_history (reservation_date, created_at)
                VALUES (?, ?)
            ''', (date_str, now_str))
            
            rows_affected = conn.total_changes
            conn.commit()
            conn.close()

            if rows_affected > 0:
                logger.info(f"💾 DB保存成功: {date_str}")
                return True
            else:
                logger.info(f"⏭️ DB登録済みのためスキップ: {date_str}")
                return False

        except Exception as e:
            logger.error(f"❌ DB保存エラー: {e}")
            return False

    def _get_email_body(self, msg: email.message.Message) -> str:
        """メール本文抽出"""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return ""

    def _extract_reservation_date(self, body: str) -> Optional[datetime]:
        """日時抽出"""
        date_pattern = r'■来店日時\s*(\d{4}年\d{1,2}月\d{1,2}日.*?\d{1,2}:\d{2})'
        match = re.search(date_pattern, body, re.DOTALL)
        
        if match:
            raw_date_str = match.group(1)
            try:
                clean_date_str = re.sub(r'（.*?）', '', raw_date_str).strip()
                dt = datetime.strptime(clean_date_str, '%Y年%m月%d日%H:%M')
                logger.info(f"📅 日時抽出成功: {dt}")
                return dt
            except ValueError as e:
                logger.error(f"⚠️ 日時パースエラー: {e}")
                return None
        return None

    def _create_notification_message(self, dt: datetime, is_new: bool) -> str:
        """主婦層向け通知メッセージ作成"""
        date_str = dt.strftime('%Y年%m月%d日 %H:%M')
        if is_new:
            return (
                f"お疲れ様です🌿\n"
                f"美容院の予約を確認し、記録しました📝\n\n"
                f"🗓️ 日時: {date_str}\n\n"
                f"カレンダーにメモしておきますね！\n"
                f"楽しみですね😊"
            )
        else:
            return (
                f"再確認: 美容院の予約データは既に記録済みです🌿\n"
                f"🗓️ 日時: {date_str}"
            )

    def run(self):
        """メイン処理フロー"""
        logger.info("🚀 散髪予約モニターを開始します...")

        try:
            mail = imaplib.IMAP4_SSL(self.IMAP_SERVER)
            mail.login(self.gmail_user, self.gmail_password)
            mail.select("inbox")
            
            today_str = datetime.now().strftime("%d-%b-%Y")
            search_query = f'(FROM "{self.TARGET_SENDER}" SINCE "{today_str}")'
            
            logger.info(f"🔎 メール検索条件: {search_query}")
            status, messages = mail.search(None, search_query)
            email_ids = messages[0].split()

            if not email_ids:
                logger.info("✨ 新しい予約メールはありませんでした。")
                mail.logout()
                return

            target_id = email_ids[-1]
            _, msg_data = mail.fetch(target_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8")

            if self.TARGET_SUBJECT in subject:
                logger.info(f"📨 対象メールを発見: {subject}")
                body = self._get_email_body(msg)
                reservation_date = self._extract_reservation_date(body)

                if reservation_date:
                    is_new_record = self._save_reservation(reservation_date)
                    message = self._create_notification_message(reservation_date, is_new_record)
                    
                    # 統一通知サービスを使用 (LINEとDiscord両方へ送信可能)
                    send_push(
                        config.LINE_USER_ID, 
                        [{"type": "text", "text": message}], 
                        target="all" # LINEとDiscord両方に送る
                    )
                else:
                    logger.warning("⚠️ 日時抽出失敗")
                    send_push(config.LINE_USER_ID, [{"type":"text", "text": f"⚠️ 日時抽出失敗: {subject}"}], target="discord", channel="error")
            
            mail.close()
            mail.logout()
            logger.info("👋 処理完了")

        except Exception as e:
            logger.error(f"❌ 予期せぬエラー: {e}")
            send_push(config.LINE_USER_ID, [{"type":"text", "text": f"🚨 システムエラー(Haircut): {e}"}], target="discord", channel="error")

if __name__ == "__main__":
    monitor = HaircutMonitor()
    monitor.run()