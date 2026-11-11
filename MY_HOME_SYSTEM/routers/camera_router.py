import asyncio
import os
import re
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import config
from services import camera_service, record_playlist_service

router = APIRouter()


class CameraSettingsUpdate(BaseModel):
    enabled: bool


def _require_camera(camera_id: str) -> Dict[str, Any]:
    """camera_idに対応するカメラ設定を返し、無ければ404を送出する(#551)。
    5箇所に重複していた `next((c for c in config.CAMERAS if c["id"] == camera_id), None)`
    + 404送出を一元化したもの。"""
    cam_conf = camera_service.get_camera_config_or_none(camera_id)
    if not cam_conf:
        raise HTTPException(status_code=404, detail="Camera not found")
    return cam_conf


def _resolve_segment_path(base_dir: str, camera_id: str, filename: str) -> str:
    """
    base_dir/camera_id/filename を解決し、パストラバーサル（`..`等）で
    base_dir の外側に出ていないことを確認したうえで絶対パスを返す。
    範囲外と判定された場合は 400 を送出する。
    """
    candidate = os.path.join(base_dir, camera_id, filename)
    resolved_base = os.path.realpath(base_dir)
    resolved_candidate = os.path.realpath(candidate)

    if os.path.commonpath([resolved_base, resolved_candidate]) != resolved_base:
        raise HTTPException(status_code=400, detail="Invalid path")

    return resolved_candidate


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
            "enabled": cam.get("enabled", True)
        })
    return settings

@router.put("/settings/{camera_id}")
def update_camera_settings(camera_id: str, payload: CameraSettingsUpdate):
    """カメラの有効/無効を切り替え、devices.json に永続化する"""
    if not camera_service.set_camera_enabled(camera_id, payload.enabled):
        raise HTTPException(status_code=404, detail="Camera not found")

    return {"id": camera_id, "enabled": payload.enabled}

@router.get("/live/{camera_id}/stream.m3u8")
async def get_live_stream(camera_id: str):
    """ライブHLSプレイリスト（.m3u8）の取得"""
    cam_conf = _require_camera(camera_id)

    # #457: start_hls_stream はffmpeg起動等のブロッキング処理を含むため、
    # イベントループを止めないようスレッドプールへオフロードする。
    playlist_path = await asyncio.to_thread(camera_service.start_hls_stream, cam_conf)

    if not playlist_path:
        raise HTTPException(status_code=500, detail="Failed to initialize stream")

    # ffmpegの初期セグメント生成を最大5秒待機。
    # #457: 以前はtime.sleep(同期)でこの間ワーカースレッドを占有し続け、
    # 同時アクセス集中時に他リクエストの遅延要因になっていた。asyncio.sleepに
    # 変更し、待機中はイベントループを他のリクエスト処理に譲る。
    for _ in range(10):
        if os.path.exists(playlist_path):
            return FileResponse(playlist_path, media_type="application/vnd.apple.mpegurl")
        await asyncio.sleep(0.5)

    raise HTTPException(status_code=503, detail="Stream generation timeout")

# #386: target_date は camera_service 側で glob パターン(f"{target_date}_*.mp4")と
# concat ファイル名にそのまま埋め込まれる。未検証だと "*" で全期間(30日×144本)を
# 1本に結合する数時間の ffmpeg ジョブを外部から起動できたため、YYYYMMDD の8桁のみ許可する。
_TARGET_DATE_RE = re.compile(r"^\d{8}$")


def _validate_target_date(target_date: str) -> None:
    if not _TARGET_DATE_RE.match(target_date):
        raise HTTPException(status_code=400, detail="target_date must be YYYYMMDD")


@router.get("/record/{camera_id}/{target_date}/info")
def get_record_info(camera_id: str, target_date: str):
    """指定日の録画のメタデータを返す。

    - offset_seconds: 最初のファイルの開始時刻(0時からの秒数)
    - files: 録画ファイルごとの開始時刻(0時からの秒数)・プレイリスト上の位置・長さ(秒)。
      Issue #1091: 録画が欠けている時間帯はプレイリスト上で詰まるため、フロントエンドは
      「指定時刻 → 再生位置」をこの対応表で変換する。
    """
    _validate_target_date(target_date)
    cam_conf = _require_camera(camera_id)

    offset = camera_service.get_record_start_offset(cam_conf, target_date)
    files = record_playlist_service.get_record_timeline(cam_conf, target_date)
    return {"offset_seconds": offset, "files": files}

_RECORD_PART_RE = re.compile(r"^(\d{1,12})-(\d{1,12})$")


@router.get("/record/{camera_id}/{target_date}/{filename}")
def get_record_file(camera_id: str, target_date: str, filename: str, part: Optional[str] = None):
    """録画VODのプレイリスト（.m3u8）または NAS 上の録画 MP4 の一部（.mp4?part=開始-長さ）を配信する。

    Issue #1091: 以前は ffmpeg で丸1日分を HLS(.ts) 化して SD カードへ書き出していたが、
    NAS 上の fragmented MP4 を直接参照するプレイリストに変えた。MP4 はプレイリストが示す
    範囲(初期化セグメントかセグメント1つ)だけを返し、ブラウザ(MSE)が受け付けるよう
    moof の見出しを書き換える(record_playlist_service.read_record_part)。
    """
    _validate_target_date(target_date)
    cam_conf = _require_camera(camera_id)

    if filename.endswith(".m3u8"):
        playlist = record_playlist_service.get_record_playlist(cam_conf, target_date)
        if not playlist:
            raise HTTPException(status_code=404, detail="Recordings not found for the specified date")
        # 当日分は録画が増えるため、ブラウザにキャッシュさせない
        return Response(content=playlist, media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-cache"})

    elif filename.endswith(".mp4"):
        m = _RECORD_PART_RE.match(part or "")
        if m is None:
            raise HTTPException(status_code=400, detail="part=<offset>-<length> is required")
        data = record_playlist_service.read_record_part(
            cam_conf, target_date, filename, int(m.group(1)), int(m.group(2)))
        if data is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        # 範囲は索引のセグメントそのもので、書き終わった部分は変わらない
        return Response(content=data, media_type="video/mp4",
                        headers={"Cache-Control": "private, max-age=86400"})

    else:
        raise HTTPException(status_code=400, detail="Unsupported file extension")

@router.get("/live/{camera_id}/{segment_file}")
def get_live_segment(camera_id: str, segment_file: str):
    """ライブのHLSセグメント（.tsファイル）を配信"""
    # _resolve_segment_pathはパストラバーサルのみを検査し拡張子は見ないため、
    # ここで明示的に許可拡張子を絞らないと、同ディレクトリ内のffmpeg.log
    # （RTSP認証情報を含みうる）等がそのまま配信されてしまう。
    if not segment_file.endswith(".ts"):
        raise HTTPException(status_code=400, detail="Unsupported file extension")

    _require_camera(camera_id)

    segment_path = _resolve_segment_path(camera_service.HLS_LIVE_DIR, camera_id, segment_file)
    if not os.path.exists(segment_path):
        raise HTTPException(status_code=404, detail="Segment not found")

    return FileResponse(segment_path, media_type="video/MP2T")