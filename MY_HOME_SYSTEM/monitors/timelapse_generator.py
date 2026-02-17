# MY_HOME_SYSTEM/monitors/timelapse_generator.py
import os
import glob
import time
import datetime
import subprocess
import requests
import argparse
import math
from typing import List

import config
from core.database import get_db_cursor
from core.logger import setup_logging
from services.notification_service import send_push

logger = setup_logging("timelapse_generator")

# 対象とするカメラのリスト（config.CAMERAS から取得するか、固定で指定）
TARGET_CAMERAS = [cam["name"] for cam in config.CAMERAS] if config.CAMERAS else ["garden", "parking"]

def get_event_times(camera_name: str, start_time: str, end_time: str) -> List[datetime.datetime]:
    """DBから指定時間帯のイベント検知時刻を取得する"""
    event_times = []
    
    # 修正: カラム名を 'name' から 'device_name' に変更
    query = """
        SELECT timestamp FROM device_records 
        WHERE device_name = ? AND timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp ASC
    """
    
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(query, (camera_name, start_time, end_time))
            rows = cur.fetchall()
            for row in rows:
                try:
                    # ISOフォーマット等の文字列をパース
                    dt = datetime.datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                    event_times.append(dt)
                except ValueError:
                    pass
    except Exception as e:
        logger.error(f"イベント取得エラー ({camera_name}): {e}")
    
    return event_times

