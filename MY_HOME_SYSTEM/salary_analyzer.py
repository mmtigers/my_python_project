import os
import imaplib
import email
import pikepdf
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm  # フォント管理用
import google.generativeai as genai
import json
import traceback
import time
from email.header import decode_header
from pdf2image import convert_from_path
from datetime import datetime
import config
import common

# === ロガー設定 ===
logger = common.setup_logging("salary_analyzer")

# === Gemini設定 ===
if config.GEMINI_API_KEY:
    genai.configure(api_key=config.GEMINI_API_KEY)
else:
    logger.error("❌ GEMINI_API_KEYが設定されていません。.envを確認してください。")

# === グラフの日本語フォント設定 ===
def configure_fonts():
    """Matplotlibで日本語を表示するためのフォント設定"""
    try:
        # Raspberry Pi (Linux) で一般的な日本語フォントを探す
        font_candidates = ['Noto Sans CJK JP', 'IPAGothic', 'TakaoGothic', 'VL Gothic', 'WenQuanYi Micro Hei']
        found = False
        for f in font_candidates:
            try:
                # フォントがシステムにあるか確認
                if fm.findfont(f):
                    plt.rcParams['font.family'] = f
                    logger.info(f"🎨 グラフ用フォントを '{f}' に設定しました")
                    found = True
                    break
            except:
                continue
        
        if not found:
            # 見つからない場合はJapan1（汎用）を指定してみる
            plt.rcParams['font.family'] = 'sans-serif'
            plt.rcParams['font.sans-serif'] = ['Hiragino Maru Gothic Pro', 'Yu Gothic', 'Meirio', 'Takao', 'IPAexGothic', 'IPAPGothic', 'VL PGothic', 'Noto Sans CJK JP']
    except Exception as e:
        logger.warning(f"⚠️ フォント設定中にエラー: {e}")

# 初期化時にフォント設定を実行
configure_fonts()

# === CSVのカラム定義 ===
CSV_COLUMN_ORDER = [
    "year", "month", "type", "employee_id", "name", "department",
    "net_payment", "total_payment", "total_deduction", "taxable_amount",
    "base_salary", "dependent_allowance", "adjustment_pay", "select_plan_subsidy",
    "commuting_allowance", "domestic_travel", "stock_incentive",
    "income_tax", "resident_tax", "health_insurance", "care_insurance",
    "welfare_pension", "pension_contribution", "employment_insurance",
    "ryoyu_fee", "mitsubishi_fee", "union_fee", "meal_cost", "dc_contribution",
    "life_insurance", "casualty_insurance", "stock_ownership", "melon_mutual_aid",
    "work_days", "work_hours", "overtime_ordinary", "overtime_midnight",
    "overtime_holiday", "paid_leave_remaining", "paid_leave_taken", "sick_leave"
]

