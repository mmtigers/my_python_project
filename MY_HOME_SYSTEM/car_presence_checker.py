import cv2
import numpy as np
import os
import time
import subprocess
import sqlite3
from datetime import datetime
import traceback

# プロジェクト内モジュールのインポート
import config
import common

# ==========================================
# 1. 設定・定数定義
# ==========================================
# ログ設定
logger = common.setup_logging("car_checker")

# 判定設定
TARGET_CAMERA_ID = "VIGI_C540_Parking"
CENTER_CROP_RATIO = 0.3      # 中央30%を判定エリアとする
RTSP_PORT = 554              # VIGIカメラのRTSP標準ポート

# --- 昼間用設定 (色判定) ---
BLUE_PIXEL_THRESHOLD = 0.1   # 青色率10%以上で「車あり」
BLUE_LOWER = np.array([90, 50, 50])    # 青色の下限 (H, S, V)
BLUE_UPPER = np.array([130, 255, 255]) # 青色の上限

# --- 夜間用設定 (反射＆エッジ判定) ---
# 1. 反射検知 (ナンバープレート等)
BRIGHTNESS_VAL_THRESH = 230  # この輝度(0-255)以上を「反射光」とみなす
BRIGHTNESS_RATIO_THRESH = 0.005 # 画面の0.5%以上が光っていれば車あり

# 2. エッジ検知 (車体の複雑さ)
CANNY_THRESH_1 = 50
CANNY_THRESH_2 = 150
EDGE_RATIO_THRESH = 0.05    # エッジ密度が5%以上なら車あり (地面は平坦)

# ファイルパス設定
TEMP_IMAGE_PATH_TEMPLATE = "/tmp/car_check_{}.jpg"

# ==========================================
# 2. ヘルパー関数 (画像処理・取得)
# ==========================================

def capture_snapshot(cam_conf: dict) -> str:
    """RTSP経由でカメラからスナップショットを取得する"""
    tmp_path = TEMP_IMAGE_PATH_TEMPLATE.format(cam_conf['id'])
    rtsp_url = f"rtsp://{cam_conf['user']}:{cam_conf['pass']}@{cam_conf['ip']}:{RTSP_PORT}/stream1"
    
    cmd = [
        "ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", rtsp_url,
        "-frames:v", "1", "-q:v", "2", tmp_path
    ]
    
    logger.info(f"📷 画像取得開始: {cam_conf['name']} (IP: {cam_conf['ip']})")
    try:
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=True
        )
        if os.path.exists(tmp_path):
            return tmp_path
    except Exception as e:
        logger.error(f"❌ 画像取得エラー: {e}")
    return None

def is_night_mode(hsv_img: np.ndarray) -> bool:
    """画像の彩度平均が低い場合は夜間(白黒モード)とみなす"""
    saturation = hsv_img[:, :, 1]
    mean_sat = np.mean(saturation)
    # 彩度平均が15未満ならほぼモノクロ
    return mean_sat < 15

def analyze_night_mode(crop_img_bgr):
    """
    夜間用のハイブリッド判定 (反射検知 + エッジ検知)
    Returns: (is_present, details_str, score)
    """
    gray = cv2.cvtColor(crop_img_bgr, cv2.COLOR_BGR2GRAY)
    
    # 1. 反射検知 (ナンバープレートなど)
    _, bright_mask = cv2.threshold(gray, BRIGHTNESS_VAL_THRESH, 255, cv2.THRESH_BINARY)
    bright_ratio = np.count_nonzero(bright_mask) / bright_mask.size
    
    # 2. エッジ検知 (ボディの輪郭)
    edges = cv2.Canny(gray, CANNY_THRESH_1, CANNY_THRESH_2)
    edge_ratio = np.count_nonzero(edges) / edges.size
    
    logger.info(f"🌃 夜間解析: 反射率={bright_ratio:.2%} (閾値{BRIGHTNESS_RATIO_THRESH:.1%}), エッジ率={edge_ratio:.2%} (閾値{EDGE_RATIO_THRESH:.1%})")

    # 判定ロジック
    if bright_ratio >= BRIGHTNESS_RATIO_THRESH:
        return True, "Night:Reflection", bright_ratio
    elif edge_ratio >= EDGE_RATIO_THRESH:
        return True, "Night:Edge", edge_ratio
    else:
        return False, "Night:Clear", max(bright_ratio, edge_ratio)

def analyze_car_presence(image_path: str):
    """
    画像から車の有無を判定する (昼夜自動切替)
    Returns: (is_present, details_str, score)
    """
    try:
        img = cv2.imread(image_path)
        if img is None: return None, "Error", 0.0

        h, w, _ = img.shape
        # 中央切り出し
        cy, cx = h // 2, w // 2
        dy, dx = int(h * CENTER_CROP_RATIO / 2), int(w * CENTER_CROP_RATIO / 2)
        crop_img = img[cy-dy:cy+dy, cx-dx:cx+dx]

        hsv = cv2.cvtColor(crop_img, cv2.COLOR_BGR2HSV)

        # 夜間判定分岐
        if is_night_mode(hsv):
            return analyze_night_mode(crop_img)
        
        # --- 昼間: 青色検知 ---
        mask = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)
        blue_ratio = np.count_nonzero(mask) / mask.size
        
        logger.info(f"☀️ 昼間解析: 青色率={blue_ratio:.2%} (閾値{BLUE_PIXEL_THRESHOLD:.0%})")
        
        if blue_ratio >= BLUE_PIXEL_THRESHOLD:
            return True, "Day:BlueColor", blue_ratio
        else:
            return False, "Day:Clear", blue_ratio

    except Exception as e:
        logger.error(f"❌ 画像解析エラー: {e}")
        return None, "Error", 0.0

