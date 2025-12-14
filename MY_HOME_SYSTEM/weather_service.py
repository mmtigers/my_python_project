import os
import requests
import logging
from datetime import datetime
from dotenv import load_dotenv

# ログ設定（呼び出し元と共有）
logger = logging.getLogger('WeatherService')

class WeatherService:
    """
    OpenWeatherMapから天気情報を取得し、
    生活に役立つアドバイス付きのテキストを生成するクラス
    """
    
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

        if not self.api_key:
            logger.error("❌ OPENWEATHER_API_KEY が設定されていません。")

    def get_weather_report(self) -> str:
        """
        メインメソッド: 天気情報を取得して整形されたメッセージを返す
        """
        data = self._get_forecast_data()
        if not data:
            return "⚠️ 天気情報の取得に失敗しました。"

        summary = self._analyze_today_weather(data)
        return self._create_message(summary)

    def _get_forecast_data(self):
        params = {
            "lat": self.lat,
            "lon": self.lon,
            "appid": self.api_key,
            "units": "metric",
            "lang": "ja"
        }
        try:
            res = requests.get(self.API_URL, params=params, timeout=self.REQUEST_TIMEOUT)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"❌ 天気API取得エラー: {e}")
            return None

    def _analyze_today_weather(self, data):
        today_str = datetime.now().strftime('%Y-%m-%d')
        target_forecasts = [item for item in data.get("list", []) if today_str in item["dt_txt"]]
        
        if not target_forecasts:
            # 今日のデータがない場合は直近8個(24時間)を使用
            target_forecasts = data.get("list", [])[:8]

        temps = [x["main"]["temp"] for x in target_forecasts]
        pops = [x.get("pop", 0) * 100 for x in target_forecasts]
        weather_descs = [x["weather"][0]["description"] for x in target_forecasts]
        most_common_weather = max(set(weather_descs), key=weather_descs.count)

        return {
            "max_temp": max(temps),
            "min_temp": min(temps),
            "max_pop": max(pops),
            "description": most_common_weather
        }

    def _create_message(self, summary):
        max_t = round(summary["max_temp"], 1)
        min_t = round(summary["min_temp"], 1)
        pop = int(summary["max_pop"])
        desc = summary["description"]

        advice = ""
        if pop >= 50:
            advice = "☔ 傘を忘れずに。洗濯物は部屋干し推奨です。"
        elif pop >= 30:
            advice = "☁️ 折りたたみ傘があると安心です。"
        else:
            advice = "👕 外干し日和になりそうです✨"

        if max_t >= 30:
            advice += " 熱中症に注意！"
        elif max_t < 10:
            advice += " 温かくしてお出かけください。"
        
        if (max_t - min_t) > 10:
            advice += " 寒暖差が大きいので羽織るものを。"

        # 既存のメッセージに組み込みやすいよう、シンプルな形式で返す
        return (
            f"【伊丹市の天気: {desc}】\n"
            f"🌡️ {max_t}℃ / {min_t}℃  💧 降水確率: {pop}%\n"
            f"{advice}"
        )

if __name__ == "__main__":
    # テスト実行用
    service = WeatherService()
    print(service.get_weather_report())