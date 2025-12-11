# HOME_SYSTEM/salary_analyzer.py
import os
import imaplib
import email
from email.header import decode_header
import email.utils
import pikepdf
import time
import argparse
import traceback
from datetime import datetime
from pdf2image import convert_from_path

# 自作モジュール
import config
import common

# === 定数・設定 ===
logger = common.setup_logging("salary_analyzer")

# 画像保存先 (configにない場合のフォールバック付き)
IMAGE_SAVE_DIR = getattr(config, 'SALARY_IMAGE_DIR', os.path.join(config.BASE_DIR, "..", "assets", "salary_images"))

class SalaryAnalyzer:
    """
    給与明細PDFをメールから取得し、画像化して保存するクラス
    (AI解析は行わず、アーカイブ作成に特化)
    """

    def __init__(self):
        self.mail = None
        self._setup_environment()

    def _setup_environment(self):
        """ディレクトリ作成などの初期設定"""
        if not os.path.exists(IMAGE_SAVE_DIR):
            try:
                os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)
                logger.info(f"📁 保存用フォルダを作成: {IMAGE_SAVE_DIR}")
            except OSError as e:
                logger.error(f"❌ フォルダ作成失敗: {e}")

    def connect_gmail(self) -> bool:
        """GmailへのIMAP接続"""
        try:
            self.mail = imaplib.IMAP4_SSL("imap.gmail.com")
            self.mail.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            self.mail.select("inbox")
            logger.info("✅ Gmail接続成功")
            return True
        except Exception as e:
            self._handle_error("Gmail接続エラー", e)
            return False

    def fetch_target_emails(self, limit=None) -> list:
        """対象のメールIDリストを取得"""
        if not self.mail: return []
        sender = config.SALARY_MAIL_SENDER
        if not sender:
            logger.warning("⚠️ SALARY_MAIL_SENDER が未設定です")
            return []
            
        try:
            # PDF添付があるメールを検索
            query = f'from:{sender} has:attachment filename:pdf'
            status, messages = self.mail.search(None, 'X-GM-RAW', f'"{query}"')
            
            if status != "OK": return []
            
            email_ids = messages[0].split()
            # 最新のものを取得
            if limit and len(email_ids) > limit:
                return email_ids[-limit:]
            return email_ids
        except Exception as e:
            self._handle_error("メール検索エラー", e)
            return []

    def _extract_pdf_and_date(self, email_id):
        """メールからPDFと受信日時を取得"""
        try:
            _, msg_data = self.mail.fetch(email_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            
            # 件名デコード
            subject = decode_header(msg["Subject"])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode('utf-8', errors='ignore')
            
            # 日時取得
            date_tuple = email.utils.parsedate_tz(msg['Date'])
            if date_tuple:
                local_date = datetime.fromtimestamp(email.utils.mktime_tz(date_tuple))
            else:
                local_date = datetime.now()

            logger.info(f"📨 件名: {subject} (受信日: {local_date})")

            # PDF抽出
            for part in msg.walk():
                filename = part.get_filename()
                if filename and filename.endswith(".pdf"):
                    save_path = os.path.join(IMAGE_SAVE_DIR, "temp_target.pdf")
                    with open(save_path, "wb") as f:
                        f.write(part.get_payload(decode=True))
                    return save_path, local_date
            
            return None, None
        except Exception as e:
            logger.warning(f"PDF抽出失敗 (ID: {email_id}): {e}")
            return None, None

    def _unlock_pdf(self, input_path) -> str:
        """PDFのパスワード解除"""
        output_path = input_path.replace(".pdf", "_unlocked.pdf")
        passwords = config.SALARY_PDF_PASSWORDS
        if isinstance(passwords, str): passwords = [passwords]
        
        for pwd in passwords:
            try:
                with pikepdf.open(input_path, password=pwd) as pdf:
                    pdf.save(output_path)
                logger.info("🔓 PDFパスワード解除成功")
                return output_path
            except: continue
            
        logger.error("❌ PDFの解除に失敗しました")
        return None

    def convert_and_save_image(self, pdf_path, date_obj) -> str:
        """PDFを画像に変換して保存"""
        try:
            # PDF -> 画像変換 (1ページ目のみ)
            images = convert_from_path(pdf_path, first_page=1, last_page=1)
            if not images: return None
            
            # ファイル名生成 (salary_YYYYMMDD_HHMMSS.jpg)
            filename = f"salary_{date_obj.strftime('%Y%m%d_%H%M%S')}.jpg"
            save_path = os.path.join(IMAGE_SAVE_DIR, filename)
            
            # 重複チェック（既にあったらスキップ）
            if os.path.exists(save_path):
                logger.info(f"ℹ️ 既に保存済みです: {filename}")
                return save_path

            # 保存
            images[0].save(save_path, "JPEG")
            logger.info(f"🖼️ 画像保存完了: {filename}")
            return save_path

        except Exception as e:
            self._handle_error("画像変換エラー", e)
            return None

    def notify_success(self, saved_count, last_image_path):
        """保存完了通知"""
        if saved_count == 0: return

        msg = (
            f"📥 **給与明細アーカイブ完了**\n"
            f"新しく {saved_count} 件の明細を画像として保存しました。\n"
            f"Gemini API制限のため、解析は行わず保存のみ完了しています。\n"
            f"ファイルは `assets/salary_images/` に格納されました。"
        )
        
        try:
            image_data = None
            if last_image_path and os.path.exists(last_image_path):
                with open(last_image_path, "rb") as f:
                    image_data = f.read()
            
            common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], image_data=image_data, target="discord", channel="report")
            logger.info("✅ 通知送信完了")
        except Exception as e:
            self._handle_error("通知送信エラー", e)

    def _handle_error(self, context, error):
        err_msg = f"{context}: {str(error)}"
        logger.error(err_msg)
        # エラーは管理用チャンネルへ
        common.send_push(
            config.LINE_USER_ID, 
            [{"type": "text", "text": f"😰 **Salary Saver Error**\n```{err_msg}```"}], 
            target="discord",
            channel="error"
        )

    def cleanup(self):
        """一時ファイルの削除とログアウト"""
        if self.mail:
            try: self.mail.logout()
            except: pass
        try:
            for f in ["temp_target.pdf", "temp_target_unlocked.pdf"]:
                p = os.path.join(IMAGE_SAVE_DIR, f)
                if os.path.exists(p): os.remove(p)
        except: pass

    def run(self, mode="normal", limit=None):
        logger.info(f"🚀 給与明細保存プロセス起動 (モード: {mode})")
        if not self.connect_gmail(): return

        fetch_limit = limit if limit else (None if mode == "history" else 3)
        email_ids = self.fetch_target_emails(fetch_limit)
        logger.info(f"📩 対象メール: {len(email_ids)} 件")
        
        saved_count = 0
        last_saved_image = None
        
        for i, email_id in enumerate(email_ids):
            try:
                # 1. PDFと日付の取得
                pdf_path, date_obj = self._extract_pdf_and_date(email_id)
                if not pdf_path or not date_obj: continue

                # 2. パスワード解除
                unlocked_path = self._unlock_pdf(pdf_path)
                if not unlocked_path: continue

                # 3. 画像化して保存 (AI解析はスキップ)
                saved_path = self.convert_and_save_image(unlocked_path, date_obj)
                if saved_path:
                    saved_count += 1
                    last_saved_image = saved_path
                
                # 連続アクセスの負荷軽減
                time.sleep(1)

            except Exception as e:
                logger.error(f"メール処理エラー (ID: {email_id}): {e}")

        if saved_count > 0:
            logger.info(f"🎉 {saved_count} 件の画像を保存しました")
            self.notify_success(saved_count, last_saved_image)
        else:
            logger.info("✨ 新しい保存対象はありませんでした")

        self.cleanup()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["normal", "history", "test"], default="normal")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    
    saver = SalaryAnalyzer()
    if args.mode == "test": saver.run(mode="normal", limit=1)
    elif args.mode == "history": saver.run(mode="history")
    else: saver.run(mode="normal")