def process_video_clips(camera_name: str, nas_folder: str, event_times: List[datetime.datetime], tmp_dir: str) -> str:
    """イベント時刻から動画を切り出し、タイムラプス化して結合する"""
    clips = []
    last_end_time = None

    for dt in event_times:
        if last_end_time and dt < last_end_time:
            continue

        date_str = dt.strftime("%Y%m%d")
        search_pattern = os.path.join(config.NVR_RECORD_DIR, nas_folder, f"{date_str}_*.mp4")
        found_files = sorted(glob.glob(search_pattern))
        
        if not found_files:
            # ★追加: 見つからなかったファイルパスを警告出力する
            logger.warning(f"⚠️ 動画ファイルが見つかりません: {search_pattern}")
            continue
            
        src_video = found_files[-1] # 最新のファイルを使用
        logger.info(f"🎥 動画ファイルを発見: {src_video} (抽出開始...)")
        
        clip_name = os.path.join(tmp_dir, f"{camera_name}_{dt.strftime('%H%M%S')}.ts")
        
        # --- 🎬 修正箇所: ここから ---
        # ファイル名 (例: 20260215_091822.mp4) から録画開始時刻を取得し、シーク秒数を計算する
        f_start_dt_str = os.path.basename(src_video).split('.')[0]
        try:
            f_start_dt = datetime.datetime.strptime(f_start_dt_str, "%Y%m%d_%H%M%S")
            # タイムゾーンのズレを防ぐためnaiveな日時に統一して計算
            dt_naive = dt.replace(tzinfo=None) 
            exact_seek = (dt_naive - f_start_dt).total_seconds()
            seek_sec = str(max(0.0, exact_seek - 5.0)) # 5秒前から切り出し
        except ValueError:
            seek_sec = "0"
            logger.warning(f"⚠️ ファイル名からの時刻取得に失敗しました。先頭から切り出します: {src_video}")

        text_overlay = f"drawtext=text='{dt.strftime('%Y-%m-%d %H\\:%M\\:%S')}':fontcolor=white:fontsize=24:x=w-tw-10:y=10"
        filter_complex = f"[0:v]{text_overlay},scale=1280:-2,setpts=0.25*PTS[v]"
        
        cmd = [
            "nice", "-n", "15", "ffmpeg", "-y",
            "-ss", seek_sec,  # ★追加: 計算した秒数から切り出しを開始する
            "-i", src_video,
            "-t", "20",       # そこから20秒間切り出す
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            clip_name
        ]
        
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=True)
            clips.append(clip_name)
            last_end_time = dt + datetime.timedelta(seconds=20)
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg抽出エラー: {e.stderr.strip()}")

    if not clips:
        return ""

    list_file = os.path.join(tmp_dir, f"{camera_name}_list.txt")
    with open(list_file, "w") as f:
        for clip in clips:
            f.write(f"file '{clip}'\n")

    output_video = os.path.join(tmp_dir, f"{camera_name}_timelapse.mp4")
    concat_cmd = [
        "nice", "-n", "15", "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_video
    ]
    subprocess.run(concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    return output_video

def upload_video_to_discord(file_path: str, message: str) -> None:
    """Discordへ動画ファイルを直接アップロードする（分割対応・エラー検知強化版）"""
    # ★修正: Discordの10MB制限に対応するため、余裕を見て 8MB を閾値にする
    max_size = 8 * 1024 * 1024
    
    # configからの取得を安全に行う
    webhook_url = getattr(config, 'DISCORD_WEBHOOK_REPORT', getattr(config, 'DISCORD_WEBHOOK_URL', None))
    
    if not webhook_url:
        logger.error("❌ DiscordのWebhook URLが設定されていません。")
        return

    file_size = os.path.getsize(file_path)
    logger.info(f"📤 動画をDiscordへ送信します。サイズ: {file_size / (1024*1024):.2f} MB")
    
    if file_size <= max_size:
        try:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f, "video/mp4")}
                res = requests.post(webhook_url, data={"content": message}, files=files)
                
                # HTTPステータスコードが成功(200系)かチェック
                if res.status_code not in [200, 204]:
                    logger.error(f"❌ Discord送信エラー (HTTP {res.status_code}): {res.text}")
                else:
                    logger.info("✅ Discord送信に成功しました！")
        except Exception as e:
            logger.error(f"❌ Discord送信中に例外発生: {e}")
    else:
        # 8MBを超える場合は分割
        logger.info(f"⚠️ ファイルが制限を超えています。分割処理を開始します...")
        split_pattern = file_path.replace(".mp4", "_part%03d.mp4")
        split_cmd = [
            "nice", "-n", "15", "ffmpeg", "-y",
            "-i", file_path,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", "30",  # ★修正: 60秒だと10MBを超える可能性があるため 30秒 に短縮
            "-reset_timestamps", "1",
            split_pattern
        ]
        subprocess.run(split_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        split_files = sorted(glob.glob(file_path.replace(".mp4", "_part*.mp4")))
        for i, split_file in enumerate(split_files):
            part_msg = f"{message} (Part {i+1}/{len(split_files)})"
            try:
                with open(split_file, "rb") as f:
                    files = {"file": (os.path.basename(split_file), f, "video/mp4")}
                    res = requests.post(webhook_url, data={"content": part_msg}, files=files)
                    if res.status_code not in [200, 204]:
                        logger.error(f"❌ Discord送信エラー Part {i+1} (HTTP {res.status_code}): {res.text}")
                    else:
                        logger.info(f"✅ Discord送信成功 Part {i+1}！")
            except Exception as e:
                logger.error(f"❌ Discord送信中に例外発生 Part {i+1}: {e}")
            time.sleep(2)

def main():
    parser = argparse.ArgumentParser(description="タイムラプス生成スクリプト")
    parser.add_argument("--date", type=str, help="対象日付を YYYY-MM-DD 形式で指定。指定なしで本日。")
    parser.add_argument("--limit", type=int, default=0, help="【検証用】処理するイベント数の上限を指定（例: --limit 5）") # ★追加
    args = parser.parse_args()

    if args.date:
        try:
            target_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            logger.error("❌ 日付のフォーマットが不正です。")
            return
    else:
        target_date = datetime.date.today() 

    # ★修正: タイムゾーン(+09:00)を外して、DBに保存されている文字列(T06:00:00.000000)と揃える
    start_time_str = f"{target_date.isoformat()}T06:00:00.000000"
    end_time_str = f"{target_date.isoformat()}T23:59:59.999999"
    
    os.makedirs(config.TMP_VIDEO_DIR, exist_ok=True)

    TARGET_CAM_MAP = {
        "防犯カメラ": "garden", 
        "駐車場カメラ": "parking"
    }

    for db_name, nas_folder in TARGET_CAM_MAP.items():
        logger.info(f"Generating timelapse for {db_name}...")
        # ログを追加して、探している時間帯を確認
        logger.debug(f"Search window: {start_time_str} to {end_time_str}")
        
        event_times = get_event_times(db_name, start_time_str, end_time_str)
        
        if not event_times:
            logger.info(f"No events found for {db_name} today.")
            continue
            
        logger.info(f"✅ {db_name} のイベントを {len(event_times)} 件見つけました。動画生成を開始します。")
        
        # ==========================================
        # 🛡️ 恒久対策: ハードリミットと均等サンプリング処理
        # ==========================================
        # 1日の最大処理件数を定義 (Raspberry Pi 5 のサーマルリミットを考慮して最大50件 = 約15分エンコード程度に抑える)
        MAX_SAFE_LIMIT = 50 
        
        # コマンドライン引数で明示的に limit が渡されている場合はそちらを優先
        actual_limit = args.limit if args.limit > 0 else MAX_SAFE_LIMIT

        if len(event_times) > actual_limit:
            # 設計書準拠: WARNINGログとして記録（Discord等の通知対象にするため）
            logger.warning(f"⚠️ [{db_name}] イベント数({len(event_times)}件)が安全上限({actual_limit}件)を超過しました。システムの過熱を防ぐため均等サンプリングを実施します。")
            
            # 1日の出来事が満遍なく含まれるように、均等な間隔で要素を抽出する
            step = len(event_times) / actual_limit
            sampled_times = [event_times[math.floor(i * step)] for i in range(actual_limit)]
            event_times = sampled_times
            
            logger.info(f"🔧 サンプリング完了: {len(event_times)} 件の動画生成を開始します。")
        else:
            logger.info(f"🚀 全 {len(event_times)} 件の動画生成を開始します。")
        
        # ==========================================
        
        output_video = process_video_clips(db_name, nas_folder, event_times, config.TMP_VIDEO_DIR)
        
        if output_video and os.path.exists(output_video):
            msg = f"📼 {db_name} のハイライト ({target_date.isoformat()})"
            # upload_video_to_discord を呼び出してDiscordへ
            upload_video_to_discord(output_video, msg)
            logger.info(f"✨ {db_name} のアップロードが完了しました。")
            
    # クリーンアップ
    for f in glob.glob(os.path.join(config.TMP_VIDEO_DIR, "*")):
        os.remove(f)

if __name__ == "__main__":
    main()