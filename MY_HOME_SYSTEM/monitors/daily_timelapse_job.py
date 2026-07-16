import os
import sys
import datetime
import time
import glob
import json
import tempfile
import traceback
import argparse
import re
from pathlib import Path
from dataclasses import asdict

# プロジェクトルートの解決と追加
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import config
from core.logger import setup_logging
from services.notification_service import send_push

# コアエンジンから必要なクラス・関数をそのままインポート
from monitors.smart_timelapse_generator import (
    MotionDetector,
    EventBuilder,
    VideoBuilder,
    Uploader,
    SummaryInfo,
    get_video_info,
    get_video_start_dt,
    setup_directories,
    check_dependencies,
    get_ffmpeg_version
)

logger = setup_logging(__name__)

def parse_time(time_str: str) -> datetime.time:
    """HH:MM 形式の文字列を datetime.time オブジェクトに変換する"""
    if not time_str:
        return None
    time_str = time_str.replace(":", "")
    if len(time_str) == 4: # HHMM
        return datetime.time(int(time_str[0:2]), int(time_str[2:4]))
    elif len(time_str) == 6: # HHMMSS
        return datetime.time(int(time_str[0:2]), int(time_str[2:4]), int(time_str[4:6]))
    elif len(time_str) == 2: # HH
        return datetime.time(int(time_str[0:2]), 0)
    else:
        raise ValueError(f"時刻のフォーマットが不正です。HH:MM形式で指定してください: {time_str}")

