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

def extract_video_clip(cmd: List[str], input_path: str, output_path: str, max_retries: int = 3) -> bool:
    """
    FFmpegを使用して動画ファイルからクリップを抽出する。
    Exponential Backoffを用いたリトライを行い、破損ファイル等で復旧不可能な場合はFalseを返す。
    
    Args:
        cmd (List[str]): 実行するFFmpegコマンドのリスト
        input_path (str): 入力動画ファイルのパス（ログ出力用）
        output_path (str): 出力先ファイルのパス（ログ出力用）
        max_retries (int): 最大リトライ回数 (デフォルト: 3)
        
    Returns:
        bool: 抽出に成功した場合はTrue、スキップ（失敗）した場合はFalse
    """
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(f"抽出開始: {input_path} (Attempt: {attempt}/{max_retries})")
            
            # subprocess実行 (必ずtimeoutを設定しプロセスハングを防ぐ)
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300
            )
            logger.debug(f"抽出成功: {output_path}")
            return True
            
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.lower() if e.stderr else ""
            
            # フェイルソフト: 致命的なファイル破損と判断される場合はリトライを打ち切り即座にスキップ
            if "moov atom not found" in err_msg or "invalid data found" in err_msg:
                logger.error(f"ファイル破損のためスキップします: {input_path}")
                return False
                
            # Silence Policy: 途中経過はWARNINGに留める
            logger.warning(f"FFmpegエラー (Attempt {attempt}/{max_retries}): {err_msg.strip()}")
            
            if attempt < max_retries:
                sleep_time = 2 ** attempt  # 指数関数的待機 (2, 4秒...)
                time.sleep(sleep_time)
                
        except subprocess.TimeoutExpired:
            logger.warning(f"FFmpegタイムアウト (Attempt {attempt}/{max_retries}): {input_path}")
            if attempt < max_retries:
                time.sleep(2 ** attempt)

    # 最終的に失敗が確定した段階で1度だけERRORを出力
    logger.error(f"最大リトライ回数超過。抽出をスキップします: {input_path}")
    return False

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

        # --- 修正: 日またぎファイル検索ロジック ---
        dt_prev = dt - datetime.timedelta(hours=1)
        date_str_current = dt.strftime("%Y%m%d")
        date_str_prev = dt_prev.strftime("%Y%m%d")
        
        search_patterns = [
            os.path.join(config.NVR_RECORD_DIR, nas_folder, f"{date_str_current}_*.mp4")
        ]
        if date_str_current != date_str_prev:
            search_patterns.append(os.path.join(config.NVR_RECORD_DIR, nas_folder, f"{date_str_prev}_*.mp4"))

        found_files = []
        for pattern in search_patterns:
            found_files.extend(glob.glob(pattern))
        found_files.sort()
        
        if not found_files:
            logger.warning(f"⚠️ 動画ファイルが見つかりません (検索パターン: {search_patterns})")
            continue
            
        # --- 🎬 修正: 常に最新ファイルを選ぶバグを修正し、イベント時刻に合ったファイルを探す ---
        dt_naive = dt.replace(tzinfo=None)
        src_video = None
        f_start_dt = None
        
        # 録画ファイルを順番にチェックし、イベント時刻(dt)以前の最後のファイルを見つける
        for f in found_files:
            f_name = os.path.basename(f).split('.')[0]
            try:
                f_time = datetime.datetime.strptime(f_name, "%Y%m%d_%H%M%S")
                if f_time <= dt_naive:
                    src_video = f
                    f_start_dt = f_time
                else:
                    break # ソート済みなので、時刻を超えたら探索終了
            except ValueError:
                continue
        
        if not src_video or not f_start_dt:
            logger.warning(f"⚠️ イベント時刻 {dt.strftime('%H:%M:%S')} に対応する録画ファイルがありません。スキップします。")
            continue

        logger.info(f"🎥 動画ファイルを発見: {src_video} (対象イベント: {dt.strftime('%H:%M:%S')})")
        
        # ★修正: 拡張子を.mp4から.tsに戻す（concat時のタイムスタンプ破損を完全に防ぐため）
        clip_name = os.path.join(tmp_dir, f"{camera_name}_{dt.strftime('%H%M%S')}.ts")
        
        # シーク秒数を計算する
        exact_seek = (dt_naive - f_start_dt).total_seconds()
        seek_sec = str(max(0.0, exact_seek - 5.0)) # 5秒前から切り出し

        text_overlay = f"drawtext=text='{dt.strftime('%Y-%m-%d %H\\:%M\\:%S')}':fontcolor=white:fontsize=24:x=w-tw-10:y=10"
        filter_complex = f"[0:v]{text_overlay},scale=-2:720,setpts=0.125*PTS[v]"
        
        cmd = [
            "nice", "-n", "15", "ffmpeg", "-y",
            "-ss", seek_sec,
            "-t", "40",
            "-i", src_video,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-c:v", "libx264", 
            "-preset", "faster",
            "-crf", "28",
            "-maxrate", "1000k",
            "-bufsize", "2000k",
            "-an",
            "-f", "mpegts",        # ★追加: .ts出力用にフォーマットを明示
            clip_name
        ]
        
        success = extract_video_clip(cmd, src_video, clip_name)
        
        if success:
            clips.append(clip_name)
            last_end_time = dt + datetime.timedelta(seconds=40)
            
            # ★追加: 全件処理時のラズパイ過熱を防ぐためのマイクロインターバル
            # （間引きロジックを撤廃する代わりの Fail-Soft 設計）
            time.sleep(0.5)
        else:
            logger.warning(f"⚠️ クリップ抽出スキップ: {dt.strftime('%H:%M:%S')} のイベントをスキップしました。")
            continue

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
        "駐車場カメラ": "parking",
        "玄関カメラ": "entrance"
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
        
        # コマンドライン引数で明示的に limit が渡されている場合のみ制限を適用（検証時用）
        if args.limit > 0:
            logger.info(f"🔧 検証モード: 上限 {args.limit} 件で動画生成を開始します。")
            event_times = event_times[:args.limit]
        else:
            logger.info(f"🚀 全 {len(event_times)} 件の動画生成を開始します。")
        
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