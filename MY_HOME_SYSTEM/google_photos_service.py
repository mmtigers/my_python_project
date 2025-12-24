# MY_HOME_SYSTEM/google_photos_service.py
import os.path
import pickle
import requests
import logging
from datetime import datetime, timedelta
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import google.generativeai as genai
from PIL import Image
from io import BytesIO

import config
import common

# ロガー設定
logger = common.setup_logging("google_photos")

class GooglePhotosService:
    def __init__(self):
        self.creds = None
        self.service = None
        self._authenticate()
        self._setup_gemini()

    def _authenticate(self):
        """Google Photos APIの認証を行う"""
        try:
            # トークンファイルが存在すれば読み込む
            if os.path.exists(config.GOOGLE_PHOTOS_TOKEN):
                self.creds = Credentials.from_authorized_user_file(config.GOOGLE_PHOTOS_TOKEN, config.GOOGLE_PHOTOS_SCOPES)
            
            # 有効な認証情報がない場合、新規取得またはリフレッシュ
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    logger.info("🔄 トークンをリフレッシュします...")
                    self.creds.refresh(Request())
                else:
                    logger.info("🆕 新規認証フローを開始します (ブラウザ認証が必要です)")
                    # 注意: ヘッドレス環境ではローカルPCで作成したtoken.jsonを転送することを推奨
                    flow = InstalledAppFlow.from_client_secrets_file(
                        config.GOOGLE_PHOTOS_CREDENTIALS, config.GOOGLE_PHOTOS_SCOPES)
                    self.creds = flow.run_local_server(port=0)
                
                # トークンを保存
                with open(config.GOOGLE_PHOTOS_TOKEN, 'w') as token:
                    token.write(self.creds.to_json())
            
            self.service = build('photoslibrary', 'v1', credentials=self.creds, static_discovery=False)
            logger.info("✅ Google Photos API 接続成功")
            
        except Exception as e:
            # ★追加: エラーの詳細を記録し、service は None とする
            logger.error(f"Google Photos 認証エラー: {e}")
            self.service = None

    def _setup_gemini(self):
        """Geminiのセットアップ"""
        if config.GEMINI_API_KEY:
            genai.configure(api_key=config.GEMINI_API_KEY)
        else:
            logger.warning("⚠️ GEMINI_API_KEYが設定されていません")

    def get_recent_photos(self, limit=5, days=1):
        """直近の写真をバイナリデータとして取得する"""
        if not self.service:
            logger.error("❌ Google Photos APIに接続されていません。認証を確認してください。")
            return []

        # 日付フィルタの作成
        today = datetime.now()
        start_date = today - timedelta(days=days)
        
        # 検索条件 (DateFilterを使用)
        date_filter = {
            "dateFilter": {
                "ranges": [{
                    "startDate": {"year": start_date.year, "month": start_date.month, "day": start_date.day},
                    "endDate": {"year": today.year, "month": today.month, "day": today.day}
                }]
            }
        }

        try:
            logger.info(f"📸 過去{days}日間の写真を検索中...")
            results = self.service.mediaItems().search(body={
                'pageSize': limit,
                'filters': date_filter
            }).execute()
            
            items = results.get('mediaItems', [])
            logger.info(f"👉 {len(items)} 件のメディアが見つかりました")

            photos_data = []
            for item in items:
                # 動画は今回スキップ（Geminiは動画もいけますが、処理を軽くするため画像のみ）
                if "image" not in item.get("mimeType", ""):
                    continue

                # 画像データのダウンロード
                # baseUrlにパラメータを付与してダウンロード (w=幅, h=高さ, d=ダウンロード)
                download_url = f"{item['baseUrl']}=w1024-h1024" 
                res = requests.get(download_url, headers={"Authorization": f"Bearer {self.creds.token}"})
                
                if res.status_code == 200:
                    img = Image.open(BytesIO(res.content))
                    photos_data.append({
                        "id": item['id'],
                        "filename": item['filename'],
                        "timestamp": item['mediaMetadata']['creationTime'],
                        "image_obj": img
                    })
                else:
                    logger.warning(f"ダウンロード失敗: {item['filename']}")

            return photos_data

        except Exception as e:
            # ★追加: スコープ不足エラーの具体的検知
            error_str = str(e)
            if "insufficient authentication scopes" in error_str:
                logger.error("❌ 権限エラー: トークンのスコープが不足しています。")
                logger.error("👉 対処法: 'google_photos_token.json' を削除し、再度スクリプトを実行して再認証してください。")
            else:
                logger.error(f"写真検索エラー: {e}")
            return []

    def analyze_photos_with_gemini(self, photos_data):
        """取得した写真をGeminiに投げて分析させる"""
        if not photos_data or not config.GEMINI_API_KEY:
            return "分析対象の写真がないか、Geminiキーが未設定です。"

        # モデル選択 (画像対応モデル)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # プロンプト作成
        prompt = [
            "あなたは家族の思い出記録係です。以下の写真を見て、どのような出来事があったか、楽しい雰囲気でレポートしてください。",
            "また、写真から読み取れる情報（場所、食事、子供の様子など）があれば具体的に言及してください。",
            "出力フォーマット:",
            "- 📸 全体の要約",
            "- ✨ 特筆すべきポイント",
            "- 📝 各写真の簡単な説明"
        ]
        
        # 画像オブジェクトをプロンプトに追加
        for p in photos_data:
            prompt.append(p['image_obj'])
            prompt.append(f"(ファイル名: {p['filename']}, 撮影日時: {p['timestamp']})")

        try:
            logger.info("🧠 Geminiで画像を分析中...")
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"AI分析エラー: {e}")
            return "AIによる分析に失敗しました。"

if __name__ == "__main__":
    # テスト実行
    service = GooglePhotosService()
    
    # 直近3日間の写真を最大5枚取得
    photos = service.get_recent_photos(limit=5, days=3)
    
    if photos:
        report = service.analyze_photos_with_gemini(photos)
        print("\n=== 📸 Google Photos Analysis Report ===")
        print(report)
        
        # テスト時はDiscordに送ってみる
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"📸 **写真分析テスト**\n\n{report}"}], target="discord", channel="report")
    else:
        print("写真が見つかりませんでした。")