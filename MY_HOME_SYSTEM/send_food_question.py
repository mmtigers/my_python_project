# HOME_SYSTEM/send_food_question.py
import config
import common
import datetime
import pytz

logger = common.setup_logging("food_question")

def get_daily_summary():
    with common.get_db_cursor() as cursor:
        if not cursor: return ""
        try:
            today = common.get_today_date_str()
            
            # 1. 家電データ
            cursor.execute(f"SELECT device_name, device_type, power_watts FROM {config.SQLITE_TABLE_SENSOR} WHERE timestamp LIKE ? AND power_watts IS NOT NULL", (f"{today}%",))
            rows = cursor.fetchall()
            
            tv_cnt, rice, total_w = 0, False, 0
            for row in rows:
                if "テレビ" in row["device_name"] and row["power_watts"] > 20: tv_cnt += 1
                if "炊飯器" in row["device_name"] and row["power_watts"] > 5: rice = True
                if row["device_type"] == "Nature Remo E Lite": total_w += row["power_watts"]
            
            # 2. 車データ
            cursor.execute(f"SELECT action, timestamp FROM {config.SQLITE_TABLE_CAR} WHERE timestamp LIKE ? ORDER BY timestamp", (f"{today}%",))
            car_rows = cursor.fetchall()
            
            car_count = 0
            last_leave = None
            total_out_seconds = 0
            
            for row in car_rows:
                action = row["action"]
                ts = datetime.datetime.fromisoformat(row["timestamp"])
                
                if action == "LEAVE":
                    car_count += 1
                    last_leave = ts
                elif action == "RETURN" and last_leave:
                    duration = (ts - last_leave).total_seconds()
                    total_out_seconds += duration
                    last_leave = None # リセット

            # === ★追加: 3. 防犯カメラ検知集計 ===
            cursor.execute(f"SELECT contact_state FROM {config.SQLITE_TABLE_SENSOR} WHERE device_type = 'ONVIF Camera' AND timestamp LIKE ?", (f"{today}%",))
            cam_rows = cursor.fetchall()
            
            cam_msg = "📷 カメラ検知: なし"
            if cam_rows:
                total_cam = len(cam_rows)
                # 種類別にカウント
                counts = {}
                for r in cam_rows:
                    etype = r["contact_state"] # person, vehicle, intrusion etc.
                    counts[etype] = counts.get(etype, 0) + 1
                
                # 表示用ラベル変換
                label_map = {"intrusion": "🚨侵入", "person": "👤人", "vehicle": "🚗車", "motion": "👀動き"}
                details = []
                for k, v in counts.items():
                    lbl = label_map.get(k, k)
                    details.append(f"{lbl}:{v}")
                
                cam_msg = f"📷 カメラ検知: {total_cam}回 ({' '.join(details)})"

            # レポート作成
            summary = []
            # テレビ (0時間でも表示)
            tv_hours = tv_cnt * 5 / 60
            summary.append(f"📺 テレビ: 約{tv_hours:.1f}時間")

            # 炊飯状況を分岐
            if rice:
                summary.append("🍚 ご飯: 炊きました")
            else:
                summary.append("🍚 ご飯: 炊いていません")
            if total_w > 0:
                kwh = total_w * 5 / 60 / 1000
                summary.append(f"⚡ 今日の電気: {kwh:.2f}kWh (約{int(kwh*31)}円)")
                
            if car_count > 0:
                out_min = total_out_seconds / 60
                summary.append(f"🚗 車の利用: {car_count}回 (合計 約{int(out_min)}分)")
            else:
                summary.append("🚗 車の利用はありませんでした。")
            
            # カメラ情報を追加
            summary.append(cam_msg)

            return "\n".join(summary) + "\n\n" if summary else ""
        except Exception as e:
            logger.error(f"集計失敗: {e}")
            return ""

if __name__ == "__main__":
    logger.info("質問送信処理を開始...")
    report = get_daily_summary()
    
    actions = [
        ("🏠 自炊", "食事カテゴリ_自炊"), ("🍜 外食", "食事カテゴリ_外食"),
        ("🍱 その他", "食事カテゴリ_その他"), ("スキップ", "食事_スキップ")
    ]
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": t}} for l, t in actions]
    
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    # 2026年以降でなくてもDiscordを優先したい場合はここを調整
    target_platform = "discord" 
    
    msg = {
        "type": "text",
        "text": f"🌙 こんばんは、お疲れ様！\n\n{report}今日の夕食はどうしたの？",
        "quickReply": {"items": items}
    }
    
    # target="discord" を明示的に指定して送信
    if common.send_push(config.LINE_USER_ID, [msg], target=target_platform):
        logger.info("送信完了✨")
    else:
        logger.error("送信失敗💦")