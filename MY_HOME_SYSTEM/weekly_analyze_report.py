# HOME_SYSTEM/weekly_analyze_report.py
import config
import common
import datetime
import pytz

# ロガー設定
logger = common.setup_logging("weekly_report")

def get_start_date(period_type):
    """
    指定された期間タイプに応じた開始日時を取得する
    period_type: "week" (今週月曜), "month" (今月1日), "year" (今年元旦)
    """
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    
    if period_type == "week":
        # 今週の月曜日 (月曜=0, 日曜=6)
        start_date = now - datetime.timedelta(days=now.weekday())
    elif period_type == "month":
        # 今月の1日
        start_date = now.replace(day=1)
    elif period_type == "year":
        # 今年の1月1日
        start_date = now.replace(month=1, day=1)
    else:
        return None
        
    # 時刻を 00:00:00 に合わせる
    return start_date.replace(hour=0, minute=0, second=0, microsecond=0)

def get_analysis_data(start_dt):
    """指定された開始日時から現在までのデータを集計する"""
    with common.get_db_cursor() as cursor:
        if not cursor: return None
        
        try:
            now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            
            data = {}

            # 1. 食事の傾向
            sql_food = f"""
                SELECT menu_category 
                FROM {config.SQLITE_TABLE_FOOD} 
                WHERE timestamp >= ?
            """
            cursor.execute(sql_food, (start_str,))
            rows = cursor.fetchall()
            
            food_counts = {"自炊": 0, "外食": 0, "その他": 0}
            total_meals = 0
            
            for row in rows:
                record = row["menu_category"]
                if record.startswith("自炊"): food_counts["自炊"] += 1
                elif record.startswith("外食"): food_counts["外食"] += 1
                elif record.startswith("その他"): food_counts["その他"] += 1
                total_meals += 1
            
            data["food_counts"] = food_counts
            data["total_meals"] = total_meals
            
            # 2. 車の利用
            sql_car = f"""
                SELECT COUNT(*) 
                FROM {config.SQLITE_TABLE_CAR} 
                WHERE action = 'LEAVE' AND timestamp >= ?
            """
            cursor.execute(sql_car, (start_str,))
            row_car = cursor.fetchone()
            data["car_count"] = row_car[0] if row_car else 0

            # 3. 電気代 (実経過時間ベース)
            sql_power = f"""
                SELECT AVG(power_watts)
                FROM {config.SQLITE_TABLE_SENSOR}
                WHERE device_type = 'Nature Remo E Lite' AND timestamp >= ?
            """
            cursor.execute(sql_power, (start_str,))
            row_pow = cursor.fetchone()
            avg_watts = row_pow[0] if row_pow and row_pow[0] else 0
            
            if avg_watts:
                elapsed_hours = (now - start_dt).total_seconds() / 3600
                if elapsed_hours < 0: elapsed_hours = 0
                
                kwh = (avg_watts * elapsed_hours) / 1000
                bill = int(kwh * 31)
                data["elec_bill"] = bill
            else:
                data["elec_bill"] = 0

            # 4. 家族の体調
            sql_health = f"""
                SELECT COUNT(*) 
                FROM {config.SQLITE_TABLE_CHILD}
                WHERE timestamp >= ? AND condition NOT LIKE '%元気%'
            """
            cursor.execute(sql_health, (start_str,))
            row_health = cursor.fetchone()
            data["sick_count"] = row_health[0] if row_health else 0

            return data

        except Exception as e:
            logger.error(f"集計エラー (start={start_str}): {e}")
            return None

def generate_text_section(period_name, data, is_simple=False):
    """集計データからテキストセクションを生成"""
    if not data: return ""

    total = data["total_meals"]
    cook_count = data["food_counts"]["自炊"]
    cook_rate = int((cook_count / total * 100)) if total > 0 else 0
    
    # シンプルモード (月次など)
    if is_simple:
        return f"🍳 自炊率: {cook_rate}% / ⚡ 電気: 約{data['elec_bill']:,}円"

    # 詳細モード (週次)
    car_msg = f"{data['car_count']}回" if data['car_count'] > 0 else "なし"
    health_msg = "みんな元気でした✨" if data['sick_count'] == 0 else f"不調が{data['sick_count']}回ありました"

    text = f"【{period_name}】\n"
    text += f"🍳 自炊率: {cook_rate}% ({cook_count}/{total}回)\n"
    text += f"🚗 車利用: {car_msg}\n"
    text += f"⚡ 電気代: 約{data['elec_bill']:,}円\n"
    text += f"🏥 健康: {health_msg}\n"
    
    return text

def is_month_end_report():
    """今日がその月の最後のレポート日(日曜日)か判定"""
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    next_week = now + datetime.timedelta(days=7)
    return now.month != next_week.month

def run_report():
    logger.info("週間レポート生成開始...")
    
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    date_fmt = "%m/%d"

    # 1. 期間ごとの集計
    start_week = get_start_date("week")
    start_month = get_start_date("month")
    
    stats_week = get_analysis_data(start_week)
    stats_month = get_analysis_data(start_month)
    
    if not stats_week:
        logger.error("データが取得できませんでした")
        return

    # 期間文字列の生成 (例: 12/01～12/07)
    range_week = f"{start_week.strftime(date_fmt)}～{now.strftime(date_fmt)}"
    range_month = f"{start_month.strftime(date_fmt)}～{now.strftime(date_fmt)}"

    msg_header = "📊 **今週の我が家レポート** 📊\nおはようございます！今週も一週間お疲れ様でした🍵\n"
    
    msg_body = ""
    # 今週 (詳細)
    msg_body += generate_text_section(f"今週のまとめ ({range_week})", stats_week) + "\n"
    
    # 今月 (シンプル)
    msg_body += "------------------\n"
    msg_body += f"🗓️ {range_month} の累計: {generate_text_section('', stats_month, is_simple=True)}\n"
    
    # 2. 月末のみ「今年のトータル」を追加
    if is_month_end_report():
        logger.info("月末のため年次集計を実行します")
        start_year = get_start_date("year")
        stats_year = get_analysis_data(start_year)
        
        if stats_year:
            range_year = f"{start_year.strftime(date_fmt)}～{now.strftime(date_fmt)}"
            msg_body += f"\n👑 **今年のトータル ({range_year})** 👑\n"
            msg_body += "今月もやりくりお疲れ様でした✨\n"
            
            total_cook = stats_year["food_counts"]["自炊"]
            total_bill = stats_year["elec_bill"]
            
            msg_body += f"🍳 自炊回数: {total_cook}回！すごいです✨\n"
            msg_body += f"⚡ 年間電気代: 約{total_bill:,}円\n"

    msg_footer = "\n来週も無理せず、楽しくいきましょうね✨"

    full_msg = msg_header + msg_body + msg_footer
    
    if common.send_push(config.LINE_USER_ID, [{"type": "text", "text": full_msg}]):
        logger.info("レポート送信完了")
    else:
        logger.error("レポート送信失敗")

if __name__ == "__main__":
    run_report()