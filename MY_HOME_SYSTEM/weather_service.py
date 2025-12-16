import os
import requests
import logging
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
import config
import common

logger = logging.getLogger('WeatherService')

class WeatherService:
    API_URL = "http://api.openweathermap.org/data/2.5/forecast"
    REQUEST_TIMEOUT = 10

    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self._load_environment()

    def _load_environment(self):
        dotenv_path = os.path.join(self.base_dir, '.env')
        load_dotenv(dotenv_path)
        self.api_key = os.getenv("OPENWEATHER_API_KEY")
        self.lat = os.getenv("MY_LAT")
        self.lon = os.getenv("MY_LON")

    def get_weather_report(self) -> str:
        """AIレポート用テキスト生成 & DB保存"""
        data = self._get_forecast_data()
        if not data:
            return "（天気情報の取得に失敗しました）"

        summary = self._analyze_today_weather(data)
        if summary:
            # ★ここでDBに保存
            self._save_to_db(summary)
            return self._create_message(summary)
        return "（データ解析失敗）"

    def _save_to_db(self, summary):
        """予報データをDBに記録（年間グラフ用）"""
        try:
            today_str = datetime.now().strftime('%Y-%m-%d')
            with common.get_db_cursor(commit=True) as cursor:
                # 同じ日付なら上書き更新
                cursor.execute('''
                    INSERT OR REPLACE INTO weather_history (date, min_temp, max_temp, weather_desc, recorded_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    today_str, 
                    summary['min_temp'], 
                    summary['max_temp'], 
                    summary['description'], 
                    common.get_now_iso()
                ))
            logger.info(f"天気データをDBに保存: {today_str}")
        except Exception as e:
            logger.error(f"天気DB保存エラー: {e}")

    def _get_forecast_data(self):
        if not self.api_key: return None
        params = {"lat": self.lat, "lon": self.lon, "appid": self.api_key, "units": "metric", "lang": "ja"}
        try:
            res = requests.get(self.API_URL, params=params, timeout=self.REQUEST_TIMEOUT)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"APIエラー: {e}")
            return None

    def _analyze_today_weather(self, data):
        today_str = datetime.now().strftime('%Y-%m-%d')
        target = [i for i in data.get("list", []) if today_str in i["dt_txt"]]
        
        # データがない場合(夜など)はリストの先頭(直近)を使用
        if not target:
            target = data.get("list", [])[:8]

        if not target: return None

        temps = [x["main"]["temp"] for x in target]
        pops = [x.get("pop", 0) * 100 for x in target]
        descs = [x["weather"][0]["description"] for x in target]

        return {
            "max_temp": max(temps),
            "min_temp": min(temps),
            "max_pop": max(pops),
            "description": max(set(descs), key=descs.count) # 最頻値
        }

    def _create_message(self, summary):
        # AIプロンプト側で「洗濯禁止」を制御するため、ここでは事実のみを返す
        return (
            f"【天気: {summary['description']}】\n"
            f"🌡️ 最高: {summary['max_temp']}℃ / 最低: {summary['min_temp']}℃\n"
            f"💧 降水確率: {int(summary['max_pop'])}%"
        )