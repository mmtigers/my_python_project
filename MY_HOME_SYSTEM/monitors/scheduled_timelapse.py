import os
import sys
import glob
import subprocess
import argparse
import time as time_module
from datetime import datetime, time
from pathlib import Path

# プロジェクトルートのパス解決
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from core.logger import setup_logging
from services.notification_service import send_push
import config

logger = setup_logging("scheduled_timelapse")

RECORD_DIR = os.path.join(PROJECT_ROOT, "data", "timelapse_records")
os.makedirs(RECORD_DIR, exist_ok=True)

# 監視対象の設定（ベースディレクトリと全カメラの定義）
TARGET_BASE_DIR = "/mnt/nas/home_system/nvr_recordings"
TARGET_CAMERAS = getattr(config, "TIMELAPSE_CAMERAS", {
    "entrance": os.path.join(TARGET_BASE_DIR, "entrance"),
    "garden": os.path.join(TARGET_BASE_DIR, "garden"),
    "parking": os.path.join(TARGET_BASE_DIR, "parking")
})

# 定期実行時にデフォルトで処理するカメラのリスト
DEFAULT_TARGET_CAMERAS = ["entrance", "parking"]

# 抽出時間帯とトリガー定義
# 書式: name: (抽出開始時刻, 抽出終了時刻, 実行トリガー時刻の開始, 実行トリガー時刻の終了)
SCHEDULES = getattr(config, "TIMELAPSE_SCHEDULES", {
    "morning": (time(7, 50), time(8, 30), time(8, 30), time(9, 0)),
    "evening": (time(15, 0), time(16, 0), time(16, 0), time(16, 30))
})

# FFmpegパラメータの外部設定化（フォールバック付き）
FFMPEG_FPS = getattr(config, "TIMELAPSE_FPS", "15")
FFMPEG_BITRATE = getattr(config, "TIMELAPSE_BITRATE", "1500k")
FFMPEG_MAXRATE = getattr(config, "TIMELAPSE_MAXRATE", "2000k")
FFMPEG_SEGMENT_TIME = getattr(config, "TIMELAPSE_SEGMENT_TIME", "40")

def cleanup_old_records(days: int = 7):
    """指定日数以上古い .done ファイルを削除する"""
    now_ts = time_module.time()
    for f in glob.glob(os.path.join(RECORD_DIR, "*.done")):
        if os.path.isfile(f):
            if now_ts - os.path.getmtime(f) > days * 86400:
                try:
                    os.remove(f)
                    logger.info(f"古い記録ファイルを削除しました: {os.path.basename(f)}")
                except Exception as e:
                    logger.error(f"ファイル削除エラー ({f}): {e}")

def get_target_files(target_dir: str, target_date: str, start_time: time, end_time: time) -> list:
    """指定された時間帯に含まれる10分単位のMP4ファイルを取得する"""
    files = []
    pattern = os.path.join(target_dir, f"{target_date}_*.mp4")
    
    for file_path in sorted(glob.glob(pattern)):
        filename = os.path.basename(file_path)
        try:
            # ファイル名(YYYYMMDD_HHMMSS.mp4)から時刻部分を抽出
            time_str = filename.split('_')[1].split('.')[0]
            file_time = time(int(time_str[0:2]), int(time_str[2:4]), int(time_str[4:6]))
            
            # 開始時刻が指定範囲内、または指定範囲に被るファイルを対象とする
            if start_time <= file_time <= end_time:
                files.append(file_path)
        except Exception as e:
            logger.warning(f"ファイル名のパースに失敗しました ({filename}): {e}")
            continue
            
    return files

