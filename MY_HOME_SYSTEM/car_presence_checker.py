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
BLUE_PIXEL_THRESHOLD = 0.1   # 青色率10%以上で「車あり」とみなす
RTSP_PORT = 554              # VIGIカメラのRTSP標準ポート

# 色閾値 (HSV形式) - 車の青色に合わせて調整
BLUE_LOWER = np.array([90, 50, 50])
BLUE_UPPER = np.array([130, 255, 255])

# ファイルパス設定
TEMP_IMAGE_PATH_TEMPLATE = "/tmp/car_check_{}.jpg"

# ==========================================
# 2. ヘルパー関数 (画像処理・取得)
# ==========================================

def capture_snapshot(cam_conf: dict) -> str:
    """
    RTSP経由でカメラからスナップショットを取得する。
    機密情報(パスワード)はログに出さないよう配慮。
    """
    tmp_path = TEMP_IMAGE_PATH_TEMPLATE.format(cam_conf['id'])
    
    # URL生成 (configのポートではなくRTSP標準の554を強制使用)
    # user:pass が含まれるため、この変数はログ出力禁止
    rtsp_url = f"rtsp://{cam_conf['user']}:{cam_conf['pass']}@{cam_conf['ip']}:{RTSP_PORT}/stream1"
    
    cmd = [
        "ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", rtsp_url,
        "-frames:v", "1", "-q:v", "2", tmp_path
    ]
    
    logger.info(f"📷 画像取得開始: {cam_conf['name']} (IP: {cam_conf['ip']})")
    
    try:
        # ffmpeg実行 (タイムアウト20秒)
        subprocess.run(
            cmd, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL, 
            timeout=20, 
            check=True
        )
        if os.path.exists(tmp_path):
            return tmp_path
    except subprocess.TimeoutExpired:
        logger.error("❌ 画像取得タイムアウト")
    except subprocess.CalledProcessError:
        logger.error("❌ 画像取得エラー (ffmpeg)")
    except Exception as e:
        logger.error(f"❌ 予期せぬエラー: {e}")
        
    return None

def is_night_mode(hsv_img: np.ndarray) -> bool:
    """画像の彩度平均が極端に低い場合は夜間(白黒モード)とみなす"""
    saturation = hsv_img[:, :, 1]
    mean_sat = np.mean(saturation)
    # 彩度平均が10未満ならほぼモノクロ
    return mean_sat < 10

def analyze_car_presence(image_path: str):
    """
    画像の中央が青いかどうかを判定する。
    Returns: (is_present: bool|None, blue_ratio: float)
    """
    try:
        img = cv2.imread(image_path)
        if img is None:
            return None, 0.0

        h, w, _ = img.shape
        
        # 中央部分を切り出し (Crop)
        cy, cx = h // 2, w // 2
        dy, dx = int(h * CENTER_CROP_RATIO / 2), int(w * CENTER_CROP_RATIO / 2)
        crop_img = img[cy-dy:cy+dy, cx-dx:cx+dx]

        # HSV変換
        hsv = cv2.cvtColor(crop_img, cv2.COLOR_BGR2HSV)

        # 夜間判定
        if is_night_mode(hsv):
            logger.info("🌃 夜間(モノクロ)モードのため判定をスキップします")
            return None, 0.0

        # 青色マスク作成
        mask = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)
        
        # 青いピクセルの割合計算
        blue_ratio = np.count_nonzero(mask) / mask.size
        
        logger.info(f"🎨 青色率: {blue_ratio:.2%} (閾値: {BLUE_PIXEL_THRESHOLD:.0%})")
        
        is_present = (blue_ratio >= BLUE_PIXEL_THRESHOLD)
        return is_present, blue_ratio

    except Exception as e:
        logger.error(f"❌ 画像解析エラー: {e}")
        return None, 0.0

# ==========================================
# 3. データベース操作
# ==========================================

