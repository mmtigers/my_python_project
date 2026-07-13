import os
import sys
import glob
import subprocess
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

# 監視対象の設定
TARGET_DIR = "/mnt/nas/home_system/nvr_recordings/entrance"
RECORD_DIR = os.path.join(PROJECT_ROOT, "data", "timelapse_records")
os.makedirs(RECORD_DIR, exist_ok=True)

# 抽出時間帯とトリガー定義
# 書式: name: (抽出開始時刻, 抽出終了時刻, 実行トリガー時刻の開始, 実行トリガー時刻の終了)
SCHEDULES = {
    "morning": (time(7, 50), time(8, 30), time(8, 30), time(9, 0)),
    "evening": (time(15, 0), time(16, 0), time(16, 0), time(16, 30))
}

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
        # nice -n 15 で優先度低下, 40倍速(setpts=0.025*PTS), 480pスケール, ultrafast, 音声除去
        # 15fpsへの間引きとビットレート制限(1.5Mbps)に加え、40秒単位で動画をセグメント分割
        cmd = [
            "nice", "-n", "15",
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file_path,
            "-filter_complex", "setpts=0.025*PTS,scale=854:480",
            "-c:v", "libx264", "-preset", "ultrafast", 
            "-r", "15", "-b:v", "1500k", "-maxrate", "2000k", "-bufsize", "2000k", 
            "-an", "-threads", "2",
            "-f", "segment", "-segment_time", "40", "-reset_timestamps", "1",
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

def main(force_schedule: str = None):
    now = datetime.now()
    current_time = now.time()
    today_str = now.strftime("%Y%m%d")
    
    for schedule_name, (start_t, end_t, trigger_start, trigger_end) in SCHEDULES.items():
        is_triggered = (trigger_start <= current_time <= trigger_end)
        
        # 強制実行の指定がある場合は、該当スケジュールのみトリガー条件を無視
        if force_schedule == schedule_name:
            is_triggered = True
            
        if is_triggered:
            record_file = os.path.join(RECORD_DIR, f"{today_str}_{schedule_name}.done")
            
            # 既に本日分の該当スケジュールが実行済みか確認 (強制実行時は無視)
            if os.path.exists(record_file) and force_schedule != schedule_name:
                continue
                
            logger.info(f"{schedule_name} のタイムラプス処理を開始")
            target_files = get_target_files(TARGET_DIR, today_str, start_t, end_t)
            
            if not target_files:
                logger.warning(f"対象期間の動画ファイルが見つかりません ({schedule_name})")
                Path(record_file).touch() # 再試行防止
                continue
                
            output_filename = f"timelapse_entrance_{schedule_name}_{today_str}.mp4"
            output_path = os.path.join(PROJECT_ROOT, "data", output_filename)
            
            generated_files = generate_timelapse(target_files, output_path)
            
            if generated_files:
                total_parts = len(generated_files)
                line_user_id = getattr(config, "LINE_USER_ID", "")
                
                for idx, part_file in enumerate(generated_files, start=1):
                    part_filename = os.path.basename(part_file)
                    message = f"📼 {schedule_name.capitalize()}のタイムラプス映像 ({start_t.strftime('%H:%M')} - {end_t.strftime('%H:%M')}) - Part {idx}/{total_parts}"
                    
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
                    
                    # APIのレートリミットを回避するためのインターバル
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
    # コマンドライン引数で --force <schedule_name> が渡された場合の処理
    if len(sys.argv) == 3 and sys.argv[1] == "--force":
        target_schedule = sys.argv[2]
        logger.info(f"手動強制実行モードで起動: {target_schedule}")
        main(force_schedule=target_schedule)
    else:
        main()