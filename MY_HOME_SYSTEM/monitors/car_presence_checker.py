# MY_HOME_SYSTEM/monitors/car_presence_checker.py
import cv2
import numpy as np
import os
import shutil
import sys
import time
import traceback
from datetime import datetime
from typing import Tuple, Optional, Dict, Any, List

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging
from core.database import get_db_cursor, save_log_generic
from core.utils import get_now_iso
from services.notification_service import send_push

# ==========================================
# 1. 設定・定数定義
# ==========================================
logger = setup_logging("car_checker")

TARGET_CAMERA_ID: str = "VIGI_C540_Parking"
CENTER_CROP_RATIO: float = 0.3
RTSP_PORT: int = 554

# 昼間用 (色判定)
BLUE_PIXEL_THRESHOLD: float = 0.1
BLUE_LOWER: np.ndarray = np.array([90, 50, 50])
BLUE_UPPER: np.ndarray = np.array([130, 255, 255])

# 夜間用 (輝度判定)
NIGHT_BRIGHTNESS_THRESHOLD: float = 40.0
NIGHT_START_HOUR: int = 18
NIGHT_END_HOUR: int = 6

def get_camera_frame() -> Optional[np.ndarray]:
    """RTSP経由でカメラの最新フレームを取得する。"""
    rtsp_url: str = f"rtsp://{config.CAMERA_USER}:{config.CAMERA_PASS}@{config.CAMERA_IP}:{RTSP_PORT}/stream1"
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        logger.error("❌ RTSPストリームを開けませんでした。")
        return None
    
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None

def judge_car_presence(img: np.ndarray) -> Tuple[str, str, float]:
    """
    画像から車の有無を判定するロジック。
    Returns: (判定結果"PRESENT"/"ABSENT", 理由詳細, スコア)
    """
    h, w = img.shape[:2]
    ch, cw = int(h * CENTER_CROP_RATIO), int(w * CENTER_CROP_RATIO)
    cy, cx = h // 2, w // 2
    crop: np.ndarray = img[cy - ch:cy + ch, cx - cw:cx + cw]

    now_hour: int = datetime.now().hour
    is_night: bool = now_hour >= NIGHT_START_HOUR or now_hour < NIGHT_END_HOUR

    if is_night:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        brightness: float = float(np.mean(gray))
        res = "PRESENT" if brightness > NIGHT_BRIGHTNESS_THRESHOLD else "ABSENT"
        logger.info(f"🌙 Night Mode: Brightness={brightness:.1f} (Threshold: {NIGHT_BRIGHTNESS_THRESHOLD})")
        return res, f"NightMode({brightness:.1f})", brightness
    else:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)
        blue_count: int = np.count_nonzero(mask)
        blue_ratio: float = float(blue_count / mask.size)
        res = "PRESENT" if blue_ratio > BLUE_PIXEL_THRESHOLD else "ABSENT"
        logger.info(f"☀️ Day Mode: BlueRatio={blue_ratio:.2%} ({blue_count}/{mask.size})")
        return res, f"DayMode({blue_ratio:.1%})", blue_ratio

def record_result_to_db(action: str, details: str, score: float, img_path: str, is_changed: bool) -> bool:
    """判定結果をDBに記録し、変化時は画像を永続保存する。"""
    timestamp: str = get_now_iso()
    cols: List[str] = ["timestamp", "action", "rule_name", "score"]
    vals: Tuple[Any, ...] = (timestamp, action, f"{details} (Changed:{is_changed})", score)
    
    if is_changed:
        save_dir: str = os.path.join(config.ASSETS_DIR, "car_history")
        os.makedirs(save_dir, exist_ok=True)
        permanent_path = os.path.join(save_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{action}.jpg")
        try:
            shutil.move(img_path, permanent_path)
            logger.info(f"📸 Image moved to history: {os.path.basename(permanent_path)}")
        except Exception as e:
            logger.warning(f"⚠️ Image move failed: {e}")
    
    return save_log_generic(config.SQLITE_TABLE_CAR, cols, vals)

def main() -> None:
    """メイン監視プロセス。"""
    tmp_img_path = "/tmp/car_check_latest.jpg"
    try:
        # 1. 映像取得
        frame = get_camera_frame()
        if frame is None: return

        # 2. AI判定
        current_action, details, score = judge_car_presence(frame)
        cv2.imwrite(tmp_img_path, frame)

        # 3. 前回状態との比較 (DBから取得)
        last_action: str = "UNKNOWN"
        last_ts: Optional[str] = None
        with get_db_cursor() as cur:
            if cur:
                cur.execute(f"SELECT action, timestamp FROM {config.SQLITE_TABLE_CAR} ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                if row:
                    last_action = row["action"] if isinstance(row, dict) else row[0]
                    last_ts = row["timestamp"] if isinstance(row, dict) else row[1]

        has_status_changed: bool = (last_action == "UNKNOWN" or last_action != current_action)
        
        # 定期記録判定 (1時間経過)
        should_save: bool = has_status_changed
        if not has_status_changed and last_ts:
            try:
                if (datetime.now() - datetime.fromisoformat(last_ts)).total_seconds() > 3600:
                    should_save = True
            except Exception: pass

        # 4. 実行
        if should_save:
            record_result_to_db(current_action, details, score, tmp_img_path, has_status_changed)
            if has_status_changed:
                status_msg: str = "🚗 車が戻りました" if current_action == "PRESENT" else "💨 車が出かけました"
                send_push(config.LINE_USER_ID or "", [{"type": "text", "text": f"【車庫通知】\n{status_msg}\n判定: {details}"}], target="discord")
                logger.info(f"📢 Status change notification sent: {current_action}")
        else:
            logger.info(f"✅ No change: {current_action} ({details})")
        
        if os.path.exists(tmp_img_path): os.remove(tmp_img_path)

    except Exception as e:
        # 【重要】既存の「エラー発生時のLINE通知」機能を維持
        err_detail = f"🔥 Car Presence Checker Error: {e}\n{traceback.format_exc()}"
        logger.error(err_detail)
        send_push(config.LINE_USER_ID or "", [{"type": "text", "text": f"⚠️ 車庫監視スクリプトでエラーが発生しました。\n{e}"}], target="discord", channel="error")

if __name__ == "__main__":
    main()