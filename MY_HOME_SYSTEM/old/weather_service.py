# MY_HOME_SYSTEM/weather_service.py
import os
import requests
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any, Union
from dotenv import load_dotenv

import config
import common

# ロガー設定 (commonの設定を利用)
logger = logging.getLogger('WeatherService')

class WeatherService:
    """
    天気予報を取得し、データベースへの保存とユーザーへの通知を行うクラス。
    """
    
    # 定数定義
    API_URL: str = "http://api.openweathermap.org/data/2.5/forecast"
    REQUEST_TIMEOUT: int = 10
    SWITCH_TO_TOMORROW_HOUR: int = 17  # この時間を過ぎたら明日の天気を案内する

    # 監視対象の都市リスト
    TARGET_LOCATIONS: List[Dict[str, Union[str, float]]] = [
        {"name": "伊丹", "lat": 34.78, "lon": 135.41}, # 兵庫県伊丹市
        {"name": "高砂", "lat": 34.76, "lon": 134.80}, # 兵庫県高砂市
        {"name": "奈良", "lat": 34.68, "lon": 135.80}, # 奈良県奈良市
    ]

    def __init__(self) -> None:
        self.base_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.api_key: Optional[str] = None
        self._load_environment()
        self._ensure_table_schema() 

    def _load_environment(self) -> None:
        """環境変数の読み込み"""
        dotenv_path = os.path.join(self.base_dir, '.env')
        load_dotenv(dotenv_path)
        self.api_key = os.getenv("OPENWEATHER_API_KEY")
        if not self.api_key:
            logger.warning("OpenWeatherMap API Key is missing in .env")

    def _ensure_table_schema(self) -> None:
        """データベースのテーブル構造を確認し、必要に応じてマイグレーションを行う"""
        # common.get_db_cursor を使用してリソース管理を委譲
        with common.get_db_cursor(commit=True) as cursor:
            if not cursor:
                return

            try:
                # テーブル存在確認
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='weather_history'")
                row = cursor.fetchone()
                
                if not row:
                    self._create_new_table(cursor)
                    logger.info("🛠️ DB Init: Created weather_history table.")
                else:
                    # 複合ユニーク制約の確認
                    create_sql: str = row[0]
                    # 表記ゆれ対応: "UNIQUE(date, location)" vs "UNIQUE (date, location)"
                    if "UNIQUE(date, location)" not in create_sql and "UNIQUE (date, location)" not in create_sql:
                        logger.info("🛠️ DB Migration: Updating table schema...")
                        # マイグレーション実行 (connectionが必要なためcursor.connectionを参照)
                        self._migrate_table(cursor)
                    else:
                        self._add_missing_columns(cursor)

            except Exception as e:
                self._handle_error(f"DB Schema Check Error: {e}")

    def _create_new_table(self, cursor: sqlite3.Cursor) -> None:
        """新規テーブル作成用SQL"""
        sql = """
        CREATE TABLE IF NOT EXISTS weather_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            location TEXT DEFAULT '伊丹',
            min_temp INTEGER,
            max_temp INTEGER,
            weather_desc TEXT,
            max_pop INTEGER,
            umbrella_level TEXT,
            recorded_at TEXT,
            UNIQUE(date, location)
        )
        """
        cursor.execute(sql)

    def _add_missing_columns(self, cursor: sqlite3.Cursor) -> None:
        """カラム不足時の追加処理"""
        try:
            cursor.execute("PRAGMA table_info(weather_history)")
            # row[1] is name
            cols: List[str] = [row[1] for row in cursor.fetchall()]
            
            if "location" not in cols:
                cursor.execute("ALTER TABLE weather_history ADD COLUMN location TEXT")
            if "max_pop" not in cols:
                cursor.execute("ALTER TABLE weather_history ADD COLUMN max_pop INTEGER")
            if "umbrella_level" not in cols:
                cursor.execute("ALTER TABLE weather_history ADD COLUMN umbrella_level TEXT")
        except Exception as e:
            logger.warning(f"Column add warning: {e}")

    def _migrate_table(self, cursor: sqlite3.Cursor) -> None:
        """テーブル再作成によるマイグレーション"""
        # cursor.connection を使用して同じトランザクション内で処理するか、
        # common.get_db_cursor(commit=True) 内なので cursor.execute だけで完結させる
        try:
            cursor.execute("DROP TABLE IF EXISTS weather_history_backup")
            cursor.execute("ALTER TABLE weather_history RENAME TO weather_history_backup")
            self._create_new_table(cursor)
            
            # カラムマッピングを動的に生成してデータ移行
            cursor.execute("PRAGMA table_info(weather_history_backup)")
            old_cols: List[str] = [r[1] for r in cursor.fetchall()]
            
            cols_to_copy: List[str] = ['date', 'min_temp', 'max_temp', 'weather_desc', 'recorded_at']
            select_parts: List[str] = list(cols_to_copy)
            insert_parts: List[str] = list(cols_to_copy)
            
            # 必須カラムのデフォルト値対応
            if 'location' in old_cols:
                select_parts.append("COALESCE(location, '伊丹')")
            else:
                select_parts.append("'伊丹'")
            insert_parts.append('location')

            if 'max_pop' in old_cols:
                select_parts.append("max_pop")
                insert_parts.append('max_pop')
            if 'umbrella_level' in old_cols:
                select_parts.append("umbrella_level")
                insert_parts.append('umbrella_level')

            sql = f"INSERT INTO weather_history ({', '.join(insert_parts)}) SELECT {', '.join(select_parts)} FROM weather_history_backup"
            cursor.execute(sql)
            cursor.execute("DROP TABLE weather_history_backup")
        except Exception as e:
            # 呼び出し元でログ出力させるため再送出
            raise e

    def _handle_error(self, message: str) -> None:
        """エラーハンドリング共通処理（ログ出力 + Discord通知）"""
        logger.error(message)
        try:
            common.send_push(
                config.LINE_USER_ID, 
                [{"type": "text", "text": f"⚠️ 天気システムエラー\n{message}"}], 
                target="discord", 
                channel="error"
            )
        except Exception:
            pass # 通知エラーは握りつぶしてループを防ぐ

    def get_weather_report_text(self) -> str:
        """
        レポート用テキストを生成する（既存機能の維持）
        """
        reports: List[str] = []
        target_date, date_label = self._determine_target_date()
        target_date_str = target_date.strftime('%Y-%m-%d')

        # print -> logger.info に変更
        logger.info(f"🌤️ 天気取得開始: {date_label} ({target_date_str}) のデータを取得します...")
        
        for loc in self.TARGET_LOCATIONS:
            # 型ヒントのためにキャスト (TARGET_LOCATIONSの構造は保証されている)
            lat: float = float(loc["lat"])
            lon: float = float(loc["lon"])
            name: str = str(loc["name"])

            # 1. APIデータ取得
            raw_data = self._get_forecast_data(lat, lon)
            if not raw_data:
                reports.append(f"❌ {name}: 情報取得に失敗しました")
                continue

            # 2. データ解析
            summary = self._analyze_weather_for_date(raw_data, name, target_date_str)
            
            if summary:
                # 3. DB保存
                if not self._save_to_db(summary):
                    logger.warning(f"Failed to save weather data for {name}")

                # 4. 主婦向けメッセージ生成
                advice = self._generate_advice_message(summary)
                
                # アイコン決定
                icon = "🌂"
                if summary["umbrella_level"] == "必須":
                    icon = "☔"
                elif summary["umbrella_level"] == "不要":
                    icon = "☀️"
                
                # レポート形式
                msg = (f"{name}({date_label}): {summary['description']} "
                       f"(🌡️{summary['max_temp']}/{summary['min_temp']}°C) {icon}{summary['umbrella_level']}\n"
                       f"└ {advice}")
                reports.append(msg)
            else:
                reports.append(f"❓ {name}: 予報データが見つかりませんでした")

        return "\n\n".join(reports)

    def notify_weather_info(self, target: str = "line") -> None:
        """
        天気を取得して指定のターゲットに通知を送る
        """
        report_text = self.get_weather_report_text()
        if not report_text:
            return

        # ヘッダー作成
        header = "☀️ お天気情報をお届けします"
        
        messages = [{"type": "text", "text": f"{header}\n\n{report_text}"}]
        
        try:
            common.send_push(config.LINE_USER_ID, messages, target=target)
            # print -> logger.info
            logger.info(f"✅ 通知送信完了 ({target})")
        except Exception as e:
            self._handle_error(f"通知送信失敗: {e}")

    def _determine_target_date(self) -> Tuple[datetime, str]:
        """現在時刻に基づいて、対象日（今日 or 明日）を決定する"""
        now = datetime.now()
        is_night = now.hour >= self.SWITCH_TO_TOMORROW_HOUR
        
        target_date = now + timedelta(days=1) if is_night else now
        date_label = "明日" if is_night else "今日"
        return target_date, date_label

    def _get_forecast_data(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """OpenWeatherMap APIからデータを取得"""
        if not self.api_key:
            return None
            
        params = {
            "lat": lat, 
            "lon": lon, 
            "appid": self.api_key, 
            "units": "metric", 
            "lang": "ja"
        }
        
        try:
            res = requests.get(self.API_URL, params=params, timeout=self.REQUEST_TIMEOUT)
            res.raise_for_status()
            return res.json()
        except requests.exceptions.RequestException as e:
            self._handle_error(f"API接続エラー ({lat}, {lon}): {e}")
            return None

    def _analyze_weather_for_date(self, data: Dict[str, Any], location_name: str, target_date_str: str) -> Optional[Dict[str, Any]]:
        """指定した日付のデータを抽出し、集計する"""
        
        forecasts_for_target_date = [
            item for item in data.get("list", []) 
            if target_date_str in item["dt_txt"]
        ]
        
        # データがない場合（深夜など）は、リストの先頭から直近8個（24時間分）を使用するバックアップ処理
        if not forecasts_for_target_date:
            logger.info(f"{target_date_str} のデータがないため、直近データを使用します。")
            forecasts_for_target_date = data.get("list", [])[:8]

        if not forecasts_for_target_date:
            return None

        # 気温（四捨五入して整数化）
        temps = [x["main"]["temp"] for x in forecasts_for_target_date]
        max_temp = int(round(max(temps)))
        min_temp = int(round(min(temps)))
        
        # 降水確率 (0-1 -> 0-100)
        pops = [x.get("pop", 0) * 100 for x in forecasts_for_target_date]
        max_pop = int(max(pops))
        
        # 天気説明（最頻値）
        descs = [x["weather"][0]["description"] for x in forecasts_for_target_date]
        main_desc = max(set(descs), key=descs.count)
        
        # 傘判定
        weather_ids = [x["weather"][0]["id"] for x in forecasts_for_target_date]
        umbrella_level = self._judge_umbrella_necessity(max_pop, weather_ids)

        return {
            "date": target_date_str,
            "location": location_name,
            "max_temp": max_temp,
            "min_temp": min_temp,
            "max_pop": max_pop,
            "description": main_desc,
            "umbrella_level": umbrella_level
        }

    def _judge_umbrella_necessity(self, max_pop: int, weather_ids: List[int]) -> str:
        """降水確率と天気IDから傘の必要性を判定"""
        # ID 2xx: 雷雨, 5xx: 雨
        has_heavy_rain = any(200 <= wid < 600 for wid in weather_ids) 
        # ID 3xx: 小雨
        has_light_rain = any(300 <= wid < 400 for wid in weather_ids)
        
        if max_pop >= 50 or has_heavy_rain:
            return "必須"
        elif max_pop >= 30 or has_light_rain:
            return "あるほうがいい"
        else:
            return "不要"

    def _generate_advice_message(self, summary: Dict[str, Any]) -> str:
        """主婦が好む表現で一言アドバイスを生成"""
        level = summary["umbrella_level"]
        temp_diff = summary["max_temp"] - summary["min_temp"]
        max_temp = summary["max_temp"]
        
        msg = ""
        
        # 傘について
        if level == "必須":
            msg = "しっかりした傘を持ってお出かけください☔"
        elif level == "あるほうがいい":
            msg = "折りたたみ傘があると安心ですよ🌂"
        else:
            if max_temp > 25:
                msg = "日傘があるといいかもしれませんね👒"
            else:
                msg = "お洗濯物がよく乾きそうです👕"

        # 気温についての一言追加
        if temp_diff > 10:
            msg += " 寒暖差が大きいので、羽織るものがあると便利です。"
        elif max_temp < 5:
            msg += " とても寒いので温かくしてくださいね🧣"
        elif max_temp > 30:
            msg += " 水分補給を忘れずに🥤"
            
        return msg

    def _save_to_db(self, summary: Dict[str, Any]) -> bool:
        """DBへの保存処理（トランザクション管理含む）"""
        # common.get_db_cursor を使用して一元管理
        with common.get_db_cursor(commit=True) as cursor:
            if not cursor:
                return False

            try:
                # Upsert文 (SQLite 3.24+)
                sql = """
                INSERT INTO weather_history 
                (date, location, min_temp, max_temp, weather_desc, max_pop, umbrella_level, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, location) DO UPDATE SET
                    min_temp=excluded.min_temp,
                    max_temp=excluded.max_temp,
                    weather_desc=excluded.weather_desc,
                    max_pop=excluded.max_pop,
                    umbrella_level=excluded.umbrella_level,
                    recorded_at=excluded.recorded_at
                """
                
                vals = (
                    summary["date"],
                    summary["location"],
                    summary["min_temp"],
                    summary["max_temp"],
                    summary["description"],
                    summary["max_pop"],
                    summary["umbrella_level"],
                    common.get_now_iso()
                )
                
                cursor.execute(sql, vals)
                # print -> logger.info
                logger.info(f"💾 DB保存完了: {summary['location']} ({summary['date']})")
                return True
                
            except Exception as e:
                self._handle_error(f"DB保存エラー: {e}")
                return False

if __name__ == "__main__":
    # 単体テスト実行
    logging.basicConfig(level=logging.INFO) # commonを使わない場合のフォールバック用
    ws = WeatherService()
    # コンソール出力のみ確認したい場合
    print("\n=== レポートプレビュー ===")
    print(ws.get_weather_report_text())
    print("========================\n")