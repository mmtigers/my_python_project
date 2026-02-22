# MY_HOME_SYSTEM/monitors/car_presence_checker.py
import cv2
import numpy as np
import os
import shutil
import sys
import traceback
import time  # Added for sleep
from datetime import datetime
from typing import Tuple, Optional, List, Any

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging
from core.database import get_db_cursor, save_log_generic
from services.notification_service import send_push
from core.utils import get_now_iso, with_exponential_backoff

# ==========================================
# 1. 設定・定数定義
# ==========================================
logger = setup_logging("car_checker")

# 定数定義
# Note: config.CAMERAS内のportはONVIF/HTTP用(2020)の可能性があるため、
# RTSPは標準の554をデフォルトとしつつ、必要に応じて環境変数等で変更可能な設計とする。
RTSP_PORT: int = 554
MAX_RETRIES: int = 3
RETRY_INTERVAL: int = 5  # seconds

# 判定エリア設定
CENTER_CROP_RATIO: float = 0.3

# 昼間用 (色判定: 青い車を検知)
BLUE_PIXEL_THRESHOLD: float = 0.1
BLUE_LOWER: np.ndarray = np.array([90, 50, 50])
BLUE_UPPER: np.ndarray = np.array([130, 255, 255])

# 夜間用 (輝度判定: 暗い車庫を検知)
NIGHT_BRIGHTNESS_THRESHOLD: float = 40.0
NIGHT_START_HOUR: int = 18
NIGHT_END_HOUR: int = 6

# 状態定数 (SSOT)
STATE_PRESENT = "PRESENT"
STATE_ABSENT = "ABSENT"


@with_exponential_backoff(base_delay=5, max_delay=300, alert_threshold=5)
def get_camera_frame() -> np.ndarray:
    """
    RTSP経由でカメラの最新フレームを取得する。
    例外発生時は with_exponential_backoff デコレータにより無限リトライが行われる。
    
    Returns:
        np.ndarray: 取得に成功した画像フレーム
    Raises:
        ValueError: 設定情報が不足している場合
        ConnectionError: RTSPストリームが開けなかった場合
        IOError: フレームの読み出しに失敗した場合
    """
    if not config.CAMERA_IP or not config.CAMERA_USER:
        raise ValueError("Camera config is missing (IP or User not found).")

    rtsp_url: str = f"rtsp://{config.CAMERA_USER}:{config.CAMERA_PASS}@{config.CAMERA_IP}:{RTSP_PORT}/stream1"
    
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        raise ConnectionError(f"RTSP Connection failed to open: {rtsp_url}")
    
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.read()  # バッファクリアのための空読み
        ret, frame = cap.read()
        
        if ret and frame is not None:
            return frame
        else:
            raise IOError("RTSP Stream opened but failed to read frame.")
    finally:
        cap.release()

def get_camera_frame(retries: int = MAX_RETRIES, interval: int = RETRY_INTERVAL) -> Optional[np.ndarray]:
    """
    RTSP経由でカメラの最新フレームを取得する。
    接続失敗時は指定回数リトライを行う。
    """
    # config不備のガード
    if not config.CAMERA_IP or not config.CAMERA_USER:
        logger.error("❌ Camera config is missing (IP or User not found).")
        return None

    rtsp_url: str = f"rtsp://{config.CAMERA_USER}:{config.CAMERA_PASS}@{config.CAMERA_IP}:{RTSP_PORT}/stream1"
    
    for attempt in range(1, retries + 1):
        cap = None
        try:
            cap = cv2.VideoCapture(rtsp_url)
            if not cap.isOpened():
                logger.warning(f"⚠️ RTSP Connection failed (Attempt {attempt}/{retries}). Retrying in {interval}s...")
                time.sleep(interval)
                continue
            
            # バッファ対策: 最新フレームを取得
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
            # 念のため数フレーム空読みして安定させる（オプション）
            if attempt > 1:
                cap.read() 
                
            ret, frame = cap.read()
            
            if ret and frame is not None:
                if attempt > 1:
                    logger.info(f"✅ RTSP Connection recovered on attempt {attempt}.")
                return frame
            else:
                logger.warning(f"⚠️ RTSP Stream opened but failed to read frame (Attempt {attempt}/{retries}).")
                
        except Exception as e:
            logger.warning(f"⚠️ Unexpected error during RTSP connection: {e}")
        finally:
            if cap:
                cap.release()
        
        # 次の試行まで待機（最終回以外）
        if attempt < retries:
            time.sleep(interval)

    logger.error(f"❌ RTSP connection failed after {retries} attempts. Giving up.")
    return None

