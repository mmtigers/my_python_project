# MY_HOME_SYSTEM/send_ai_report.py
import google.generativeai as genai
import json
import config
import common
import traceback
import argparse
import sys
import os
from datetime import datetime

# === ロガー設定 ===
logger = common.setup_logging("ai_report")

def get_family_profile():
    """
    家族構成プロファイルを生成する。
    個人情報はコードに直書きせず、config (環境変数) から読み込む。
    """
    # configに設定がなければ汎用的な名称を使用
    dad_name = getattr(config, "DAD_NAME", "旦那様")
    mom_name = getattr(config, "MOM_NAME", "奥様")
    
    # 子供情報はconfig.CHILDREN_NAMESから動的に生成
    children_info = ""
    if config.CHILDREN_NAMES:
        children_info = ", ".join([f"{name}" for name in config.CHILDREN_NAMES])
    else:
        children_info = "お子様たち"

    return f"""
    - 夫: {dad_name} (仕事熱心)
    - 妻: {mom_name} (専業主婦, 家事育児に奮闘中)
    - 子供: {children_info}
    - 住まい: {getattr(config, "HOME_LOCATION", "自宅")}
    - 実家: {getattr(config, "PARENTS_LOCATION", "実家")}
    """

def parse_arguments():
    """コマンドライン引数の解析"""
    parser = argparse.ArgumentParser(description='AI日報送信スクリプト')
    parser.add_argument('--target', type=str, default='discord', choices=['line', 'discord', 'both'],
                        help='通知先 (line, discord, both)')
    return parser.parse_args()

def setup_gemini():
    """Gemini APIのセットアップとモデル選択"""
    if not config.GEMINI_API_KEY:
        logger.error("❌ Gemini API Keyが設定されていません。")
        sys.exit(1)
    
    genai.configure(api_key=config.GEMINI_API_KEY)
    
    # 優先モデルリスト (新しい順)
    candidates = [
        "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash",
        "gemini-1.5-pro", "gemini-pro"
    ]
    
    try:
        # 利用可能なモデルを動的に探索
        available_models = [m.name.replace("models/", "") for m in genai.list_models()]
        print(f"🤖 [Model Check] API Available: {len(available_models)} models found.")

        for cand in candidates:
            if cand in available_models:
                print(f"✨ [Model Select] Selected: {cand}")
                return genai.GenerativeModel(cand)
        
        # フォールバック
        fallback = next((m for m in available_models if "flash" in m), "gemini-1.5-flash")
        print(f"⚠️ [Model Fallback] Selected: {fallback}")
        return genai.GenerativeModel(fallback)

    except Exception as e:
        logger.error(f"モデル選択エラー: {e}")
        # 最悪の場合のデフォルト
        return genai.GenerativeModel("gemini-1.5-flash")

def fetch_daily_data():
    """DBから今日のデータを取得し、辞書形式で返す"""
    print("📥 [Data Fetch] データベースから情報を収集中...")
    data = {}
    today_str = common.get_today_date_str()
    
    with common.get_db_cursor() as cursor:
        if not cursor:
            raise ConnectionError("データベースに接続できませんでした")
        
        # 1. 天気・環境
        cursor.execute(f"""
            SELECT device_name, avg(temperature_celsius) as temp, avg(humidity_percent) as hum 
            FROM {config.SQLITE_TABLE_SENSOR} 
            WHERE timestamp LIKE ? AND device_type LIKE '%Meter%'
            GROUP BY device_id
        """, (f"{today_str}%",))
        data['environment'] = [{ "place": r["device_name"], "temp": round(r["temp"],1), "humidity": round(r["hum"],1) } for r in cursor.fetchall()]

        # 2. 実家の活動
        # location='高砂' (またはconfig依存) のセンサーを取得
        target_loc = getattr(config, "PARENTS_LOCATION", "高砂")
        taka_ids = [d["id"] for d in config.MONITOR_DEVICES if d.get("location") == target_loc and "Contact" in d.get("type", "")]
        
        if taka_ids:
            placeholders = ",".join(["?"] * len(taka_ids))
            cursor.execute(f"""
                SELECT device_name, COUNT(*) 
                FROM {config.SQLITE_TABLE_SENSOR} 
                WHERE timestamp LIKE ? AND device_id IN ({placeholders}) AND contact_state IN ('open', 'detected')
                GROUP BY device_id
            """, (f"{today_str}%", *taka_ids))
            data['parents_home'] = {r["device_name"]: r[1] for r in cursor.fetchall()}
        
        # 3. 電気代
        cursor.execute(f"""
            SELECT avg(power_watts) FROM {config.SQLITE_TABLE_SENSOR} 
            WHERE timestamp LIKE ? AND device_type = 'Nature Remo E Lite'
        """, (f"{today_str}%",))
        row = cursor.fetchone()
        avg_w = row[0] if row and row[0] is not None else 0
        est_bill = int((avg_w * 24 / 1000) * 31)
        
        data['electricity'] = {
            "estimated_daily_bill_yen": est_bill, 
            "avg_watts": int(avg_w),
            "status": "Generating Power (Solar)" if avg_w < 0 else "Consuming Power"
        }
        
        # 4. 車の移動
        cursor.execute(f"SELECT count(*) FROM {config.SQLITE_TABLE_CAR} WHERE timestamp LIKE ? AND action='LEAVE'", (f"{today_str}%",))
        data['car_outing_count'] = cursor.fetchone()[0]

        # 5. 子供の体調ログ
        cursor.execute(f"SELECT child_name, condition FROM {config.SQLITE_TABLE_CHILD} WHERE timestamp LIKE ?", (f"{today_str}%",))
        data['children_health'] = [{ "child": r["child_name"], "condition": r["condition"] } for r in cursor.fetchall()]

    return data

