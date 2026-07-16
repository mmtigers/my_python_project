import os
import sys
import subprocess
import csv
import datetime
import math
import time
import json
import tempfile
import numpy as np
import cv2
import traceback
import shutil
import re
import requests
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass, field, asdict

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# プロジェクトルートの解決と追加
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import config
from core.logger import setup_logging
from services.notification_service import send_push

__version__ = "1.6.4"
logger = setup_logging(__name__)

# ==========================================
# 1. 設定値
# ==========================================
FPS_ANALYZE = getattr(config, 'TIMELAPSE_FPS_ANALYZE', 1)
WIDTH = getattr(config, 'TIMELAPSE_WIDTH', 320)
HEIGHT = getattr(config, 'TIMELAPSE_HEIGHT', 180)

# OpenCV解析パラメータ
BG_HISTORY = getattr(config, 'TIMELAPSE_BG_HISTORY', 120)
BG_VAR_THRESH = getattr(config, 'TIMELAPSE_BG_VAR_THRESH', 16)
MORPH_KERNEL_SIZE = getattr(config, 'TIMELAPSE_MORPH_KERNEL_SIZE', 3)
MIN_AREA_THRESHOLD = getattr(config, 'TIMELAPSE_MIN_AREA_THRESHOLD', 300)

ROI_X = getattr(config, 'TIMELAPSE_ROI_X', 0)
ROI_Y = getattr(config, 'TIMELAPSE_ROI_Y', 0)
ROI_W = getattr(config, 'TIMELAPSE_ROI_W', WIDTH)
ROI_H = getattr(config, 'TIMELAPSE_ROI_H', HEIGHT)

GAP_THRESH = getattr(config, 'TIMELAPSE_GAP_THRESH', 5)
BUFFER_SEC = getattr(config, 'TIMELAPSE_BUFFER_SEC', 3)
SPEEDUP_FACTOR = getattr(config, 'TIMELAPSE_SPEEDUP_FACTOR', 4)

DEBUG_FFMPEG = getattr(config, 'TIMELAPSE_DEBUG_FFMPEG', False)
FAST_STREAM_COPY_MODE = getattr(config, 'TIMELAPSE_FAST_STREAM_COPY_MODE', False)

