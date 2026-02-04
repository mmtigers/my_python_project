import re
import os
import sys
import requests
from tqdm import tqdm
from urllib.parse import urlparse

class VideoDownloader:
    def __init__(self):
        # 保存先の絶対パス設定
        self.save_dir = "/mnt/nas/ddd"
        
        # ヘッダー設定
        self.base_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
        }

    def fetch_html(self, url):
        """URLからHTMLを取得"""
        try:
            print("🌍 サイトにアクセス中...")
            headers = self.base_headers.copy()
            headers['Referer'] = url
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"❌ サイトへのアクセスに失敗しました: {e}")
            return None

    def extract_video_urls(self, html_content):
        """動画URL候補を抽出 [HD, SD]"""
        urls = []
        # HD画質
        match_hd = re.search(r"video_alt_url\s*:\s*['\"]([^'\"]+)['\"]", html_content)
        if match_hd:
            url = match_hd.group(1).strip().rstrip('/')
            urls.append(('HD (高画質)', url))

        # 標準画質
        match_sd = re.search(r"video_url\s*:\s*['\"]([^'\"]+)['\"]", html_content)
        if match_sd:
            url = match_sd.group(1).strip().rstrip('/')
            urls.append(('SD (標準画質)', url))
            
        return urls

    def generate_filename_from_url(self, page_url):
        """
        URLの末尾からファイル名を生成する
        例: .../venz-036-242/ -> venz-036-242.mp4
        """
        # 末尾のスラッシュを除去して分割
        clean_url = page_url.split('?')[0].rstrip('/')
        filename_base = clean_url.split('/')[-1]
        
        # 万が一空文字になった場合の対策
        if not filename_base:
            filename_base = "video_download"
            
        return f"{filename_base}.mp4"

    def download_file(self, video_candidates, filename, page_url):
        """NASへ保存実行"""
        
        # 保存先ディレクトリの確認と作成
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except PermissionError:
            print(f"❌ エラー: 保存先 '{self.save_dir}' への書き込み権限がありません。")
            return

        file_path = os.path.join(self.save_dir, filename)

        # Referer偽装
        headers = self.base_headers.copy()
        headers['Referer'] = page_url

        success = False

        for label, video_url in video_candidates:
            print(f"🔄 {label} のリンクを試行中...")
            
            try:
                response = requests.get(video_url, stream=True, headers=headers, timeout=20)
                
                if response.status_code == 404:
                    print(f"⚠️ {label} は見つかりませんでした (404)。次の候補を試します。")
                    continue
                
                response.raise_for_status()

                total_size = int(response.headers.get('content-length', 0))
                block_size = 1024 * 1024 # 1MB

                print(f"📥 保存開始: {file_path}")
                
                with open(file_path, 'wb') as file, tqdm(
                    desc=filename,
                    total=total_size,
                    unit='iB',
                    unit_scale=True,
                    unit_divisor=1024,
                    colour='green'
                ) as bar:
                    for data in response.iter_content(block_size):
                        size = file.write(data)
                        bar.update(size)
                
                print(f"\n✨ ダウンロード完了！")
                success = True
                break

            except Exception as e:
                print(f"❌ {label} のダウンロード中にエラー: {e}")
                # 失敗した書きかけファイルがあれば削除（ゴミを残さない）
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass
                continue

        if not success:
            print("\n⛔ すべての候補でダウンロードに失敗しました。")

def main():
    print("="*50)
    print("   NAS保存用ダウンローダー (/mnt/nas/ddd)")
    print("="*50)
    
    while True:
        target_url = input("\n動画URLを入力 (終了は q): ").strip()
        
        if target_url.lower() == 'q':
            break
        
        if not target_url.startswith('http'):
            print("⚠️ URLは http から始めてください。")
            continue

        downloader = VideoDownloader()
        
        # 1. HTML取得
        html = downloader.fetch_html(target_url)
        if not html:
            continue

        # 2. リンク抽出
        video_candidates = downloader.extract_video_urls(html)
        if not video_candidates:
            print("⚠️ 動画URLが見つかりませんでした。")
            continue

        # 3. URLからファイル名決定
        filename = downloader.generate_filename_from_url(target_url)
        print(f"📝 ファイル名設定: {filename}")

        # 4. ダウンロード
        downloader.download_file(video_candidates, filename, target_url)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断しました。")
        sys.exit()