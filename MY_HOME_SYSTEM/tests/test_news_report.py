import sys
import os

# 既存のモジュールを読み込めるようにパスを追加
sys.path.append(os.getcwd())

from news_service import NewsService
from weather_service import WeatherService
from send_ai_report import setup_gemini, generate_report, fetch_daily_data

print("🧪 --- ニュース統合テスト開始 ---")

# 1. ニュース取得テスト
print("\n[1] ニュース取得テスト")
news = NewsService().get_top_news()
if news:
    print(f"✅ 取得成功: {news[:3]} ... (他{len(news)-3}件)")
else:
    print("⚠️ ニュース取得失敗（またはニュースなし）")

# 2. レポート生成テスト (送信はしない)
print("\n[2] レポート生成シミュレーション")
try:
    print("   モデルセットアップ中...")
    model = setup_gemini()
    
    print("   データ収集中...")
    # 注意: fetch_daily_dataはDB接続を伴うため、環境によっては失敗する可能性があります
    # その場合はダミーデータを使用してください
    try:
        data = fetch_daily_data()
    except Exception as e:
        print(f"   ⚠️ DB接続エラーのためダミーデータを使用: {e}")
        data = {
            "environment": [], "parents_home": {}, "electricity": {"avg_watts": 500}, 
            "car_outing_count": 0, "children_health": [],
            "weather_report": "晴れ、気温20度",
            "news_topics": news
        }

    print("   AI文章生成中...")
    report = generate_report(model, data)
    
    print(f"\n📄 生成されたレポート:\n{'-'*40}\n{report}\n{'-'*40}")
    print("\n✅ テスト完了")

except Exception as e:
    print(f"❌ テスト失敗: {e}")
    import traceback
    traceback.print_exc()