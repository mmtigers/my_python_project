import config
import common
import datetime
import os
import pytz
import sys
from typing import Dict, Optional, Any

# ロガー設定 (設計書 8.1: core.loggerの使用ラッパー) [cite: 144]
logger = common.setup_logging("weekly_report")

# 定数定義 (本来はconfig.pyまたは.envから読み込むべき値)
# 設計書 9.2: 機密情報・設定値の分離 
DEFAULT_ELEC_PRICE_PER_KWH = 31

# #234: 外部cron(リポジトリ管理外)による月曜8時台の多重起動時に重複送信するのを防ぐための
# 実行済みフラグファイル。monitors/tv_lock_monitor.pyのLAST_RUN_FILEと同じ方式。
LAST_RUN_FILE = os.path.join(config.FALLBACK_ROOT, "last_weekly_report.txt")

def get_start_date(period_type: str) -> Optional[datetime.datetime]:
    """指定された期間タイプに応じた集計開始日時を取得する。

    Args:
        period_type (str): "week" (先週月曜), "month" (今月1日), "year" (今年元旦)

    Returns:
        Optional[datetime.datetime]: 開始日時オブジェクト。無効なタイプの場合はNone。
    """
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    # 時間を 00:00:00 にリセット
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if period_type == "week":
        # 月曜実行時に「先週の月曜日」を取得するため、7日戻る
        # (scheduler.pyが月曜に実行することを前提)
        days_to_last_monday = now.weekday() + 7 if now.weekday() == 0 else now.weekday()
        return today - datetime.timedelta(days=days_to_last_monday)
    elif period_type == "month":
        return today.replace(day=1)
    elif period_type == "year":
        return today.replace(month=1, day=1)
    
    return None

def get_analysis_data(start_dt: datetime.datetime) -> Optional[Dict[str, Any]]:
    """指定された開始日時から現在までのデータをDBから集計する。

    Args:
        start_dt (datetime.datetime): 集計開始日時。

    Returns:
        Optional[Dict[str, Any]]: 集計結果を含む辞書。エラー時はNone。
    """
    with common.get_db_cursor() as cursor:
        if not cursor:
            return None
        
        try:
            now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            
            data: Dict[str, Any] = {}

            # 1. 食事の傾向
            # Note: config.SQLITE_TABLE_FOOD は基本設計書3.2には明記がないが、既存互換のため維持
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
                if record.startswith("自炊"):
                    food_counts["自炊"] += 1
                elif record.startswith("外食"):
                    food_counts["外食"] += 1
                elif record.startswith("その他"):
                    food_counts["その他"] += 1
                total_meals += 1
            
            data["food_counts"] = food_counts
            data["total_meals"] = total_meals
            
            # 2. 車の利用 (設計書 3.2: car_records) 
            sql_car = f"""
                SELECT COUNT(*) 
                FROM {config.SQLITE_TABLE_CAR} 
                WHERE action = 'LEAVE' AND timestamp >= ?
            """
            cursor.execute(sql_car, (start_str,))
            row_car = cursor.fetchone()
            data["car_count"] = row_car[0] if row_car else 0

            # 3. 電気代 (実経過時間ベース)
            # 修正: 設計書 3.2 に基づき power_usage テーブルと wattage カラムを使用 
            # 注意: config.SQLITE_TABLE_POWER_USAGE が未定義の場合は config.py への追加が必要
            table_power = getattr(config, "SQLITE_TABLE_POWER_USAGE", "power_usage")

            # #170: power_usageにはスマートメーター(全体消費)と各プラグ(個別家電)が
            # 同居しており、プラグの消費電力はスマートメーターの計測値に既に含まれる
            # 部分集合である。デバイスを絞らずAVG(wattage)を取るとプラグのアイドル値が
            # スマートメーター値を希釈してしまうため、services/analysis_service.pyの
            # load_sensor_data()と同じ分類基準(device_nameに"Remo"を含む)で
            # スマートメーターの行のみに絞る。
            sql_power = f"""
                SELECT AVG(wattage)
                FROM {table_power}
                WHERE timestamp >= ? AND device_name LIKE '%Remo%'
            """
            cursor.execute(sql_power, (start_str,))
            row_pow = cursor.fetchone()
            avg_watts = row_pow[0] if row_pow and row_pow[0] else 0
            
            if avg_watts:
                elapsed_hours = (now - start_dt).total_seconds() / 3600
                if elapsed_hours < 0:
                    elapsed_hours = 0
                
                kwh = (avg_watts * elapsed_hours) / 1000
                bill = int(kwh * DEFAULT_ELEC_PRICE_PER_KWH)
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
            # 設計書 8.2: エラーログ運用 (Tracebackを含めるべきだがここでは簡易化) [cite: 151]
            logger.error(f"集計エラー (start={start_str}): {e}", exc_info=True)
            return None