def get_last_status_from_db():
    """DBから直近の車の状態と時刻を取得"""
    try:
        with sqlite3.connect(config.SQLITE_DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(f"SELECT action, timestamp FROM {config.SQLITE_TABLE_CAR} ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                return row["action"], row["timestamp"]
    except Exception as e:
        logger.error(f"❌ DB読み込みエラー: {e}")
    return "UNKNOWN", ""

def save_evidence_image(src_path: str, action: str, ratio: float) -> str:
    """証拠画像をassetsフォルダに保存し、相対パスを返す"""
    filename = f"car_{action}_{int(time.time())}_{int(ratio*100)}.jpg"
    dest_path = os.path.join(config.ASSETS_DIR, "security_logs", filename)
    
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        # 画像をコピーではなく移動して一時ファイルを消す
        os.rename(src_path, dest_path)
        # DB保存用に assets/ 以下のパスを返す (dashboard.pyの仕様に合わせるなら security_logs/... )
        return f"security_logs/{filename}"
    except Exception as e:
        logger.error(f"❌ 画像保存エラー: {e}")
        return None

def record_result_to_db(action: str, blue_ratio: float, image_path: str, has_status_changed: bool):
    """
    判定結果をDBに保存する。
    1. 状態変化時 -> car_records (イベントログ)
    2. 証拠画像 -> security_logs (ダッシュボード表示用)
    """
    now_iso = common.get_now_iso()
    
    try:
        with sqlite3.connect(config.SQLITE_DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            
            # 1. 状態変化があればイベント記録
            if has_status_changed:
                cursor.execute(f"""
                    INSERT INTO {config.SQLITE_TABLE_CAR} (action, rule_name, timestamp)
                    VALUES (?, ?, ?)
                """, (action, "ColorCheck", now_iso))
                logger.info(f"📝 イベント記録: {action}")
            
            # 2. 証拠画像を保存 (security_logs)
            evidence_path = save_evidence_image(image_path, action, blue_ratio)
            if evidence_path:
                details = f"BlueRatio:{blue_ratio:.1%}"
                cursor.execute("""
                    INSERT INTO security_logs (timestamp, device_name, classification, image_path, recorded_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (now_iso, "ParkingCamera", f"{action} ({details})", evidence_path, now_iso))
                logger.info(f"📸 証拠画像保存: {evidence_path}")
                
            conn.commit()

    except Exception as e:
        logger.error(f"❌ DB書き込みエラー: {e}")
        raise e # 上位でキャッチさせる

# ==========================================
# 4. 通知ロジック (主婦向け表現)
# ==========================================

def send_user_notification(action: str, blue_ratio: float):
    """
    ユーザー（奥様）向けの優しいメッセージを作成して送信する。
    """
    if action == "LEAVE":
        message = (
            "🚗 車でお出かけしたみたいだよ。\n"
            "いってらっしゃい！気をつけてね👋\n"
            f"(判定確度: {blue_ratio:.0%})"
        )
    elif action == "RETURN":
        message = (
            "🏠 おかえりなさい！\n"
            "車が戻ってきたよ🍵 お疲れさま。\n"
            f"(判定確度: {blue_ratio:.0%})"
        )
    else:
        return

    # LINEとDiscord両方に送る (configで制御可能だが、重要な家族イベントなので両方が望ましい)
    common.send_push(
        config.LINE_USER_ID, 
        [{"type": "text", "text": message}], 
        target="all" # LINE & Discord
    )
    logger.info(f"📨 通知送信完了: {action}")

def send_error_notification(error_msg: str):
    """システムエラーをDiscordにのみ通知する"""
    try:
        common.send_push(
            config.LINE_USER_ID, # ダミーID (Discordターゲットなら無視される場合が多いが念のため)
            [{"type": "text", "text": f"⚠️ [CarChecker] エラー発生:\n{error_msg}"}],
            target="discord"
        )
    except Exception:
        pass # エラー通知のエラーは握りつぶす

# ==========================================
# 5. メイン処理
# ==========================================

def main():
    logger.info("🚀 車の有無チェック開始 (Start)")
    
    try:
        # カメラ設定の検索
        target_cam = next((c for c in config.CAMERAS if c["id"] == TARGET_CAMERA_ID), None)
        if not target_cam:
            raise ValueError(f"カメラID {TARGET_CAMERA_ID} が config.py に見つかりません。")

        # 1. 画像取得
        img_path = capture_snapshot(target_cam)
        if not img_path:
            logger.warning("画像が取得できなかったため、処理を中断します。")
            return

        # 2. 画像判定
        is_present, blue_ratio = analyze_car_presence(img_path)
        
        # 判定不能（夜間など）の場合は、一時ファイルを削除して終了
        if is_present is None:
            if os.path.exists(img_path):
                os.remove(img_path)
            return

        # 現在の状態決定
        current_action = "RETURN" if is_present else "LEAVE"
        
        # 3. 前回の状態と比較
        last_action, last_ts = get_last_status_from_db()
        
        # 状態変化フラグ
        has_status_changed = (last_action == "UNKNOWN" or last_action != current_action)
        
        # 定期記録フラグ (変化なしでも1時間に1回は証拠を残す)
        should_save_log = has_status_changed
        if not has_status_changed and last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                # 3600秒 = 1時間
                if (datetime.now() - last_dt).total_seconds() > 3600:
                    should_save_log = True
                    logger.info("⏰ 定期記録タイミングです (1時間経過)")
            except Exception:
                pass # 日付パースエラー等は無視

        # 4. DB記録と通知
        if should_save_log:
            record_result_to_db(current_action, blue_ratio, img_path, has_status_changed)
            
            # 状態が変わった時だけユーザー通知を送る
            if has_status_changed:
                send_user_notification(current_action, blue_ratio)
        else:
            logger.info(f"✅ 変化なし: {current_action} (率:{blue_ratio:.1%}) - DB記録スキップ")
            # 記録しない場合は画像を削除
            if os.path.exists(img_path):
                os.remove(img_path)

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(f"🔥 クリティカルエラー発生: {e}")
        send_error_notification(error_msg)
    
    logger.info("🏁 車の有無チェック終了 (End)")

if __name__ == "__main__":
    main()