# MY_HOME_SYSTEM/verify_photo_feature.py
import os
import sys

# パスを通す
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
# 新しく作ったモジュールと修正したモジュールをインポート
try:
    from google_photos_service import GooglePhotosService
    from send_ai_report import build_system_prompt
except ImportError as e:
    print(f"❌ インポートエラー: {e}")
    print("ファイル名や配置場所が正しいか確認してください。")
    sys.exit(1)

def test_google_photos_connection():
    print("\n📸 [Test 1] Google Photos API 接続テスト")
    try:
        service = GooglePhotosService()

        # ★追加: サービスが利用可能かチェック
        if not service.service:
            print("   ❌ 認証に失敗しているため、テストを中止します。ログを確認してください。")
            return []

        # 過去10日間の写真を1枚だけ取得してみる
        print("   写真を探しています...")
        photos = service.get_recent_photos(limit=1, days=10)

        if photos:
            p = photos[0]
            print(f"   ✅ 成功: 写真が見つかりました！")
            print(f"      ファイル名: {p['filename']}")
            print(f"      撮影日時: {p['timestamp']}")
            return photos
        else:
            print("   ⚠️ 接続は成功しましたが、直近10日間に写真が見つかりませんでした。")
            print("      (Googleフォトに新しい写真があれば取得されます)")
            return []
            
    except Exception as e:
        print(f"   ❌ エラー発生: {e}")
        return []

def test_gemini_analysis(photos):
    print("\n🧠 [Test 2] Gemini 写真分析テスト")
    if not photos:
        print("   ⏭️ 写真がないためスキップします")
        return

    if not config.GEMINI_API_KEY:
        print("   ⚠️ GEMINI_API_KEY が設定されていないためスキップします")
        return

    try:
        service = GooglePhotosService()
        print("   Geminiに写真を送信中...")
        result = service.analyze_photos_with_gemini(photos)
        print(f"   ✅ 分析結果:\n{'-'*20}\n{result}\n{'-'*20}")
    except Exception as e:
        print(f"   ❌ エラー発生: {e}")

def test_prompt_generation():
    print("\n📝 [Test 3] プロンプト生成ロジックの確認")
    
    # ダミーデータ（写真分析の結果が入ったと仮定）
    dummy_data = {
        'weather_report': '晴れ',
        'news_topics': [],
        'photo_analysis': '★テスト成功★ 家族で動物園に行っている写真です。',
        'environment': [],
        'electricity': {'avg_watts': 0}
    }
    
    try:
        # 修正した関数を呼び出し
        prompt = build_system_prompt(dummy_data)
        
        # 結果確認
        if "今日の写真ハイライト" in prompt and "★テスト成功★" in prompt:
            print("   ✅ 成功: プロンプトに写真セクションが含まれています！")
            print("   ▼ 生成されたプロンプトの一部:")
            
            # 該当部分を抜き出して表示
            start = prompt.find("【今日の写真ハイライト】")
            end = prompt.find("レポートの後半で") + 20
            print(f"      {prompt[start:end]}...")
        else:
            print("   ❌ 失敗: プロンプトに写真情報が反映されていません。")
            print("      send_ai_report.py の修正箇所をもう一度確認してください。")
            
    except Exception as e:
        print(f"   ❌ エラー発生: {e}")

if __name__ == "__main__":
    print("🚀 検証スクリプトを開始します...")
    
    # 1. API接続確認
    found_photos = test_google_photos_connection()
    
    # 2. Gemini分析確認 (APIキーがある場合のみ)
    test_gemini_analysis(found_photos)
    
    # 3. プロンプト組み込み確認
    test_prompt_generation()
    
    print("\n🏁 検証終了")