# MY_HOME_SYSTEM/camera_digest_service.py
import os
import glob
from datetime import datetime
import random
import config
import common

# ログ設定
logger = common.setup_logging("cam_digest")

SNAPSHOT_DIR = os.path.join(config.ASSETS_DIR, "snapshots")

def get_todays_highlight_images(limit=10):
    """
    今日のスナップショットから、AI解析用の画像を抽出する
    """
    today_str = datetime.now().strftime('%Y%m%d')
    # ファイル名パターン: snapshot_{id}_{YYYYMMDD}_{HHMMSS}.jpg
    # camera_monitor.pyの保存形式: snapshot_{id}_{ts}.jpg (ts=YYYYMMDD_HHMMSS)
    pattern = os.path.join(SNAPSHOT_DIR, f"*_{today_str}_*.jpg")
    
    files = sorted(glob.glob(pattern))
    
    if not files:
        logger.info("今日のカメラ画像はありません")
        return []

    logger.info(f"今日の画像数: {len(files)}枚")

    # 枚数が多い場合は間引く (均等にサンプリング)
    if len(files) > limit:
        step = len(files) / limit
        selected_files = [files[int(i * step)] for i in range(limit)]
        logger.info(f"-> {len(selected_files)}枚にサンプリングしました")
        return selected_files
    
    return files

if __name__ == "__main__":
    # テスト実行
    imgs = get_todays_highlight_images()
    print(f"取得した画像: {len(imgs)}枚")
    for img in imgs:
        print(f"- {os.path.basename(img)}")