FONT_FILE = getattr(config, 'TIMELAPSE_FONT_FILE', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
MAX_FILE_SIZE_MB = getattr(config, 'TIMELAPSE_MAX_FILE_SIZE_MB', 22)
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
SAFE_SPLIT_BYTES = 10 * 1024 * 1024 # 分割処理用バッファマージン

def get_ffmpeg_stderr():
    return sys.stderr if DEBUG_FFMPEG else subprocess.DEVNULL

# ==========================================
# データクラス
# ==========================================
@dataclass
class MotionRecord:
    time_sec: int
    largest_area: float
    contour_count: int

@dataclass
class EventRecord:
    event_id: str
    start_sec: int
    end_sec: int
    max_area: float
    score: float = 0.0
    duration: int = 0
    person_count: int = 0
    vehicle_count: int = 0
    animal_count: int = 0
    face_detected: int = 0

    def __post_init__(self):
        self.duration = (self.end_sec - self.start_sec) + 1

@dataclass
class SummaryInfo:
    target_date: str
    events: int = 0
    summary_duration: int = 0
    total_processing_time: float = 0.0
    output_path: str = ""
    file_size_bytes: int = 0
    version: str = __version__
    ffmpeg_version: str = ""
    opencv_version: str = cv2.__version__
    fast_stream_copy_mode: bool = FAST_STREAM_COPY_MODE

# ==========================================
# ユーティリティ
# ==========================================
def sec_to_time(sec: int) -> str:
    return str(datetime.timedelta(seconds=sec))

def check_dependencies() -> bool:
    required_cmds = ["ffmpeg", "ffprobe", "nice"]
    for cmd in required_cmds:
        if not shutil.which(cmd):
            logger.error(f"{cmd}コマンドが見つかりません。")
            return False
    return True

def get_ffmpeg_version() -> str:
    try:
        res = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        return res.stdout.splitlines()[0]
    except Exception:
        return "Unknown"

def check_drawtext_localtime_support() -> bool:
    try:
        res = subprocess.run(["ffmpeg", "-h", "filter=drawtext"], capture_output=True, text=True, stderr=subprocess.STDOUT, timeout=10)
        return "localtime" in res.stdout
    except Exception:
        return False

HAS_DRAWTEXT_LOCALTIME = check_drawtext_localtime_support()

def get_video_info(input_path: str, retries: int = 3) -> Dict[str, Any]:
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', input_path]
    for attempt in range(1, retries + 1):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode != 0:
                logger.error(f"ffprobeがエラー終了しました: {res.stderr}")
                return {}
            if not res.stdout.strip():
                logger.error("ffprobeの出力が空でした。")
                return {}
            return json.loads(res.stdout)
        except subprocess.TimeoutExpired as e:
            logger.warning(f"ffprobeがタイムアウトしました (Attempt {attempt}/{retries}): {input_path}")
            if attempt < retries:
                time.sleep(5)  # NASの負荷軽減
            else:
                logger.error(f"ffprobeの解析に失敗しました(最大再試行到達): {e}")
                return {}
        except Exception as e:
            logger.error(f"ffprobeの解析中に予期せぬエラー: {e}")
            return {}
    return {}

def get_video_start_dt(input_path: str, video_info: Dict[str, Any]) -> datetime.datetime:
    try:
        tags = video_info.get("format", {}).get("tags", {})
        if "creation_time" in tags:
            dt = datetime.datetime.fromisoformat(tags["creation_time"].replace("Z", "+00:00"))
            return dt.astimezone().replace(tzinfo=None)
    except Exception as e:
        logger.debug(f"creation_time パースに失敗: {e}")

    base_name = os.path.basename(input_path)
    m = re.search(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', base_name)
    if m:
        try:
            return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 
                                     int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except ValueError:
            pass

    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', base_name)
    if m:
        try:
            return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    target_date = datetime.date.today()
    return datetime.datetime.combine(target_date, datetime.time(0, 0, 0))

def escape_ffmpeg_filename(filename: str) -> str:
    return filename.replace("'", "'\\''")

def escape_drawtext(text: str) -> str:
    return text.replace('\\', '\\\\').replace("'", "'\\''").replace(':', '\\:')

def check_roi(w: int, h: int) -> None:
    if ROI_W <= 0 or ROI_H <= 0:
        raise ValueError(f"ROIの幅と高さは0より大きい必要があります (W:{ROI_W}, H:{ROI_H})")
    if not (0 <= ROI_X < w) or not (0 <= ROI_Y < h):
        raise ValueError(f"ROIの始点が画面外です (X:{ROI_X}, Y:{ROI_Y}, W:{w}, H:{h})")
    if ROI_X + ROI_W > w or ROI_Y + ROI_H > h:
        raise ValueError(f"ROIが画面サイズ({w}x{h})に収まっていません")

def setup_directories() -> Tuple[str, str, str]:
    base_dir = getattr(config, "BASE_DIR", PROJECT_ROOT)
    work_dir = os.path.join(base_dir, "work", "timelapse")
    output_dir = os.path.join(base_dir, "assets", "timelapse")
    records_dir = os.path.join(base_dir, "data", "timelapse_records")
    
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(records_dir, exist_ok=True)
    
    for f in os.listdir(work_dir):
        file_path = os.path.join(work_dir, f)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.remove(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            logger.warning(f"作業ディレクトリのクリーンアップに失敗しました: {e}")
            
    return work_dir, output_dir, records_dir

def mark_as_done(records_dir: str, base_filename: str, summary: SummaryInfo):
    os.makedirs(records_dir, exist_ok=True)
    record_file = os.path.join(records_dir, f"{os.path.splitext(base_filename)[0]}.done")
    with open(record_file, "w", encoding="utf-8") as f:
        json.dump(asdict(summary), f, indent=2, ensure_ascii=False)

def log_cpu_usage():
    if HAS_PSUTIL:
        logger.info(f"現在のCPU使用率: {psutil.cpu_percent()}%")

# ==========================================
# モジュール 1: MotionDetector
# ==========================================
class MotionDetector:
    def __init__(self):
        check_roi(WIDTH, HEIGHT)
        self.fgbg = cv2.createBackgroundSubtractorMOG2(history=BG_HISTORY, varThreshold=BG_VAR_THRESH, detectShadows=False)
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MORPH_KERNEL_SIZE, MORPH_KERNEL_SIZE))

    def detect(self, input_path: str, work_dir: str, duration_sec: float) -> List[MotionRecord]:
        logger.info(f"[1/4] 動き検知を開始します: {input_path}")
        os.makedirs(work_dir, exist_ok=True)
        log_cpu_usage()
        
        cmd = [
            'nice', '-n', '15',
            'ffmpeg', '-v', 'error', '-nostdin', '-i', input_path,
            '-vf', f'fps={FPS_ANALYZE},scale={WIDTH}:{HEIGHT}',
            '-f', 'image2pipe', '-pix_fmt', 'gray', '-vcodec', 'rawvideo', '-'
        ]
        if shutil.which('ionice'):
            cmd = ['ionice', '-c', '2', '-n', '7'] + cmd
        
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            logger.error(f"ffmpegプロセスの起動に失敗しました: {e}")
            raise
        
        records: List[MotionRecord] = []
        current_sec = 0
        frame_size = WIDTH * HEIGHT
        total_expected_frames = int(duration_sec * FPS_ANALYZE) if duration_sec else 0

        try:
            while True:
                raw_frame = process.stdout.read(frame_size)
                if len(raw_frame) != frame_size:
                    break
                    
                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((HEIGHT, WIDTH))
                roi_frame = frame[ROI_Y:ROI_Y+ROI_H, ROI_X:ROI_X+ROI_W]
                fgmask = self.fgbg.apply(roi_frame)
                fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_OPEN, self.kernel)
                fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_CLOSE, self.kernel)
                
                contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if contours:
                    largest_contour = max(contours, key=cv2.contourArea)
                    largest_area = cv2.contourArea(largest_contour)
                    if largest_area > MIN_AREA_THRESHOLD:
                        records.append(MotionRecord(current_sec, largest_area, len(contours)))
                        
                current_sec += 1
                if total_expected_frames > 0 and current_sec % max(1, int(total_expected_frames / 10)) == 0:
                    logger.info(f"解析進捗: {(current_sec / total_expected_frames) * 100:.0f}%")
            
            process.wait(timeout=60)
            if process.returncode != 0:
                err_msg = ""
                if process.stderr is not None:
                    err_msg = process.stderr.read().decode('utf-8', errors='ignore')
                    if DEBUG_FFMPEG:
                        logger.error(f"FFmpeg stderr:\n{err_msg}")
                raise subprocess.CalledProcessError(process.returncode, cmd, stderr=err_msg)

        except Exception as e:
            logger.exception(f"フレーム解析中に例外が発生しました: {e}")
            raise
        finally:
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            try:
                if process.poll() is None:
                    process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error("FFmpegプロセスの終了がタイムアウトしました。強制終了します。")
                process.kill()
                process.wait()

        motion_csv = os.path.join(work_dir, "motion.csv")
        with open(motion_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time", "largest_area", "contour_count"])
            for rec in records:
                writer.writerow([sec_to_time(rec.time_sec), rec.largest_area, rec.contour_count])

        return records

# ==========================================
# モジュール 2: EventBuilder
# ==========================================
class EventBuilder:
    def build(self, motion_records: List[MotionRecord], work_dir: str) -> List[EventRecord]:
        logger.info("[2/4] イベントのグルーピングとスコア算出...")
        if not motion_records:
            return []

        events: List[EventRecord] = []
        current_event_records = [motion_records[0]]

        for record in motion_records[1:]:
            last_record = current_event_records[-1]
            if record.time_sec - last_record.time_sec <= GAP_THRESH:
                current_event_records.append(record)
            else:
                events.append(self._create_event_record(current_event_records, len(events) + 1))
                current_event_records = [record]
                
        if current_event_records:
            events.append(self._create_event_record(current_event_records, len(events) + 1))

        # バッファの追加と重複結合
        merged_events: List[EventRecord] = []
        for ev in events:
            ev.start_sec = max(0, ev.start_sec - BUFFER_SEC)
            ev.end_sec = ev.end_sec + BUFFER_SEC
            ev.duration = (ev.end_sec - ev.start_sec) + 1
            
            if not merged_events:
                merged_events.append(ev)
            else:
                prev_ev = merged_events[-1]
                if ev.start_sec <= prev_ev.end_sec:
                    prev_ev.end_sec = max(prev_ev.end_sec, ev.end_sec)
                    prev_ev.duration = (prev_ev.end_sec - prev_ev.start_sec) + 1
                    prev_ev.max_area = max(prev_ev.max_area, ev.max_area)
                    prev_ev.score += ev.score
                else:
                    merged_events.append(ev)
                    
        for i, ev in enumerate(merged_events, 1):
            ev.event_id = f"Event{i:03d}"
            
        total_time = sum(e.duration for e in merged_events)
        logger.info(f"  -> 生成イベント数: {len(merged_events)}件 (合計: {total_time}秒)")
        
        events_csv = os.path.join(work_dir, "events.csv")
        with open(events_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "start", "end", "duration", "max_area", "score"])
            for ev in merged_events:
                writer.writerow([ev.event_id, sec_to_time(ev.start_sec), sec_to_time(ev.end_sec), ev.duration, ev.max_area, ev.score])

        events_enriched_csv = os.path.join(work_dir, "events_enriched.csv")
        with open(events_enriched_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "start", "end", "person_count", "vehicle_count", "animal_count", "face_detected", "score"])
            for ev in merged_events:
                writer.writerow([ev.event_id, sec_to_time(ev.start_sec), sec_to_time(ev.end_sec), 
                                 ev.person_count, ev.vehicle_count, ev.animal_count, ev.face_detected, ev.score])

        return merged_events

    def _create_event_record(self, records: List[MotionRecord], index: int) -> EventRecord:
        event_id = f"Event{index:03d}"
        start_sec = records[0].time_sec
        end_sec = records[-1].time_sec
        max_area = max(r.largest_area for r in records)
        score = sum(r.largest_area for r in records)
        return EventRecord(event_id=event_id, start_sec=start_sec, end_sec=end_sec, max_area=max_area, score=score)