class SalaryAnalyzer:
    def __init__(self):
        self.mail = None
        self.diagnose_environment()

    def diagnose_environment(self):
        logger.info("--- 🏥 システム診断 ---")
        if not config.GEMINI_API_KEY: logger.error("❌ APIキーが空です。")
        else: logger.info("✅ APIキー設定済み")

    def connect_gmail(self):
        try:
            self.mail = imaplib.IMAP4_SSL("imap.gmail.com")
            self.mail.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            self.mail.select("inbox")
            logger.info("✅ Gmail接続成功")
            return True
        except Exception as e:
            self._handle_error("Gmail接続エラー", e)
            return False

    def fetch_target_emails(self, limit=None):
        if not self.mail: return []
        sender = config.SALARY_MAIL_SENDER
        if not sender: return []
        try:
            status, messages = self.mail.search(None, 'X-GM-RAW', f'"from:{sender} has:attachment"')
            if status != "OK": return []
            email_ids = messages[0].split()
            if limit and len(email_ids) > limit: return email_ids[-limit:]
            return email_ids
        except Exception as e:
            self._handle_error("メール検索エラー", e)
            return []

    def unlock_pdf(self, input_path, output_path):
        for pwd in config.SALARY_PDF_PASSWORDS:
            try:
                with pikepdf.open(input_path, password=pwd) as pdf:
                    pdf.save(output_path)
                logger.info(f"🔓 PDF解除成功")
                return True
            except: continue
        logger.error("❌ PDFパスワード解除失敗")
        return False

    def get_model_candidates(self):
        """
        利用可能なモデルリストを返す。
        ★実験的モデルを除外し、安定版のみを使用する。
        """
        return [
            # 1. 最新の高速・軽量モデル（旧 1.5-flash の後継）
            #    無料枠での制限が最も緩く、給与明細の読み取りには十分な性能
            'models/gemini-2.5-flash',

            # 2. バージョン固定なしのエイリアス（常に最新のFlashを指す）
            'models/gemini-flash-latest',

            # 3. 高性能モデル（Flashで失敗した時のバックアップ）
            #    無料枠でも使えますが、Flashより回数制限が厳しいため後ろに配置
            'models/gemini-2.5-pro',
            
            # 4. 高性能モデルのエイリアス
            'models/gemini-pro-latest'
        ]

    def analyze_with_gemini(self, image_path):
        uploaded_file = None
        try:
            model_list = self.get_model_candidates()
            logger.info("📤 画像をGeminiにアップロード中...")
            uploaded_file = genai.upload_file(path=image_path, display_name="Salary Slip")
            
            prompt = """
            この給与明細画像を分析し、JSONデータを出力してください。
            数値はカンマなしの整数。「0」や「空欄」は 0。年月はヘッダーから正確に読み取ること。

            {
                "year": 2025, "month": 11, "type": "給与", 
                "employee_id": "社員番号", "name": "氏名", "department": "所属コード",

                // 支給
                "base_salary": 0, "dependent_allowance": 0, "adjustment_pay": 0,
                "select_plan_subsidy": 0, "commuting_allowance": 0, "domestic_travel": 0,
                "stock_incentive": 0, "total_payment": 0,

                // 控除
                "income_tax": 0, "resident_tax": 0, "health_insurance": 0, "care_insurance": 0,
                "welfare_pension": 0, "pension_contribution": 0, "employment_insurance": 0,
                "ryoyu_fee": 0, "mitsubishi_fee": 0, "union_fee": 0, "meal_cost": 0,
                "dc_contribution": 0, "life_insurance": 0, "casualty_insurance": 0,
                "stock_ownership": 0, "melon_mutual_aid": 0, "total_deduction": 0,

                // 合計
                "net_payment": 0, "taxable_amount": 0,

                // 勤怠
                "work_days": 0, "work_hours": 0, "overtime_ordinary": 0, "overtime_midnight": 0,
                "overtime_holiday": 0, "paid_leave_remaining": 0, "paid_leave_taken": 0, "sick_leave": 0
            }
            """
            
            for model_name in model_list:
                try:
                    logger.info(f"🤖 モデル {model_name} で解析中...")
                    model = genai.GenerativeModel(model_name)
                    res = model.generate_content([uploaded_file, prompt])
                    text = res.text.replace("```json", "").replace("```", "").strip()
                    data = json.loads(text)

                    if not data.get("year") or data.get("year") == 0:
                        logger.warning(f"⚠️ {model_name}: 年月読み取り失敗。")
                        continue
                    
                    if data.get("net_payment", 0) == 0 and data.get("total_payment", 0) > 0:
                        data["net_payment"] = data["total_payment"] - data.get("total_deduction", 0)
                        
                    return data

                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "Quota" in err_str:
                        logger.warning(f"⚠️ {model_name} 利用枠上限(429)。10秒待機して次のモデルへ...")
                        time.sleep(10)
                    else:
                        logger.warning(f"⚠️ {model_name} エラー: {e}")
            
            raise Exception("すべてのモデルで解析に失敗しました。")

        except Exception as e:
            self._handle_error("Gemini分析エラー", e)
            return None
        finally:
            if uploaded_file:
                try: uploaded_file.delete()
                except: pass

    def update_csv_database(self, data):
        if not data: return False
        try:
            csv_path = config.SALARY_CSV_PATH if data['type'] == "給与" else config.BONUS_CSV_PATH
            if os.path.exists(csv_path): df = pd.read_csv(csv_path)
            else: df = pd.DataFrame(columns=CSV_COLUMN_ORDER)
            
            new_row = pd.DataFrame([data])
            for col in CSV_COLUMN_ORDER:
                if col not in new_row.columns: new_row[col] = 0
            for col in CSV_COLUMN_ORDER:
                if col not in df.columns: df[col] = 0
            
            new_row = new_row.reindex(columns=CSV_COLUMN_ORDER)
            df = df.reindex(columns=CSV_COLUMN_ORDER)

            df_combined = pd.concat([df, new_row])
            df_combined['year'] = pd.to_numeric(df_combined['year'], errors='coerce')
            df_combined['month'] = pd.to_numeric(df_combined['month'], errors='coerce')
            df_combined = df_combined.dropna(subset=['year', 'month'])
            
            df_combined = df_combined.drop_duplicates(subset=['year', 'month'], keep='last')
            df_combined = df_combined.sort_values(['year', 'month'])
            
            df_combined.to_csv(csv_path, index=False)
            logger.info(f"💾 CSV更新完了: {csv_path}")
            return True
        except Exception as e:
            self._handle_error("CSV保存エラー", e)
            return False

    def generate_graph(self, data_type="給与"):
        csv_path = config.SALARY_CSV_PATH if data_type == "給与" else config.BONUS_CSV_PATH
        if not os.path.exists(csv_path): return None
        try:
            df = pd.read_csv(csv_path)
            if df.empty: return None
            
            df['date'] = pd.to_datetime(df['year'].astype(str) + '-' + df['month'].astype(str) + '-01', errors='coerce')
            df = df.dropna(subset=['date'])
            if df.empty: return None

            fig, axes = plt.subplots(2, 2, figsize=(16, 12))
            # フォント設定が反映されるようタイトルに日本語を使用
            fig.suptitle(f"{data_type} ダッシュボード", fontsize=20, fontweight='bold')
            plt.subplots_adjust(hspace=0.3, wspace=0.2)

            # 1. 給与推移
            ax1 = axes[0, 0]
            ax1.plot(df['date'], df['total_payment'], label='総支給', marker='o', linestyle='--', color='gray')
            ax1.plot(df['date'], df['net_payment'], label='手取り', marker='o', linewidth=3, color='orange')
            ax1.plot(df['date'], df['total_deduction'], label='控除', marker='x', linestyle=':', color='red')
            ax1.set_title("給与推移 (円)")
            ax1.legend()
            ax1.grid(True, linestyle=':', alpha=0.6)
            ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: format(int(x), ',')))

            # 2. 勤怠
            ax2 = axes[0, 1]
            ax2.bar(df['date'], df['work_hours'], label='在場時間', alpha=0.6, color='skyblue', width=20)
            ax2.set_title("勤怠時間")
            ax2.legend()

            # 3. 支給内訳
            ax3 = axes[1, 0]
            icols = ['base_salary', 'adjustment_pay', 'select_plan_subsidy', 'commuting_allowance']
            ilabs = ['基本給', '調整給', 'セレクトプラン', '通勤費']
            bottom = None
            for c, l in zip(icols, ilabs):
                if c in df.columns:
                    v = df[c].fillna(0)
                    ax3.bar(df['date'], v, label=l, bottom=bottom, width=20, alpha=0.8)
                    bottom = v if bottom is None else bottom + v
            ax3.set_title("支給内訳")
            ax3.legend()

            # 4. 控除内訳
            ax4 = axes[1, 1]
            dcols = ['income_tax', 'resident_tax', 'health_insurance', 'welfare_pension']
            dlabs = ['所得税', '住民税', '健康保険', '厚生年金']
            bottom = None
            for c, l in zip(dcols, dlabs):
                if c in df.columns:
                    v = df[c].fillna(0)
                    ax4.bar(df['date'], v, label=l, bottom=bottom, width=20, alpha=0.8)
                    bottom = v if bottom is None else bottom + v
            ax4.set_title("控除内訳")
            ax4.legend()

            graph_filename = f"dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            graph_path = os.path.join(config.SALARY_IMAGE_DIR, graph_filename)
            plt.savefig(graph_path, bbox_inches='tight')
            plt.close()
            return graph_path

        except Exception as e:
            self._handle_error("グラフ作成エラー", e)
            return None

    def notify_user(self, result_data, graph_path):
        if not result_data: return
        
        msg_text = (
            f"💰 **給料明細レポート** ({result_data['year']}年{result_data['month']}月)\n"
            f"お仕事お疲れ様でした🍵 今月も無事にデータが届きましたよ！\n\n"
            f"💴 **手取り: {result_data.get('net_payment',0):,} 円**\n"
            f"🏢 総支給: {result_data.get('total_payment',0):,} 円\n"
            f"📉 控除計: {result_data.get('total_deduction',0):,} 円\n\n"
            f"家計の管理に役立つダッシュボードを作成しました📊"
        )
        
        if graph_path:
            with open(graph_path, 'rb') as f:
                image_data = f.read()
                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_text}], image_data=image_data, target="discord")
        else:
            common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_text}], target="discord")
        logger.info("📨 Discord通知送信完了")

    def _handle_error(self, context, error):
        logger.error(f"{context}: {error}")
        err_msg = f"😰 **System Error**\n{context}\n```{str(error)}```"
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": err_msg}], target="discord")

    def cleanup(self):
        if self.mail:
            try: self.mail.logout()
            except: pass

    def run(self, is_all_history=False, limit=None):
        logger.info("🚀 給料明細分析プロセス起動")
        if not self.connect_gmail(): return
        
        limit_val = limit if limit is not None else (None if is_all_history else 3)
        email_ids = self.fetch_target_emails(limit_val)
        logger.info(f"📩 対象メール: {len(email_ids)} 件")
        
        processed = 0
        last_res, last_graph = None, None
        
        for i, e_id in enumerate(email_ids):
            try:
                # 処理の合間に待機 (API制限対策)
                if i > 0:
                    logger.info("⏳ API制限回避のため5秒待機中...")
                    time.sleep(5)

                logger.info(f"📨 [{i+1}/{len(email_ids)}] メール処理開始...")
                _, msg_data = self.mail.fetch(e_id, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                
                subject, encoding = decode_header(msg["Subject"])[0]
                if isinstance(subject, bytes): subject = subject.decode(encoding if encoding else "utf-8")
                logger.info(f"   件名: {subject}")

                for part in msg.walk():
                    if part.get_filename() and part.get_filename().endswith(".pdf"):
                        tmp_pdf = os.path.join(config.SALARY_IMAGE_DIR, "temp.pdf")
                        unlocked = os.path.join(config.SALARY_IMAGE_DIR, "temp_ul.pdf")
                        with open(tmp_pdf, "wb") as f: f.write(part.get_payload(decode=True))
                        
                        if self.unlock_pdf(tmp_pdf, unlocked):
                            images = convert_from_path(unlocked, first_page=1, last_page=1)
                            if images:
                                img_path = os.path.join(config.SALARY_IMAGE_DIR, "target.jpg")
                                images[0].save(img_path, "JPEG")
                                res = self.analyze_with_gemini(img_path)
                                if res and self.update_csv_database(res):
                                    last_graph = self.generate_graph(res['type'])
                                    last_res = res
                                    processed += 1
                                    
                        if os.path.exists(tmp_pdf): os.remove(tmp_pdf)
                        if os.path.exists(unlocked): os.remove(unlocked)
            except Exception as e:
                self._handle_error(f"メール処理エラー (ID: {e_id})", e)
        
        if processed > 0:
            logger.info(f"🎉 合計 {processed} 件処理しました。")
            self.notify_user(last_res, last_graph)
        else:
            logger.info("✨ 新しいデータは見つかりませんでした。")
        self.cleanup()

if __name__ == "__main__":
    analyzer = SalaryAnalyzer()
    print("--- 給料明細分析ツール (安定版) ---")
    print("1: 通常実行 (最新のメールを確認)")
    print("2: 履歴取込 (過去のメールを全て確認)")
    print("3: お試し実行 (最新5件のみ確認)")
    mode = input("モードを選択 (1/2/3): ")
    if mode == "1": analyzer.run(False)
    elif mode == "2": analyzer.run(True)
    elif mode == "3": analyzer.run(True, limit=5)