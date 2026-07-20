import os
import time
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from typing import List, Dict, Any
import config
from services import camera_service

router = APIRouter()

@router.get("/settings")
def get_camera_settings():
    """フロントエンドへ有効なカメラの一覧と設定を返す"""
    settings = []
    # devices.json からロードされた config.CAMERAS を使用
    for idx, cam in enumerate(config.CAMERAS):
        settings.append({
            "id": cam["id"],
            "name": cam["name"],
            "order": idx + 1,  # 配列の順序を表示順とする
            "enabled": True
        })
    return settings

@router.get("/live/{camera_id}/stream.m3u8")
def get_live_stream(camera_id: str):
    """ライブHLSプレイリスト（.m3u8）の取得"""
    cam_conf = next((c for c in config.CAMERAS if c["id"] == camera_id), None)
    if not cam_conf:
        raise HTTPException(status_code=404, detail="Camera not found")

    playlist_path = camera_service.start_hls_stream(cam_conf)
    
    if not playlist_path:
        raise HTTPException(status_code=500, detail="Failed to initialize stream")

    # ffmpegの初期セグメント生成を最大5秒待機
    for _ in range(10):
        if os.path.exists(playlist_path):
            return FileResponse(playlist_path, media_type="application/vnd.apple.mpegurl")
        time.sleep(0.5)

    raise HTTPException(status_code=503, detail="Stream generation timeout")

@router.get("/record/{camera_id}/{target_date}/info")
def get_record_info(camera_id: str, target_date: str):
    """指定日の録画ファイルのメタデータ（最初のファイルのオフセット秒数）を返す"""
    cam_conf = next((c for c in config.CAMERAS if c["id"] == camera_id), None)
    if not cam_conf:
        raise HTTPException(status_code=404, detail="Camera not found")

    offset = camera_service.get_record_start_offset(cam_conf, target_date)
    return {"offset_seconds": offset}

@router.get("/record/{camera_id}/{target_date}/{filename}")
def get_record_file(camera_id: str, target_date: str, filename: str):
    """録画VODのプレイリスト（.m3u8）またはセグメント（.ts）を配信"""
    # .m3u8 プレイリストの要求の場合
    if filename.endswith(".m3u8"):
        cam_conf = next((c for c in config.CAMERAS if c["id"] == camera_id), None)
        if not cam_conf:
            raise HTTPException(status_code=404, detail="Camera not found")

        playlist_path = camera_service.generate_record_playlist(cam_conf, target_date)
        if not playlist_path:
            raise HTTPException(status_code=404, detail="Recordings not found for the specified date")

        return FileResponse(playlist_path, media_type="application/vnd.apple.mpegurl")

    # .ts セグメントの要求の場合
    elif filename.endswith(".ts"):
        base_dir = camera_service.HLS_VOD_DIR
        # NASの元動画に日付フォルダは無いため、生成されたtsファイルも camera_id 直下のパスで解決する
        segment_path = os.path.join(base_dir, camera_id, filename)
        if not os.path.exists(segment_path):
            raise HTTPException(status_code=404, detail="Segment not found")
            
        return FileResponse(segment_path, media_type="video/MP2T")

    else:
        raise HTTPException(status_code=400, detail="Unsupported file extension")

@router.get("/live/{camera_id}/{segment_file}")
def get_live_segment(camera_id: str, segment_file: str):
    """ライブのHLSセグメント（.tsファイル）を配信"""
    base_dir = camera_service.HLS_LIVE_DIR
    segment_path = os.path.join(base_dir, camera_id, segment_file)
    if not os.path.exists(segment_path):
        raise HTTPException(status_code=404, detail="Segment not found")
        
    return FileResponse(segment_path, media_type="video/MP2T")