# ==========================================
# モジュール 3: VideoBuilder
# ==========================================
class VideoBuilder:
    def build(self, input_path: str, events: List[EventRecord], output_path: str, temp_dir: str, video_start_dt: datetime.datetime) -> bool:
        if not events:
            return False

        logger.info(f"[3/4] 切り出し開始 (FAST_STREAM_COPY_MODE={FAST_STREAM_COPY_MODE})...")
        clip_files = []
        
        for ev in events:
            clip_path = self._build_clip(input_path, ev, temp_dir, video_start_dt)
            if clip_path:
                clip_files.append(clip_path)

        if not clip_files:
            logger.warning("有効なクリップが一つも生成されませんでした。")
            return False

        logger.info("[4/4] クリップの結合と全体サムネイル生成...")
        log_cpu_usage()
        
        if not self._build_concat(clip_files, output_path, temp_dir):
            return False
            
        self._generate_thumbnail(output_path)
        logger.info(f"タイムラプス動画の生成完了: {output_path}")
        return True

    def _build_clip(self, input_path: str, ev: EventRecord, temp_dir: str, video_start_dt: datetime.datetime) -> str:
        clip_path = os.path.join(temp_dir, f"{ev.event_id}.mp4")
        cmd = self._build_ffmpeg_command(input_path, ev, clip_path, video_start_dt)
        
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=get_ffmpeg_stderr(), check=True, timeout=3600)
            
            event_thumb_path = clip_path.replace(".mp4", ".jpg")
            self._generate_thumbnail(clip_path, event_thumb_path)
            
            return clip_path
        except subprocess.CalledProcessError:
            logger.error(f"FFmpeg切り出しエラー ({ev.event_id})")
            return ""
        except subprocess.TimeoutExpired:
            logger.error(f"FFmpeg処理がタイムアウトしました ({ev.event_id})")
            return ""

    def _build_ffmpeg_command(self, input_path: str, ev: EventRecord, clip_path: str, video_start_dt: datetime.datetime) -> List[str]:
        if FAST_STREAM_COPY_MODE:
            cmd = ['nice', '-n', '15', 'ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(ev.start_sec), '-i', input_path, '-t', str(ev.duration), '-c', 'copy', '-avoid_negative_ts', '1', clip_path]
        else:
            start_dt = video_start_dt + datetime.timedelta(seconds=ev.start_sec)
            vf = self._build_drawtext(ev, start_dt)
            
            # --- 軽量化のためのチューニング設定を追加 ---
            # 解像度を幅854に縮小、フレームレートを15fpsに落として劇的にサイズ削減
            vf += ",scale=854:-2,fps=15"
            cmd = ['nice', '-n', '15', 'ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(ev.start_sec), '-to', str(ev.end_sec), '-i', input_path, '-vf', vf, '-an', '-c:v', 'libx264', '-preset', 'superfast', '-crf', '32', clip_path]
        
        if shutil.which('ionice'): cmd = ['ionice', '-c', '2', '-n', '7'] + cmd
        return cmd

    def _build_drawtext(self, ev: EventRecord, start_dt: datetime.datetime) -> str:
        font_opt = f":fontfile='{FONT_FILE.replace(':', '\\\\:')}'" if os.path.exists(FONT_FILE) else ""
        if HAS_DRAWTEXT_LOCALTIME:
            ts = int(start_dt.timestamp())
            expr = r'%{pts\:localtime\:' + str(ts) + r'\:%Y-%m-%d %H\\\:%M\\\:%S}'
            vf = f"drawtext=text='{expr}'{font_opt}:x=10:y=10:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5"
        else:
            time_str = escape_drawtext(start_dt.strftime('%Y-%m-%d %H:%M:%S'))
            vf = f"drawtext=text='{time_str}'{font_opt}:x=10:y=10:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5"
        return f"{vf},drawtext=text='{escape_drawtext(ev.event_id)}'{font_opt}:enable='between(t,0,1)':x=10:y=50:fontsize=24:fontcolor=yellow:box=1:boxcolor=black@0.5,setpts={1.0 / SPEEDUP_FACTOR}*PTS"

    def _build_concat(self, clip_files: List[str], output_path: str, temp_dir: str) -> bool:
        concat_txt = os.path.join(temp_dir, "concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            for clip in clip_files: f.write(f"file '{escape_ffmpeg_filename(Path(clip).as_posix())}'\n")
        cmd = ['nice', '-n', '15', 'ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'concat', '-safe', '0', '-i', concat_txt]
        
        if FAST_STREAM_COPY_MODE:
            # FAST_STREAM_COPY の結合時にも軽量化オプションを適用
            cmd += ['-vf', f"setpts={1.0 / SPEEDUP_FACTOR}*PTS,scale=854:-2,fps=15", '-an', '-c:v', 'libx264', '-preset', 'superfast', '-crf', '32']
        else:
            cmd += ['-c', 'copy']
            
        cmd.append(output_path)
        
        if shutil.which('ionice'): cmd = ['ionice', '-c', '2', '-n', '7'] + cmd
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=get_ffmpeg_stderr(), check=True, timeout=3600)
            return True
        except Exception: return False

    def _generate_thumbnail(self, video_path: str, output_path: Optional[str] = None) -> None:
        out = output_path or video_path.replace(".mp4", ".jpg")
        try:
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', video_path, '-vframes', '1', '-q:v', '2', out], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300, check=True)
        except Exception as e:
            logger.warning(f"サムネイル生成に失敗しました ({video_path}): {e}")

# ==========================================
# モジュール 4: Uploader (Discord専用直接送信)
# ==========================================
class Uploader:
    def split_and_send(self, summary: SummaryInfo, base_filename: str) -> None:
        if not summary.output_path or not os.path.exists(summary.output_path): return
        summary.file_size_bytes = os.path.getsize(summary.output_path)
        
        # ⚠️環境に合わせてWebhook URLを変更してください (configから取得、または直接記述)
        webhook_url = getattr(config, "DISCORD_WEBHOOK_URL", "ここにDiscordのWebhook URLを貼り付け")
        if not webhook_url or webhook_url == "ここにDiscordのWebhook URLを貼り付け":
            logger.error("Discord Webhook URLが設定されていないため動画を送信できません。")
            return

        if summary.file_size_bytes <= MAX_FILE_SIZE_BYTES:
            logger.info(f"動画サイズ {summary.file_size_bytes/(1024*1024):.2f}MB。そのまま送信します。")
            msg = f"🎥 {summary.target_date} のダイジェスト動画: {base_filename}"
            self._send_to_discord(webhook_url, msg, summary.output_path)
            self._send_completion_notice(webhook_url, 1)
        else:
            logger.warning(f"動画サイズ ({summary.file_size_bytes/(1024*1024):.2f}MB) が制限を超過。分割処理を開始します。")
            log_cpu_usage()
            try:
                res = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', summary.output_path], capture_output=True, text=True, check=True, timeout=60)
                dur = float(res.stdout.strip())
                # バッファを持たせたサイズ (10MB) で分割数を決定し、偏りによる送信エラーを防止
                pc = math.ceil(summary.file_size_bytes / SAFE_SPLIT_BYTES)
                base_dir = os.path.dirname(summary.output_path)
                split_pattern = os.path.join(base_dir, f"{os.path.splitext(os.path.basename(summary.output_path))[0]}_part_%03d.mp4")
                split_cmd = ['nice', '-n', '15', 'ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', summary.output_path, '-c', 'copy', '-f', 'segment', '-segment_time', str(math.ceil(dur / pc)), '-reset_timestamps', '1', split_pattern]
                
                if shutil.which('ionice'):
                    split_cmd = ['ionice', '-c', '2', '-n', '7'] + split_cmd
                    
                subprocess.run(split_cmd, stdout=subprocess.DEVNULL, stderr=get_ffmpeg_stderr(), check=True, timeout=3600)
                
                split_files = sorted(Path(base_dir).glob(f"{os.path.splitext(os.path.basename(summary.output_path))[0]}_part_*.mp4"))
                if not split_files: raise RuntimeError("動画分割に失敗しました。出力ファイルが生成されませんでした。")
                
                for i, s_file in enumerate(split_files):
                    msg = f"🎥 {summary.target_date} ダイジェスト (Part {i+1}/{len(split_files)})"
                    self._send_to_discord(webhook_url, msg, str(s_file))
                    time.sleep(5) # APIレートリミット対策
                    
                self._send_completion_notice(webhook_url, len(split_files))
            except Exception as e: logger.error(f"分割送信エラー: {e}")

    def _send_to_discord(self, webhook_url: str, message: str, file_path: str) -> None:
        """Discord Webhookにファイルを直接アップロードする"""
        try:
            with open(file_path, "rb") as f:
                file_name = os.path.basename(file_path)
                response = requests.post(
                    webhook_url,
                    data={"content": message},
                    files={"file": (file_name, f, "video/mp4")}
                )
            if response.status_code in [200, 204]:
                logger.info(f"Discord送信成功: {file_name}")
            else:
                logger.error(f"Discord送信失敗: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Discord送信中に例外発生: {e}")

    def _send_completion_notice(self, webhook_url: str, count: int):
        try:
            requests.post(webhook_url, data={"content": f"✅ {count}ファイルの送信が完了しました。"})
        except Exception:
            pass

# ==========================================
# Main
# ==========================================
def run_smart_timelapse_job(input_video: str) -> None:
    t_start = time.perf_counter()
    user_id = getattr(config, "LINE_USER_ID", "")
    if not check_dependencies(): return

    work, out, rec = setup_directories()
    try:
        info = get_video_info(input_video)
        start_dt = get_video_start_dt(input_video, info)
        duration = float(info.get('format', {}).get('duration', 0))
        if duration <= 0: raise ValueError("動画長不正")

        sum_info = SummaryInfo(target_date=start_dt.strftime('%Y-%m-%d'), ffmpeg_version=get_ffmpeg_version())
        
        records = MotionDetector().detect(input_video, work, duration)
        events = EventBuilder().build(records, work)
        
        if not events:
            send_push(user_id, [{"type": "text", "text": f"ℹ️ {sum_info.target_date} の動きなし"}], "discord", "report")
            return

        sum_info.output_path = os.path.join(out, os.path.basename(input_video).replace(".mp4", "_summary.mp4"))
        if os.path.exists(sum_info.output_path):
            try:
                os.remove(sum_info.output_path)
            except OSError as e:
                logger.warning(f"既存の出力ファイル削除に失敗しました: {e}")
            
        with tempfile.TemporaryDirectory() as temp_dir:
            if VideoBuilder().build(input_video, events, sum_info.output_path, temp_dir, start_dt):
                sum_info.total_processing_time = time.perf_counter() - t_start
                sum_info.events = len(events)
                sum_info.summary_duration = sum([e.duration for e in events])
                sum_info.file_size_bytes = os.path.getsize(sum_info.output_path)
                
                mark_as_done(rec, os.path.basename(input_video), sum_info)
                Uploader().split_and_send(sum_info, os.path.basename(input_video))
            
    except Exception as e:
        logger.error(f"エラー: {traceback.format_exc()}")
        send_push(user_id, [{"type": "text", "text": f"⚠️ エラー: {str(e)}"}], "discord", "error")

if __name__ == "__main__":
    if len(sys.argv) < 2 or not os.path.exists(sys.argv[1]): sys.exit(1)
    run_smart_timelapse_job(sys.argv[1])