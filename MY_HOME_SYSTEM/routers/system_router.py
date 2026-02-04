# MY_HOME_SYSTEM/routers/system_router.py
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from services import backup_service

router = APIRouter()

@router.post("/backup")
async def manual_backup() -> Dict[str, Any]:
    """手動バックアップトリガー"""
    success, msg, size = backup_service.perform_backup()
    if not success: 
        raise HTTPException(status_code=500, detail=msg)
    return {"status": "success", "message": msg, "size_mb": size}