# ==========================================
# 3. データベース・ファイル操作
# ==========================================

def get_last_status_from_db():
    """DBから直近の状態を取得"""
    try:
        with sqlite3.connect(config.SQLITE_DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(f"SELECT action, timestamp FROM {config.SQLITE_TABLE_CAR} ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row: return row["action"], row["timestamp"]
    except Exception as e:
        logger.error(f"❌ DB読み込みエラー: {e}")
    return "UNKNOWN", ""

def save_evidence_image(src_path: str, action: str, details: str) -> str:
    """証拠画像を保存"""
    # ファイル名に詳細(Day/Night)を含める
    safe_details = details.replace(":", "-")
    filename = f"car_{action}_{safe_details}_{int(time.time())}.jpg"
    dest_path = os.path.join(config.ASSETS_DIR, "security_logs", filename)
    
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        os.rename(src_path, dest_path)
        return f"security_logs/{filename}"
    except Exception as e:
        logger.error(f"❌ 画像保存エラー: {e}")
        return None

def record_result_to_db(action: str, details: str, score: float, image_path: str, has_status_changed: bool):
    """DBに保存 (イベントログ & 防犯ログ)"""
    now_iso = common.get_now_iso()
    try:
        with sqlite3.connect(config.SQLITE_DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            
            # 1. イベントログ (変化時)
            if has_status_changed:
                cursor.execute(f"""
                    INSERT INTO {config.SQLITE_TABLE_CAR} (action, rule_name, timestamp)
                    VALUES (?, ?, ?)
                """, (action, details, now_iso))
                logger.info(f"📝 イベント記録: {action} ({details})")
            
            # 2. 防犯ログ (画像付き)
            evidence_path = save_evidence_image(image_path, action, details)
            if evidence_path:
                info_text = f"{action} (Score:{score:.1%}, {details})"
                cursor.execute("""
                    INSERT INTO security_logs (timestamp, device_name, classification, image_path, recorded_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (now_iso, "ParkingCamera", info_text, evidence_path, now_iso))
                
            conn.commit()

    except Exception as e:
        logger.error(f"❌ DB書き込みエラー: {e}")

# ==========================================
# 4. 通知ロジック
# ==========================================

def send_user_notification(action: str, score: float, details: str):
    """LINE/Discordへ通知"""
    # 判定理由によってメッセージを微調整
    reason_ja = "色判定"
    if "Night" in details:
        if "Reflection" in details: reason_ja = "反射検知"
        elif "Edge" in details: reason_ja = "形状検知"
        else: reason_ja = "夜間モード"

    if action == "LEAVE":
        message = (
            "🚗 車でお出かけしたみたいだよ。\n"
            "いってらっしゃい！気をつけてね👋\n"
            f"(判定: {reason_ja}, 確度: {score:.0%})"
        )
    elif action == "RETURN":
        message = (
            "🏠 おかえりなさい！\n"
            "車が戻ってきたよ🍵 お疲れさま。\n"
            f"(判定: {reason_ja}, 確度: {score:.0%})"
        )
    else:
        return

    common.send_push(
        config.LINE_USER_ID, 
        [{"type": "text", "text": message}], 
        target="all"
    )
    logger.info(f"📨 通知送信完了: {action}")

# ==========================================
# 5. メイン処理
# ==========================================

def main():
    logger.info("🚀 車チェック開始 (Hybrid版)")
    try:
        target_cam = next((c for c in config.CAMERAS if c["id"] == TARGET_CAMERA_ID), None)
        if not target_cam:
            raise ValueError(f"カメラID {TARGET_CAMERA_ID} が見つかりません。")

        # 1. 画像取得
        img_path = capture_snapshot(target_cam)
        if not img_path: return

        # 2. 解析 (昼夜ハイブリッド)
        is_present, details, score = analyze_car_presence(img_path)
        
        # エラー時は終了
        if is_present is None:
            if os.path.exists(img_path): os.remove(img_path)
            return

        current_action = "RETURN" if is_present else "LEAVE"
        
        # 3. 状態比較
        last_action, last_ts = get_last_status_from_db()
        has_status_changed = (last_action == "UNKNOWN" or last_action != current_action)
        
        # 定期記録判定 (1時間経過)
        should_save_log = has_status_changed
        if not has_status_changed and last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                if (datetime.now() - last_dt).total_seconds() > 3600:
                    should_save_log = True
                    logger.info("⏰ 定期記録タイミング")
            except: pass

        # 4. 記録と通知
        if should_save_log:
            record_result_to_db(current_action, details, score, img_path, has_status_changed)
            if has_status_changed:
                send_user_notification(current_action, score, details)
        else:
            logger.info(f"✅ 変化なし: {current_action} ({details}) - 記録スキップ")
            if os.path.exists(img_path): os.remove(img_path)

    except Exception as e:
        logger.error(f"🔥 エラー発生: {e}\n{traceback.format_exc()}")
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"⚠️ 車検知エラー: {e}"}], target="discord")

if __name__ == "__main__":
    main()