# HOME_SYSTEM/send_food_question.py
import config
import common

def get_daily_summary():
    """今日の家電稼働状況と総消費電力を集計"""
    conn = common.get_db_connection()
    if not conn: return ""
    
    try:
        cursor = conn.cursor()
        today = common.get_today_date_str()
        
        # ★修正: device_type も取得するように変更
        sql = f"""
            SELECT device_name, device_type, power_watts 
            FROM {config.SQLITE_TABLE_SENSOR} 
            WHERE timestamp LIKE ? AND power_watts IS NOT NULL
        """
        cursor.execute(sql, (f"{today}%",))
        rows = cursor.fetchall()
        
        # 集計用変数
        tv_on_count = 0
        rice_cooked = False
        total_watts_sum = 0 # Nature Remo用
        
        for row in rows:
            name = row["device_name"]
            dtype = row["device_type"]
            power = row["power_watts"]
            
            # 1. テレビ (Plug Mini)
            if "テレビ" in name and power > 20:
                tv_on_count += 1
                
            # 2. 炊飯器 (Plug Mini)
            if "炊飯器" in name and power > 5:
                rice_cooked = True
                
            # 3. ★追加: 家全体の電力 (Nature Remo E Lite)
            if dtype == "Nature Remo E Lite":
                total_watts_sum += power
                
        # レポート作成
        summary = []
        
        # テレビ稼働時間
        if tv_on_count > 0:
            summary.append(f"📺 テレビ: 約{tv_on_count * 5 / 60:.1f}時間")
            
        # 炊飯状況
        if rice_cooked:
            summary.append("🍚 ご飯: 炊きました")
            
        # ★追加: 総消費電力 (kWh)
        # 5分間隔の測定と仮定: 合計W * 5分 / 60分 / 1000 = kWh
        if total_watts_sum > 0:
            total_kwh = total_watts_sum * 5 / 60 / 1000
            # 電気代換算 (目安: 31円/kWh)
            cost_yen = int(total_kwh * 31) 
            summary.append(f"⚡ 今日の電気: {total_kwh:.2f}kWh (約{cost_yen}円)")

        if not summary:
            return ""
            
        return "\n".join(summary) + "\n\n"
        
    except Exception as e:
        print(f"[ERROR] 集計失敗: {e}")
        return ""
    finally:
        conn.close()

if __name__ == "__main__":
    print("[INFO] 質問送信開始...")
    report = get_daily_summary()
    
    # 挨拶
    actions = [
        ("🏠 自炊", "食事カテゴリ_自炊"), ("🍜 外食", "食事カテゴリ_外食"),
        ("🍱 その他", "食事カテゴリ_その他"), ("スキップ", "食事_スキップ")
    ]
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": t}} for l, t in actions]
    
    msg = {
        "type": "text",
        "text": f"🍽️ こんばんは！\n\n{report}今日の夕食はどうされましたか？",
        "quickReply": {"items": items}
    }
    
    if common.send_push(config.LINE_USER_ID, [msg]):
        print("[SUCCESS] 送信完了")
    else:
        print("[ERROR] 送信失敗")