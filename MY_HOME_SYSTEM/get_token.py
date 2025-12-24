# get_token.py (ローカルPC実行用)
import os
from google_auth_oauthlib.flow import InstalledAppFlow

# 設定
SCOPES = ['https://www.googleapis.com/auth/photoslibrary.readonly']
CREDENTIALS_FILE = 'google_photos_credentials.json'
TOKEN_FILE = 'google_photos_token.json'

def main():
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"❌ '{CREDENTIALS_FILE}' が見つかりません。配置してください。")
        return

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    print("🌍 ブラウザが起動します。Googleアカウントでログインして許可してください...")
    
    # ここでブラウザが開き、認証を求められます
    creds = flow.run_local_server(port=0)

    # 成功したらトークンを保存
    with open(TOKEN_FILE, 'w') as token:
        token.write(creds.to_json())
    
    print(f"✅ 認証成功！ '{TOKEN_FILE}' が生成されました。")
    print("👉 このファイルをラズパイの 'MY_HOME_SYSTEM/' ディレクトリに配置してください。")

if __name__ == '__main__':
    main()