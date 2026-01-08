# MY_HOME_SYSTEM/shopping_monitor.py
import imaplib
import email
from email.header import decode_header
import re
import datetime
import sys
import os
import traceback
from typing import Optional, Dict, List, Any

# 外部ライブラリ
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

import common
import config

logger = common.setup_logging("shopping_monitor")

# デバッグ用ディレクトリ
DEBUG_DIR = os.path.join(config.BASE_DIR, "debug_output")
if not os.path.exists(DEBUG_DIR):
    os.makedirs(DEBUG_DIR, exist_ok=True)

class ShoppingMonitor:
    """
    ECサイト(Amazon, Rakuten等)の注文確認メールを解析し、購入履歴をDBに記録するクラス
    【完結版】文字化け強力補正 & 「単価x個数」行からの商品名逆探知ロジック搭載
    """
    def __init__(self):
        self.mail = None
        self.new_records = []

    def connect_gmail(self) -> bool:
        if not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
            logger.error("❌ Gmail設定（ユーザー/アプリパスワード）が不足しています。")
            return False
        try:
            self.mail = imaplib.IMAP4_SSL("imap.gmail.com")
            self.mail.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            self.mail.select("inbox")
            logger.info("✅ Gmail接続成功")
            return True
        except Exception as e:
            self._handle_error("Gmail接続エラー", e)
            return False

    def _get_imap_date(self) -> str:
        return datetime.datetime.now().strftime("%d-%b-%Y")

    def _search_by_sender_today(self, sender: str) -> List[str]:
        try:
            date_str = self._get_imap_date()
            criterion = f'(FROM "{sender}" ON "{date_str}")'
            logger.info(f"   検索条件: {criterion}")
            status, messages = self.mail.search(None, criterion)
            if status != "OK":
                return []
            ids = messages[0].split()
            return [i for i in ids if i]
        except Exception as e:
            logger.error(f"❌ 検索コマンド実行エラー: {e}")
            return []

    def _decode_payload(self, part) -> str:
        """メール本文を正しい文字コードでデコードする (JIS最優先)"""
        payload = part.get_payload(decode=True)
        if not payload: return ""

        # 候補リスト: ISO-2022-JPを最優先にする（エスケープ有無に関わらず試す）
        encodings = ['iso-2022-jp', 'utf-8', 'cp932', 'euc-jp', 'shift_jis']
        charset = part.get_content_charset()
        if charset and charset not in encodings:
            encodings.insert(0, charset)

        for enc in encodings:
            try:
                return payload.decode(enc)
            except: continue
        
        return payload.decode('utf-8', errors='replace')

    def _clean_text(self, text: str) -> str:
        if BeautifulSoup:
            try:
                soup = BeautifulSoup(text, "html.parser")
                for tag in soup(["script", "style", "head", "title", "meta"]): tag.decompose()
                # 改行を多めに入れて構造を維持
                return soup.get_text(separator="\n", strip=True)
            except: pass
        return re.sub(r'<[^>]+>', ' ', text)

    def _clean_price_str(self, price_str: str) -> int:
        try:
            clean = price_str.translate(str.maketrans({chr(0xFF01 + i): chr(0x21 + i) for i in range(94)})).replace(",", "")
            return int(clean)
        except: return 0

    def _find_item_by_price_line(self, text: str) -> str:
        """
        【逆探知ロジック】
        「1,850円 x 1個」のような行を見つけ、その『数行前』にある文字列を商品名として採用する
        """
        lines = text.splitlines()
        # パターン: 数値(円) x 数値(個)
        # 例: 1,850円 x 1個 = 1,850円
        #     1850円x1
        regex = re.compile(r'[0-9,]+円?\s*[x×]\s*[0-9]+')
        
        for i, line in enumerate(lines):
            if regex.search(line):
                # この行が見つかったら、そこから上に向かって「商品名っぽい行」を探す
                # 1〜5行上を探索
                for j in range(1, 6):
                    if i - j < 0: break
                    candidate = lines[i - j].strip()
                    # 空行や「お届け目安」などの定型文はスキップ
                    if not candidate: continue
                    if "お届け" in candidate or "注" in candidate or "カラー" in candidate or "サイズ" in candidate: continue
                    
                    # これが商品名の可能性が高い
                    return candidate
        return ""

    def _parse_amazon(self, text_body: str, subject: str) -> Dict:
        data = {"price": 0, "item": "不明な商品"}
        
        match_a = re.search(r'Amazon\.co\.jpのご注文:\s*"([^"]+)"', subject)
        match_b = re.search(r'注文済み[：:]\s*["「]([^"」]+)["」]', subject)
        if match_a: data["item"] = match_a.group(1)[:40] + "..."
        elif match_b: data["item"] = match_b.group(1)[:40] + "..."
        else: data["item"] = subject.replace("Amazon.co.jpのご注文:", "").replace("注文済み:", "").strip()[:40]

        patterns = [r'(?:注文合計|ご請求額|合計|お支払い金額|領収書合計)(?:税込)?[ ：:\u3000]*[\s\n]*[￥¥]?[\s\n]*([0-9,]+)']
        for pat in patterns:
            matches = re.findall(pat, text_body)
            for m in matches:
                val = self._clean_price_str(m)
                if val > 0 and val != 2025: 
                    data["price"] = val
                    break
            if data["price"] > 0: break
            
        if data["price"] == 0:
            matches = re.findall(r'[￥¥][\s\n]*([0-9,]+)', text_body)
            candidates = [self._clean_price_str(m) for m in matches if self._clean_price_str(m) > 0]
            if candidates: data["price"] = max(candidates)

        return data

    def _parse_rakuten(self, text_body: str, subject: str) -> Dict:
        data = {"price": 0, "item": "楽天での購入品"}

        # 1. 金額抽出
        price_patterns = [
            r'(?:[\[【]?(?:合計|お?支払い?金額|ご?請求金額|ポイント利用後|差引支払金額|総計)[\]】]?)(?:税込)?[ ：:\u3000]*[\s\n]*([0-9,]+)',
        ]
        for pat in price_patterns:
            matches = re.findall(pat, text_body)
            for m in matches:
                val = self._clean_price_str(m)
                if val > 0:
                    data["price"] = val
                    break
            if data["price"] > 0: break

        # 2. 商品名抽出
        # A. テキストメールの [商品] ラベルを探す
        item_match = re.search(r'(?:\[商品\]|商品名)\s*[:：]?\s*(.+)', text_body)
        
        # B. HTMLメールの「単価 x 個数」行からの逆探知
        if not item_match:
            detected_name = self._find_item_by_price_line(text_body)
            if detected_name:
                data["item"] = detected_name[:40] + "..."
        else:
            data["item"] = item_match.group(1).strip()[:40] + "..."

        # C. それでもダメなら件名から
        if data["item"] == "楽天での購入品":
            clean = subject.replace("【楽天市場】", "").replace("注文内容ご確認", "").replace("ご注文内容の確認", "").strip()
            clean = re.sub(r'\[.+?\]', '', clean).strip()
            if clean: data["item"] = clean[:40]

        return data

    def save_record(self, platform: str, order_date: str, item: str, price: int, email_id: str) -> bool:
        # 0円でも保存
        try:
            with common.get_db_cursor(commit=True) as cur:
                cur.execute(f"SELECT id FROM {config.SQLITE_TABLE_SHOPPING} WHERE email_id=?", (email_id,))
                if cur.fetchone(): return False

                vals = (platform, order_date, item, price, email_id, common.get_now_iso())
                cols = ["platform", "order_date", "item_name", "price", "email_id", "timestamp"]
                placeholders = ", ".join(["?"] * len(vals))
                columns = ", ".join(cols)
                cur.execute(f"INSERT INTO {config.SQLITE_TABLE_SHOPPING} ({columns}) VALUES ({placeholders})", vals)
                self.new_records.append({"platform": platform, "item": item, "price": price})
                return True
        except Exception as e:
            logger.error(f"DB保存エラー: {e}")
        return False

    def notify_user(self):
        count = len(self.new_records)
        if count == 0: return

        total_price = sum([r["price"] for r in self.new_records])
        item_lines = []
        for r in self.new_records[:3]:
            icon = "📦" if r["platform"] == "Amazon" else "🛍️"
            price_str = f"{r['price']:,}円" if r["price"] > 0 else "金額不明"
            item_lines.append(f"{icon} {r['item']} ({price_str})")
        if count > 3: item_lines.append(f"（他 {count - 3} 件）")
        items_str = "\n".join(item_lines)

        msg = (
            f"🛒 **ネットショッピング記録**\n"
            f"家計簿にメモしました📝\n\n"
            f"{items_str}\n\n"
            f"💰 推定合計: {total_price:,} 円"
        )
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord", channel="report")
        logger.info(f"通知送信完了: {count}件")

    def _save_debug_log(self, platform: str, subject: str, body: str):
        try:
            safe_sub = re.sub(r'[\\/:*?"<>|]', '_', subject)[:20]
            filename = f"fail_{platform}_{datetime.datetime.now().strftime('%H%M%S')}_{safe_sub}.txt"
            path = os.path.join(DEBUG_DIR, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"Subject: {subject}\n\n--- BODY ---\n{body}")
            return filename
        except: return "error"

    def run(self):
        logger.info("🚀 ショッピング履歴監視を開始します...")
        if not self.connect_gmail(): return

        try:
            total_checked = 0
            for target in config.SHOPPING_TARGETS:
                platform = target["platform"]
                sender = target.get("sender")
                keywords = target.get("subject_keywords", [])
                if isinstance(keywords, str): keywords = [keywords]
                if not sender: continue
                
                logger.info(f"🔎 {platform} ({sender}) のメールを検索中...")
                email_ids = self._search_by_sender_today(sender)
                
                if email_ids:
                    logger.info(f"   本日の受信: {len(email_ids)} 件")
                    for eid in email_ids:
                        self._process_single_email(eid, platform, keywords)
                    total_checked += len(email_ids)
                else:
                    logger.info(f"   本日の受信なし")

            if self.new_records:
                self.notify_user()
            else:
                logger.info(f"✨ 新しい注文はありませんでした (処理数: {total_checked})")
        except Exception as e:
            logger.error(f"実行時エラー: {e}")
            logger.debug(traceback.format_exc())
        finally:
            try: self.mail.logout()
            except: pass

    def _process_single_email(self, email_id, platform, keywords):
        order_date = common.get_today_date_str()
        try:
            eid_str = email_id.decode('utf-8') if isinstance(email_id, bytes) else str(email_id)
            res, msg_data = self.mail.fetch(email_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = "No Subject"
            if msg["Subject"]:
                decoded_list = decode_header(msg["Subject"])
                parts = []
                for chunk, encoding in decoded_list:
                    if isinstance(chunk, bytes):
                        parts.append(chunk.decode(encoding or 'utf-8', errors='ignore'))
                    else: parts.append(str(chunk))
                subject = "".join(parts)
            
            is_target = False
            for k in keywords:
                if k in subject:
                    is_target = True
                    break
            if not is_target: return

            date_tuple = email.utils.parsedate_tz(msg['Date'])
            if date_tuple:
                local_dt = datetime.datetime.fromtimestamp(email.utils.mktime_tz(date_tuple))
                order_date = local_dt.strftime("%Y-%m-%d")

            html_body = ""
            text_body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    decoded_text = self._decode_payload(part)
                    if ctype == "text/html": html_body = decoded_text
                    elif ctype == "text/plain": text_body = decoded_text
            else: text_body = self._decode_payload(msg)

            target_text = text_body
            if html_body:
                extracted = self._clean_text(html_body)
                if extracted and len(extracted) > len(text_body): target_text = extracted

            data = {}
            if platform == "Amazon": data = self._parse_amazon(target_text, subject)
            elif platform == "Rakuten": data = self._parse_rakuten(target_text, subject)
            
            price = data.get("price", 0)
            item = data.get("item", "不明")

            if price == 0:
                fpath = self._save_debug_log(platform, subject, target_text)
                preview = target_text[:50].replace('\n', ' ') 
                logger.warning(f"   ⚠️ 解析失敗(0円): {preview}... (Log: {fpath})")

            # 0円でも保存する
            if self.save_record(platform, order_date, item, price, eid_str):
                logger.info(f"   💰 記録成功 [{platform}]: {item} ({price}円)")
        except Exception as e:
            logger.warning(f"   メール処理エラー: {str(e)[:100]}")

if __name__ == "__main__":
    monitor = ShoppingMonitor()
    monitor.run()