def build_system_prompt(data):
    """AIへの指示書（プロンプト）を作成"""
    # configから名前を取得（なければデフォルト）
    mom_name = getattr(config, "MOM_NAME", "奥様")
    
    return f"""
    あなたは「優秀で気が利く、少しユーモアのある執事」です。
    主人の代わりに、妻の{mom_name}さんへ「今日の一日のレポート」を送ります。
    
    【家族構成】
    {get_family_profile()}

    【目的】
    {mom_name}さんが読んで「ホッとする」「労われている」と感じるメッセージを作成すること。
    データ報告そのものより、そこから読み取れる「生活の様子」への共感が重要です。

    【今日のデータ (JSON)】
    {json.dumps(data, ensure_ascii=False)}

    【作成ルール】
    1. **トーン:** 丁寧語（です・ます）ですが、堅苦しすぎず、温かみのある口調で。絵文字を適度に使ってください。
    2. **ターゲット:** 主婦である{mom_name}さんに向けて話しかけてください。
    3. **内容の優先度:**
       - **最重要:** 子供たちのこと（体調記録があれば必ず触れる。なければ「今日も元気で何より」と触れる）。
       - **重要:** 実家の様子（センサー反応があれば「お母様も活動的でした」、なければ「静かでした」）。
       - **重要:** 電気代（マイナスの場合は「発電して家計を助けています！」と褒める。高い場合は「快適に過ごすのが一番です」とフォロー）。
    4. **締めくくり:** 最後に「今日の夕食はどうされますか？」と優しく尋ねてください。
    5. **長さ:** スマホで読みやすいよう、300文字程度にまとめてください。
    """

def save_report_to_db(message):
    """生成されたレポートをDBに保存"""
    print("💾 [DB Save] レポートを記録します...")
    # テーブル名、カラムリスト、値のタプル
    return common.save_log_generic(
        config.SQLITE_TABLE_AI_REPORT, 
        ["message", "timestamp"], 
        (message, common.get_now_iso())
    )



def generate_report(model, data):
    """AIを使ってメッセージを生成"""
    print("🧠 [AI Thinking] レポートを作成中...")
    prompt = build_system_prompt(data)
    response = model.generate_content(prompt)
    return response.text.strip()

def send_notification(message, target):
    """指定されたターゲットにメッセージを送信"""
    print(f"📤 [Sending] 送信先: {target}")
    
    # QuickReplyボタンの作成
    actions = [
        ("🏠 自炊", "食事カテゴリ_自炊"), ("🍜 外食", "食事カテゴリ_外食"),
        ("🍱 その他", "食事カテゴリ_その他"), ("スキップ", "食事_スキップ")
    ]
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": t}} for l, t in actions]
    
    msg_payload = {
        "type": "text",
        "text": message,
        "quickReply": {"items": items}
    }

    # 送信処理
    success = False
    targets_to_send = ['line', 'discord'] if target == 'both' else [target]
        
    for t in targets_to_send:
        # common.send_push の target 引数に渡す
        if common.send_push(config.LINE_USER_ID, [msg_payload], target=t, channel="report"):
            print(f"   ✅ {t}: 送信成功")
            success = True
        else:
            print(f"   ❌ {t}: 送信失敗")
            
    return success

def main():
    print(f"\n🚀 --- AI Reporter Start: {datetime.now().strftime('%H:%M:%S')} ---")
    args = parse_arguments()
    
    try:
        # 1. セットアップ
        model = setup_gemini()
        
        # 2. データ収集
        daily_data = fetch_daily_data()
        
        # 3. AI生成
        report_text = generate_report(model, daily_data)
        print(f"\n📝 [Generated Report]\n{'-'*30}\n{report_text}\n{'-'*30}\n")
        
        # 4. 送信
        if send_notification(report_text, args.target):
            print("🎉 All Done! 正常に終了しました。")
        else:
            logger.error("メッセージの送信に失敗しました")
            sys.exit(1)


        # ▼【追加】DB保存
        if save_report_to_db(report_text):
            print("   ✅ DB保存完了")
        else:
            logger.error("   ❌ DB保存失敗")

        if send_notification(report_text, args.target):
            print("🎉 完了")
        else:
            sys.exit(1)

    except Exception as e:
        logger.error(f"予期せぬエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        common.send_push(config.LINE_USER_ID, 
                         [{"type": "text", "text": f"😰 **AI Reporter Error**\n```{e}```"}], 
                         target="discord", channel="error")
        sys.exit(1)

if __name__ == "__main__":
    main()