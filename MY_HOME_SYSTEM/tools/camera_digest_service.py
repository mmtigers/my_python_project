# MY_HOME_SYSTEM/tools/camera_digest_service.py
import os
import glob
from datetime import datetime
from typing import List

from PIL import Image, UnidentifiedImageError

import config
# 【修正点】正しい関数名 setup_logging をインポート
from core.logger import setup_logging

# 【修正点】ロガーの初期化
logger = setup_logging(__name__)

# 設定の外部化
SNAPSHOT_DIR = os.path.join(config.ASSETS_DIR, "snapshots")


def _validate_image(file_path: str) -> bool:
    """画像ファイルが正常に開けるか検証する。
    
    Args:
        file_path (str): 検証対象のファイルパス。

    Returns:
        bool: 正常な画像ファイルであればTrue、破損や読込不可の場合はFalse。
    """
    if not os.path.exists(file_path):
        logger.warning(f"File not found during validation: {file_path}")
        return False

    try:
        # 画像としての整合性をチェック
        with Image.open(file_path) as img:
            img.verify()
        return True
    except (UnidentifiedImageError, OSError) as e:
        logger.warning(f"Corrupted image detected: {file_path}, Error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error validating image {file_path}: {e}")
        return False


def get_todays_highlight_images(limit: int = 10) -> List[str]:
    """今日のスナップショットから、AI解析用に有効な画像を抽出・サンプリングする。

    Args:
        limit (int): 抽出する画像の最大枚数。デフォルトは10。

    Returns:
        List[str]: 有効な画像ファイルパスのリスト。
    """
    today_str = datetime.now().strftime('%Y%m%d')
    # パターン: snapshot_{id}_{YYYYMMDD}_{HHMMSS}.jpg
    pattern = os.path.join(SNAPSHOT_DIR, f"*_{today_str}_*.jpg")
    
    logger.debug(f"Searching images with pattern: {pattern}")
    
    # ファイル取得とソート
    files: List[str] = sorted(glob.glob(pattern))
    
    if not files:
        logger.info("No images found for today.")
        return []

    total_files = len(files)
    logger.info(f"Found {total_files} images for today.")

    # サンプリングロジック
    selected_files: List[str] = []
    
    if total_files > limit:
        # 均等に間引く
        step = total_files / limit
        sampled_paths = [files[int(i * step)] for i in range(limit)]
        logger.info(f"Sampled {len(sampled_paths)} images from {total_files} files.")
    else:
        sampled_paths = files

    # 有効性チェック
    for path in sampled_paths:
        if _validate_image(path):
            selected_files.append(path)
        else:
            pass # 破損ファイルはスキップ

    logger.info(f"Returned {len(selected_files)} valid highlight images.")
    return selected_files


if __name__ == "__main__":
    # テスト実行
    try:
        logger.info("Starting manual execution of camera_digest_service...")
        imgs = get_todays_highlight_images()
        
        logger.info(f"Result: {len(imgs)} valid images found.")
        for img in imgs:
            # 運用時はDEBUGレベルだが、手動実行時の確認用にあえて出力したい場合はprint併用も可
            # ここではloggerに従い出力
            logger.debug(f"- {os.path.basename(img)}")
            
    except Exception as e:
        logger.critical(f"Critical error during script execution: {e}", exc_info=True)