def generate_timelapse(file_list: list, output_base_path: str) -> list:
    """FFmpegを使用してタイムラプス動画を生成し、分割されたファイルのリストを返す"""
    if not file_list:
        return []
        
    list_file_path = os.path.join(PROJECT_ROOT, "data", "concat_list.txt")
    generated_files = []
    try:
        with open(list_file_path, "w") as f:
            for file_path in file_list:
                f.write(f"file '{file_path}'\n")
                
        # 分割出力用のファイル名パターンを生成
        output_pattern = output_base_path.replace(".mp4", "_part%02d.mp4")
        
        # FFmpegコマンドの構築
        cmd = [
            "nice", "-n", "15",
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file_path,
            "-filter_complex", "setpts=0.025*PTS,scale=854:480",
            "-c:v", "libx264", "-preset", "ultrafast", 
            "-r", FFMPEG_FPS, "-b:v", FFMPEG_BITRATE, "-maxrate", FFMPEG_MAXRATE, "-bufsize", FFMPEG_MAXRATE, 
            "-an", "-threads", "2",
            "-f", "segment", "-segment_time", FFMPEG_SEGMENT_TIME, "-reset_timestamps", "1",
            output_pattern
        ]
        
        logger.info(f"FFmpegコマンド実行: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if res.returncode == 0:
            logger.info("タイムラプス生成成功")
            search_pattern = output_base_path.replace(".mp4", "_part*.mp4")
            generated_files = sorted(glob.glob(search_pattern))
            return generated_files
        else:
            logger.error(f"FFmpegエラー: {res.stderr.decode('utf-8')}")
            return []
            
    except Exception as e:
        logger.error(f"動画生成中に例外発生: {e}")
        return []
    finally:
        if os.path.exists(list_file_path):
            os.remove(list_file_path)

def main(args):
    now = datetime.now()
    current_time = now.time()
    
    # 対象日付の決定 (引数指定がなければ今日)
    target_date_str = args.date if args.date else now.strftime("%Y%m%d")
    
    # スケジュールの決定
    schedules = SCHEDULES.copy()
    force_schedule = args.force
    
    # カスタム時刻指定がある場合
    if args.start and args.end:
        try:
            start_t = time(int(args.start[:2]), int(args.start[2:4]))
            end_t = time(int(args.end[:2]), int(args.end[2:4]))
            schedules = {"custom": (start_t, end_t, time(0,0), time(23,59))}
            force_schedule = "custom"
            logger.info(f"カスタム時間帯での抽出を実行: {target_date_str} {start_t.strftime('%H:%M')} - {end_t.strftime('%H:%M')}")
        except Exception as e:
            logger.error(f"カスタム時刻のフォーマットエラー (HHMM形式で指定してください): {e}")
            return
            
    # 古い記録ファイルのクリーンアップ
    cleanup_old_records()
    
    # 処理対象のカメラを決定 (引数指定があればそれを優先、なければデフォルト)
    target_camera_keys = args.cameras.split(",") if args.cameras else DEFAULT_TARGET_CAMERAS
    
    # カメラごとにループ
    for camera_name in target_camera_keys:
        camera_name = camera_name.strip()
        if camera_name not in TARGET_CAMERAS:
            logger.warning(f"未知のカメラが指定されました: {camera_name}")
            continue
            
        target_dir = TARGET_CAMERAS[camera_name]
        
        for schedule_name, (start_t, end_t, trigger_start, trigger_end) in schedules.items():
            is_triggered = (trigger_start <= current_time <= trigger_end)
            
            # 強制実行の指定がある場合はトリガー条件を無視
            if force_schedule == schedule_name:
                is_triggered = True
                
            if is_triggered:
                record_file = os.path.join(RECORD_DIR, f"{target_date_str}_{camera_name}_{schedule_name}.done")
                
                # 実行済みか確認 (強制/カスタム実行時は無視)
                if os.path.exists(record_file) and force_schedule != schedule_name:
                    continue
                    
                logger.info(f"[{camera_name}] {schedule_name} のタイムラプス処理を開始")
                target_files = get_target_files(target_dir, target_date_str, start_t, end_t)
                
                if not target_files:
                    logger.warning(f"対象期間の動画ファイルが見つかりません ([{camera_name}] {schedule_name})")
                    if force_schedule != schedule_name:
                        Path(record_file).touch()
                    continue
                    
                output_filename = f"timelapse_{camera_name}_{schedule_name}_{target_date_str}.mp4"
                output_path = os.path.join(PROJECT_ROOT, "data", output_filename)
                
                generated_files = generate_timelapse(target_files, output_path)
                
                if generated_files:
                    total_parts = len(generated_files)
                    line_user_id = getattr(config, "LINE_USER_ID", "")
                    
                    for idx, part_file in enumerate(generated_files, start=1):
                        part_filename = os.path.basename(part_file)
                        message = f"📼 {camera_name.capitalize()} / {schedule_name.capitalize()}のタイムラプス映像 ({target_date_str} {start_t.strftime('%H:%M')} - {end_t.strftime('%H:%M')}) - Part {idx}/{total_parts}"
                        
                        try:
                            file_size_mb = os.path.getsize(part_file) / (1024 * 1024)
                            logger.info(f"生成されたタイムラプス動画サイズ ({part_filename}): {file_size_mb:.2f} MB")

                            with open(part_file, "rb") as f:
                                video_data = f.read()
                            
                            push_success = send_push(line_user_id, [message], image_data=video_data, target="discord", channel="notify", filename=part_filename)
                            
                            if push_success:
                                logger.info(f"Discordへタイムラプス動画({part_filename})の送信完了")
                            else:
                                logger.error(f"Discordへのタイムラプス動画({part_filename})送信に失敗しました")
                                
                        except Exception as e:
                            logger.error(f"通知送信処理中に例外発生 ({part_filename}): {e}")
                        
                        if idx < total_parts:
                            time_module.sleep(5)
                
                # 実行完了を記録
                Path(record_file).touch()
                
                # 容量節約のため生成動画を削除
                if generated_files:
                    for part_file in generated_files:
                        if os.path.exists(part_file):
                            os.remove(part_file)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="監視カメラタイムラプス生成スクリプト")
    parser.add_argument("--force", type=str, help="指定したスケジュール名(morning/evening等)を強制実行")
    parser.add_argument("--date", type=str, help="対象日付 (YYYYMMDD) 省略時は本日")
    parser.add_argument("--start", type=str, help="カスタム開始時刻 (例: 1200)")
    parser.add_argument("--end", type=str, help="カスタム終了時刻 (例: 1330)")
    parser.add_argument("--cameras", type=str, help="対象カメラをカンマ区切りで指定 (例: entrance,parking)")
    args = parser.parse_args()
    
    if args.force or (args.start and args.end) or args.date:
        logger.info(f"手動強制実行モードで起動: {args}")
        
    main(args)