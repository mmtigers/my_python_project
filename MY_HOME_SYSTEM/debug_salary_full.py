# HOME_SYSTEM/debug_salary_full.py
import os
import imaplib
import email
import pikepdf
import json
import traceback
import time
from email.header import decode_header
from pdf2image import convert_from_path
import google.generativeai as genai
import config
import common

# === ロガー設定 ===
logger = common.setup_logging("debugger")

# === 保存先設定 ===
DEBUG_DIR = os.path.join(config.BASE_DIR, "debug_output")
if not os.path.exists(DEBUG_DIR):
    os.makedirs(DEBUG_DIR)

# === Gemini設定 ===
if config.GEMINI_API_KEY:
    genai.configure(api_key=config.GEMINI_API_KEY)

def diagnose_gemini_models():
    """利用可能なモデルをリストアップ"""
    logger.info("🔍 利用可能なGeminiモデルを確認中...")
    try:
        models = list(genai.list_models())
        vision_models = [m.name for m in models if 'generateContent' in m.supported_generation_methods]
        logger.info(f"📋 画像認識対応モデル: {vision_models}")
        return vision_models
    except Exception as e:
        logger.error(f"❌ モデル一覧取得エラー: {e}")
        return []

def save_text_log(filename, content):
    """ファイルにログを保存"""
    path = os.path.join(DEBUG_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path

def connect_gmail():
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        mail.select("inbox")
        return mail
    except Exception as e:
        logger.error(f"Gmail接続エラー: {e}")
        return None

def fetch_latest_salary_mail(mail):
    sender = config.SALARY_MAIL_SENDER
    query = f'from:{sender} has:attachment'
    try:
        status, messages = mail.search(None, 'X-GM-RAW', f'"{query}"')
        if status != "OK": return None
        email_ids = messages[0].split()
        if not email_ids: return None
        return email_ids[-1] # 最新1件
    except Exception as e:
        logger.error(f"メール検索エラー: {e}")
        return None

def process_and_debug():
    logger.info("🚀 徹底調査デバッガー起動")
    logger.info(f"📂 デバッグ出力先: {DEBUG_DIR}")

    # 1. モデル診断
    vision_models = diagnose_gemini_models()
    
    # テストするモデルの候補 (高性能な順)
    target_models = [
        'models/gemini-1.5-pro',        # 最も賢い (読み取り精度が高い)
        'models/gemini-2.5-flash',      # 最新・高速
        'models/gemini-1.5-flash',      # 標準
    ]
    
    # 実際に使えるモデルに絞る
    available_targets = [m for m in target_models if m in vision_models]
    # マッチしない場合、部分一致で探す
    if not available_targets:
        for tm in target_models:
            for vm in vision_models:
                if tm.split("/")[-1] in vm:
                    available_targets.append(vm)
                    break
    
    # それでもなければリストの先頭を使う
    if not available_targets and vision_models:
        available_targets = [vision_models[0]]
        
    logger.info(f"🧪 テスト対象モデル: {available_targets}")

    # 2. メール取得 & 画像化
    mail = connect_gmail()
    if not mail: return
    
    mail_id = fetch_latest_salary_mail(mail)
    if not mail_id:
        logger.error("❌ 給与明細メールが見つかりませんでした")
        return

    logger.info(f"📩 最新のメール (ID: {mail_id.decode()}) を取得中...")
    _, msg_data = mail.fetch(mail_id, "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])
    
    subject = decode_header(msg["Subject"])[0][0]
    if isinstance(subject, bytes): subject = subject.decode()
    logger.info(f"   件名: {subject}")

    image_path = None
    
    # 添付ファイル処理
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None: continue
        filename = part.get_filename()
        
        if filename and filename.lower().endswith(".pdf"):
            logger.info(f"📄 PDF発見: {filename}")
            
            # PDF保存
            pdf_path = os.path.join(DEBUG_DIR, "debug_target.pdf")
            with open(pdf_path, "wb") as f:
                f.write(part.get_payload(decode=True))
            
            # パスワード解除
            unlocked_path = os.path.join(DEBUG_DIR, "debug_unlocked.pdf")
            is_unlocked = False
            for pwd in config.SALARY_PDF_PASSWORDS:
                try:
                    with pikepdf.open(pdf_path, password=pwd) as pdf:
                        pdf.save(unlocked_path)
                    logger.info(f"🔓 パスワード解除成功 (Pass: {pwd[:2]}***)")
                    is_unlocked = True
                    break
                except: continue
            
            if not is_unlocked:
                logger.error("❌ パスワード解除に失敗しました")
                return

            # 画像変換 (高解像度設定 dpi=300)
            logger.info("🖼️ 画像変換中 (DPI=300)...")
            try:
                images = convert_from_path(unlocked_path, dpi=300, first_page=1, last_page=1)
                if images:
                    image_path = os.path.join(DEBUG_DIR, "debug_image.jpg")
                    images[0].save(image_path, "JPEG", quality=95)
                    logger.info(f"✅ 画像保存完了: {image_path}")
                else:
                    logger.error("❌ 画像変換後のリストが空です")
                    return
            except Exception as e:
                logger.error(f"❌ 画像変換エラー: {e}")
                return
            break
    
    if not image_path:
        logger.error("❌ PDFが見つからなかったか、画像化できませんでした")
        return

    # 3. 各モデルで分析テスト
    prompt = """
    この給与明細画像を分析し、以下の情報をJSONで抽出してください。
    
    【重要・厳守】
    1. 画像に書かれている数値のみを使用すること。例示の数値（2024年など）は絶対に使わないこと。
    2. 読み取れない項目は null にすること。0 にしないこと。
    3. 数値はカンマなしの整数にすること。
    
    出力フォーマット:
    {
        "year": 2025, "month": 11, "type": "給与",
        "name": "氏名",
        "total_payment": 0,  // 支給合計
        "net_payment": 0     // 差引支給額
    }
    """

    results_summary = []

    logger.info("\n🤖 --- AI分析テスト開始 ---")
    
    for model_name in available_targets:
        logger.info(f"\n▶️ モデル: {model_name} で試行中...")
        uploaded_file = None
        try:
            # アップロード
            uploaded_file = genai.upload_file(path=image_path, display_name="Debug Salary")
            
            model = genai.GenerativeModel(model_name)
            response = model.generate_content([uploaded_file, prompt])
            
            raw_text = response.text
            
            # ログ保存
            log_file = f"response_{model_name.replace('models/', '')}.txt"
            save_text_log(log_file, raw_text)
            logger.info(f"   💾 生レスポンス保存: {log_file}")
            
            # 簡易解析チェック
            logger.info(f"   📝 レスポンス抜粋:\n{raw_text[:200]}...")
            
            # JSONパース試行
            clean_text = raw_text.replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(clean_text)
                year = data.get('year')
                pay = data.get('net_payment')
                status = "✅ 成功" if year and pay else "⚠️ 項目欠損"
                logger.info(f"   📊 解析結果: {status} (Year: {year}, NetPay: {pay})")
            except:
                logger.error("   ❌ JSONパース失敗")

        except Exception as e:
            logger.error(f"   ❌ エラー発生: {e}")
        finally:
            if uploaded_file:
                try: uploaded_file.delete()
                except: pass
    
    logger.info("\n🎉 --- 調査終了 ---")
    logger.info(f"出力フォルダを確認してください: {DEBUG_DIR}")
    logger.info("特に 'debug_image.jpg' を開いて、文字が鮮明に読めるか確認してください。")

if __name__ == "__main__":
    process_and_debug()