def judge_car_presence(img: np.ndarray) -> Tuple[str, str, float]:
    """
    画像から車の有無を判定するロジック。
    Returns: (判定結果 STATE_*, 理由詳細, スコア)
    """
    if img is None:
        return "UNKNOWN", "Invalid Image", 0.0

    h, w = img.shape[:2]
    ch, cw = int(h * CENTER_CROP_RATIO), int(w * CENTER_CROP_RATIO)
    cy, cx = h // 2, w // 2
    crop: np.ndarray = img[cy - ch:cy + ch, cx - cw:cx + cw]

    now_hour: int = datetime.now().hour
    is_night: bool = now_hour >= NIGHT_START_HOUR or now_hour < NIGHT_END_HOUR

    if is_night:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        brightness: float = float(np.mean(gray))
        res = STATE_PRESENT if brightness > NIGHT_BRIGHTNESS_THRESHOLD else STATE_ABSENT
        return res, f"NightMode({brightness:.1f})", brightness
    else:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)
        blue_count: int = np.count_nonzero(mask)
        blue_ratio: float = float(blue_count / mask.size)
        res = STATE_PRESENT if blue_ratio > BLUE_PIXEL_THRESHOLD else STATE_ABSENT
        return res, f"DayMode({blue_ratio:.1%})", blue_ratio

def record_result_to_db(action: str, details: str, score: float, img_path: str, is_changed: bool) -> bool:
    """判定結果をDBに記録し、変化時は画像を永続保存する。"""
    timestamp: str = get_now_iso()
    cols: List[str] = ["timestamp", "action", "rule_name", "score"]
    vals: Tuple[Any, ...] = (timestamp, action, f"{details}", score)
    
    if is_changed and os.path.exists(img_path):
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
    tmp_img_path: str = "/tmp/car_check_latest.jpg"
    
    try:
        # 1. 映像取得 (Retry機能付き)
        frame: Optional[np.ndarray] = get_camera_frame()
        if frame is None:
            return 

        # 2. AI判定
        current_action: str
        details: str
        score: float
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
        
        # 状態変化判定
        if last_action == "UNKNOWN":
            logger.info(f"🆕 Initial state detected: {current_action}. Saving without notification.")
            record_result_to_db(current_action, details, score, tmp_img_path, is_changed=True)
            return

        has_status_changed: bool = (last_action != current_action)
        
        # 定期記録判定 (1時間経過していたら、変化がなくても記録する)
        should_save: bool = has_status_changed
        if not has_status_changed and last_ts:
            try:
                last_dt: datetime = datetime.fromisoformat(last_ts)
                now: datetime = datetime.now()
                if last_dt.tzinfo is not None and now.tzinfo is None:
                    now = now.astimezone()
                
                if (now - last_dt).total_seconds() > 3600:
                    should_save = True
            except Exception as e:
                logger.warning(f"Time comparison failed: {e}")
                should_save = True

        # 4. 保存と通知
        if should_save:
            success: bool = record_result_to_db(current_action, details, score, tmp_img_path, has_status_changed)
            
            if success and has_status_changed:
                status_msg: str = "🚗 車が戻りました" if current_action == STATE_PRESENT else "💨 車が出かけました"
                send_push(
                    config.LINE_USER_ID or "", 
                    [{"type": "text", "text": f"【車庫通知】\n{status_msg}\n判定: {details}"}], 
                    target="discord"
                )
                logger.info(f"📢 Status change notification sent: {current_action}")
            elif not success:
                 logger.error("❌ Failed to save record to DB.")
        else:
            # 変更箇所：状態に変化がない場合は DEBUG へ降格
            logger.debug(f"✅ No change: {current_action} ({details})")
        
        # クリーンアップ
        if os.path.exists(tmp_img_path): os.remove(tmp_img_path)

    except Exception as e:
        err_detail: str = f"🔥 Car Presence Checker Error: {e}\n{traceback.format_exc()}"
        logger.error(err_detail)
        # エラー通知
        send_push(config.LINE_USER_ID or "", [{"type": "text", "text": f"⚠️ 車庫監視スクリプトでエラーが発生しました。\n{e}"}], target="discord", channel="error")

if __name__ == "__main__":
    main()