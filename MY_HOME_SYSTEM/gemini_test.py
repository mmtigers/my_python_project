import google.generativeai as genai
import os
from dotenv import load_dotenv  # 追加: .env読み込み用ライブラリ

# .envファイルを読み込む
load_dotenv() 

# 読み込んだ環境変数を使用
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

print("利用可能なモデル一覧:")

print("利用可能なモデル一覧:")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)