def generate_text_section(period_name: str, data: Dict[str, Any], is_simple: bool = False) -> str:
    """集計データからレポート用のテキストセクションを生成する。

    Args:
        period_name (str): 期間の名称（例: "今週のまとめ"）。
        data (Dict[str, Any]): get_analysis_data で取得した集計データ。
        is_simple (bool, optional): 簡易表示モードかどうか。デフォルトは False。

    Returns:
        str: 整形されたレポートテキスト。
    """
    if not data:
        return ""

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

def is_month_end_report() -> bool:
    """今日がその月の最後のレポート日(日曜日/月曜日)か判定する。

    Returns:
        bool: 月末レポート対象日であれば True。
    """
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    next_week = now + datetime.timedelta(days=7)
    return now.month != next_week.month

def run_report() -> None:
    """週間レポート生成プロセスのメインエントリーポイント。"""
    
    # 実行タイミング制御
    # 引数 "--force" があれば強制実行する
    is_force = len(sys.argv) > 1 and sys.argv[1] == "--force"
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    
    # scheduler.py (Cron) 側で制御している前提だが、念のためガードを入れる
    is_monday = (now.weekday() == 0) # 0=Monday
    is_morning = (now.hour == 8)     # 8時台
    
    if not is_force and not (is_monday and is_morning):
        logger.debug(f"⏭️ 現在はレポート送信タイミングではありません ({now.strftime('%a %H:%M')}) - Skip")
        return

    # #234: 実行済みフラグチェック (外部cronの多重起動による重複送信防止)
    today_str = now.strftime("%Y-%m-%d")
    if not is_force and os.path.exists(LAST_RUN_FILE):
        with open(LAST_RUN_FILE, "r") as f:
            last_run = f.read().strip()
        if last_run == today_str:
            logger.debug(f"⏭️ 本日は既に週間レポートを送信済みのためスキップします ({today_str})")
            return

    logger.info("📊 週間レポート生成プロセスを開始します...")
    
    date_fmt = "%m/%d"

    # 1. 期間ごとの集計
    start_week = get_start_date("week")
    start_month = get_start_date("month")
    
    # 型チェック対応: start_weekなどがNoneの可能性を考慮
    if not start_week or not start_month:
        logger.error("❌ 日付計算に失敗しました")
        return

    stats_week = get_analysis_data(start_week)
    stats_month = get_analysis_data(start_month)
    
    if not stats_week:
        logger.error("❌ 週間データの取得に失敗しました")
        return

    # 期間文字列の生成 (例: 12/01～12/07)
    # now はレポート生成時点なので、前日(日曜)までのデータという意味合いで表示を調整
    range_week = f"{start_week.strftime(date_fmt)}～{(now - datetime.timedelta(days=1)).strftime(date_fmt)}"
    range_month = f"{start_month.strftime(date_fmt)}～{now.strftime(date_fmt)}"

    msg_header = "📊 **今週の我が家レポート** 📊\nおはようございます！今週も一週間お疲れ様でした🍵\n"
    
    msg_body = ""
    # 今週 (詳細)
    msg_body += generate_text_section(f"先週のまとめ ({range_week})", stats_week) + "\n"
    
    # 今月 (シンプル)
    if stats_month:
        msg_body += "------------------\n"
        msg_body += f"🗓️ {range_month} の累計: {generate_text_section('', stats_month, is_simple=True)}\n"
    
    # 2. 月末のみ「今年のトータル」を追加
    if is_month_end_report():
        logger.info("月末のため年次集計を実行します")
        start_year = get_start_date("year")
        
        if start_year:
            stats_year = get_analysis_data(start_year)
            
            if stats_year:
                range_year = f"{start_year.strftime(date_fmt)}～{now.strftime(date_fmt)}"
                msg_body += f"\n👑 **今年のトータル ({range_year})** 👑\n"
                msg_body += "今月もやりくりお疲れ様でした✨\n"
                
                total_cook = stats_year["food_counts"]["自炊"]
                total_bill = stats_year["elec_bill"]
                
                msg_body += f"🍳 自炊回数: {total_cook}回！すごいです✨\n"
                msg_body += f"⚡ 年間電気代: 約{total_bill:,}円\n"

    msg_footer = "\n今週も無理せず、楽しくいきましょうね✨"

    full_msg = msg_header + msg_body + msg_footer
    
    # LINE通知実行 (設計書 4.4: LINE Bot連携) [cite: 72]
    # common.send_push は設計書外の共通関数と想定されるが、ロガー運用に従い結果を記録
    if common.send_push(config.LINE_USER_ID, [{"type": "text", "text": full_msg}], target="discord"):
        logger.info("✅ レポート送信完了")
        # #234: 定時実行のときのみフラグを記録する(強制実行時は手動テスト用途のため記録しない。
        # monitors/timelapse_runner.pyの--force時の扱いと同様)
        if not is_force:
            os.makedirs(os.path.dirname(LAST_RUN_FILE), exist_ok=True)
            with open(LAST_RUN_FILE, "w") as f:
                f.write(today_str)
    else:
        logger.error("❌ レポート送信失敗")

if __name__ == "__main__":
    run_report()