def run_daily_timelapse(camera_name: str, target_date_str: str = None, start_time_str: str = None, end_time_str: str = None) -> None:
    t_start = time.perf_counter()
    user_id = getattr(config, "LINE_USER_ID", "")
    
    if not check_dependencies():
        send_push(
            user_id=user_id, 
            messages=[{"type": "text", "text": "⚠️ 日次タイムラプス生成エラー\nFFmpeg等がインストールされていません。"}], 
            target="discord", 
            channel="error"
        )
        return

    # ターゲット日付の決定（指定がなければ昨日の日付）
    if not target_date_str:
        target_date = datetime.date.today() - datetime.timedelta(days=1)
        target_date_str = target_date.strftime('%Y-%m-%d')
    else:
        try:
            target_date = datetime.datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.error(f"日付フォーマットが不正です(YYYY-MM-DD): {target_date_str}")
            return

    time_range_log = f" (時間指定: {start_time_str or '開始'} ～ {end_time_str or '終了'})" if start_time_str or end_time_str else ""
    logger.info(f"==========【日次タイムラプスバッチ開始】==========")
    logger.info(f"カメラ: {camera_name}, 対象日: {target_date_str}{time_range_log}")

    # NASのNVR録画ディレクトリパス
    nvr_dir = f"/mnt/nas/home_system/nvr_recordings/{camera_name}"
    if not os.path.exists(nvr_dir):
        logger.error(f"カメラディレクトリが見つかりません: {nvr_dir}")
        return

    # YYYYMMDD形式でファイルを検索し、時系列にソート
    date_prefix = target_date.strftime('%Y%m%d')
    target_files = sorted(glob.glob(os.path.join(nvr_dir, f"{date_prefix}_*.mp4")))
    
    # --- 時間帯フィルタリング処理 ---
    if target_files and (start_time_str or end_time_str):
        try:
            start_time = parse_time(start_time_str) if start_time_str else None
            end_time = parse_time(end_time_str) if end_time_str else None
        except ValueError as ve:
            logger.error(str(ve))
            return
            
        filter_start_dt = datetime.datetime.combine(target_date, start_time) if start_time else datetime.datetime.min
        filter_end_dt = datetime.datetime.combine(target_date, end_time) if end_time else datetime.datetime.max
        
        filtered_files = []
        for f in target_files:
            base_name = os.path.basename(f)
            m = re.search(r'\d{8}_(\d{6})\.mp4', base_name)
            if m:
                t_str = m.group(1)
                file_start_time = datetime.time(int(t_str[0:2]), int(t_str[2:4]), int(t_str[4:6]))
                file_start_dt = datetime.datetime.combine(target_date, file_start_time)
                # チャンクの最大長を安全を見て15分と仮定し、終了時刻を算出
                file_end_dt = file_start_dt + datetime.timedelta(minutes=15)
                
                # 重複判定: (ファイル開始 < フィルタ終了) AND (ファイル終了 > フィルタ開始)
                # 例: 05:56開始でも、06:00以降に被っていれば対象に含める
                if file_start_dt < filter_end_dt and file_end_dt > filter_start_dt:
                    filtered_files.append(f)
        
        target_files = filtered_files

    if not target_files:
        logger.info(f"対象期間の録画ファイルが存在しません。処理を終了します。")
        return

    logger.info(f"対象チャンクファイル数: {len(target_files)} 件")

    # コアエンジンのディレクトリセットアップを流用
    work, out, rec = setup_directories()

    sum_info = SummaryInfo(
        target_date=target_date_str,
        ffmpeg_version=get_ffmpeg_version()
    )

    # 各処理エンジンのインスタンス化
    motion_detector = MotionDetector()
    event_builder = EventBuilder()
    video_builder = VideoBuilder()
    uploader = Uploader()

    all_clip_files = []
    global_event_idx = 0
    total_event_duration = 0

    # 出力ファイル名に時間帯を付与: 例 entrance_2026-07-15_0600-0900_summary.mp4
    time_suffix = ""
    if start_time_str or end_time_str:
        s_str = start_time.strftime('%H%M') if start_time else "start"
        e_str = end_time.strftime('%H%M') if end_time else "end"
        time_suffix = f"_{s_str}-{e_str}"

    sum_info.output_path = os.path.join(out, f"{camera_name}_{target_date_str}{time_suffix}_summary.mp4")
    if os.path.exists(sum_info.output_path):
        try:
            os.remove(sum_info.output_path)
        except OSError:
            pass

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # チャンクファイルを順番に解析
            for filepath in target_files:
                time.sleep(1)
                base_name = os.path.basename(filepath)
                logger.info(f"--- チャンク処理開始: {base_name} ---")
                
                info = get_video_info(filepath)
                start_dt = get_video_start_dt(filepath, info)
                duration = float(info.get('format', {}).get('duration', 0))
                
                if duration <= 0:
                    logger.warning(f"動画長が不正なためスキップします: {base_name}")
                    continue

                # 1. 動き検知
                records = motion_detector.detect(filepath, work, duration)
                
                # 2. イベント構築
                events = event_builder.build(records, work)
                
                # CSVファイルの退避 (次チャンクでの上書きを防止)
                file_no_ext = os.path.splitext(base_name)[0]
                for csv_name in ["motion.csv", "events.csv", "events_enriched.csv"]:
                    src_csv = os.path.join(work, csv_name)
                    if os.path.exists(src_csv):
                        dst_csv = os.path.join(work, f"{os.path.splitext(csv_name)[0]}_{file_no_ext}.csv")
                        os.rename(src_csv, dst_csv)
                
                if events:
                    # 3. 各イベントごとにクリップを切り出し
                    for ev in events:
                        global_event_idx += 1
                        # 1日通して一意のEvent IDを再採番 (Event001, Event002...)
                        ev.event_id = f"Event{global_event_idx:03d}"
                        total_event_duration += ev.duration
                        
                        # エンジンの隠蔽メソッドを直接利用してクリップを生成
                        clip_path = video_builder._build_clip(filepath, ev, temp_dir, start_dt)
                        if clip_path:
                            all_clip_files.append(clip_path)

            # 全ファイルの解析ループ終了
            if not all_clip_files:
                logger.info(f"{camera_name} の対象期間内 ({target_date_str}{time_range_log}) に動き検知イベントはありませんでした。")
                send_push(
                    user_id=user_id,
                    messages=[{"type": "text", "text": f"ℹ️ {camera_name} ({target_date_str}{time_range_log}) の動きはありませんでした。"}],
                    target="discord",
                    channel="report"
                )
                return

            logger.info(f"全 {len(target_files)} ファイルの解析完了。有効クリップ総数: {len(all_clip_files)} 件")
            logger.info(f"クリップの一括結合と全体サムネイル生成を開始します...")
            
            # 4. 全クリップを1本の動画に結合
            if video_builder._build_concat(all_clip_files, sum_info.output_path, temp_dir):
                video_builder._generate_thumbnail(sum_info.output_path)
                logger.info(f"日次タイムラプス動画の生成完了: {sum_info.output_path}")
                
                # サマリー情報の更新
                sum_info.total_processing_time = time.perf_counter() - t_start
                sum_info.events = global_event_idx
                sum_info.summary_duration = total_event_duration
                sum_info.file_size_bytes = os.path.getsize(sum_info.output_path)
                
                # 完了ファイルの保存
                done_filename = f"{camera_name}_{target_date_str}{time_suffix}"
                record_file = os.path.join(rec, f"{done_filename}.done")
                with open(record_file, "w", encoding="utf-8") as f:
                    json.dump(asdict(sum_info), f, indent=2, ensure_ascii=False)
                
                # 5. Discordへ一括アップロード
                base_filename = os.path.basename(sum_info.output_path)
                uploader.split_and_send(sum_info, base_filename)
                
            else:
                logger.error("クリップの一括結合フェーズでエラーが発生しました。")
                
    except Exception as e:
        err_msg = traceback.format_exc()
        logger.error(f"日次バッチ処理中に予期せぬエラーが発生しました: {err_msg}")
        send_push(
            user_id=user_id,
            messages=[{"type": "text", "text": f"⚠️ 日次タイムラプス生成エラー ({camera_name})\n{str(e)}"}],
            target="discord",
            channel="error"
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="日次/時間指定タイムラプスバッチ処理")
    parser.add_argument("camera", help="カメラ名 (例: entrance)")
    parser.add_argument("--date", help="対象日 YYYY-MM-DD (デフォルト: 昨日)", default=None)
    parser.add_argument("--start", help="開始時刻 HH:MM (例: 06:00)", default=None)
    parser.add_argument("--end", help="終了時刻 HH:MM (例: 09:00)", default=None)
    
    args = parser.parse_args()
    
    run_daily_timelapse(
        camera_name=args.camera, 
        target_date_str=args.date, 
        start_time_str=args.start, 
        end_time_str=args.end
    )