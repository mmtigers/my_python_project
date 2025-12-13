# check_models.py
import google.generativeai as genai
import config

if config.GEMINI_API_KEY:
    genai.configure(api_key=config.GEMINI_API_KEY)
    
    print("--- 利用可能なGeminiモデル一覧 ---")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f"✅ {m.name}")
    except Exception as e:
        print(f"エラー: {e}")
else:
    print("APIキーが設定されていません")