# MY_HOME_SYSTEM/routers/quest_router.py
from fastapi import APIRouter, HTTPException, File, UploadFile
from typing import Dict, Any
import os
import uuid
import sys
import aiofiles

import common
import config
import sound_manager
from core.logger import setup_logging

# 分離したモジュールをインポート
from models.quest import (
    SyncResponse, CompleteResponse, CancelResponse, PurchaseResponse, UseItemResponse,
    QuestAction, ApproveAction, HistoryAction, RewardAction, EquipAction, 
    UpdateUserAction, SoundTestRequest, AdminBossUpdate, UseItemAction, ConsumeItemAction, WeeklyReportResponse
)
from services.quest_service import (
    game_system, quest_service, shop_service, user_service, inventory_service
)

# プロジェクトルート解決（念のため維持）
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

router = APIRouter()
logger = setup_logging("quest_router")

# ==========================================
# API Endpoints (Controller)
# ==========================================

@router.post("/sync_master", response_model=SyncResponse)
def sync_master_data():
    return game_system.sync_master_data()

@router.get("/data")
def get_all_data() -> Dict[str, Any]:
    try:
        return game_system.get_all_view_data()
    except Exception as e:
        logger.error(f"Data Fetch Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@router.post("/complete", response_model=CompleteResponse)
def complete_quest(action: QuestAction):
    return quest_service.process_complete_quest(action.user_id, action.quest_id)

@router.post("/approve", response_model=CompleteResponse)
def approve_quest(action: ApproveAction):
    return quest_service.process_approve_quest(action.approver_id, action.history_id)

@router.post("/reject", response_model=CancelResponse)
def reject_quest(action: ApproveAction):
    return quest_service.process_reject_quest(action.approver_id, action.history_id)

@router.post("/quest/cancel", response_model=CancelResponse)
def cancel_quest(action: HistoryAction):
    return quest_service.process_cancel_quest(action.user_id, action.history_id)

@router.post("/reward/purchase", response_model=PurchaseResponse)
def purchase_reward(action: RewardAction):
    return shop_service.process_purchase_reward(action.user_id, action.reward_id)

@router.post("/equip/purchase", response_model=PurchaseResponse)
def purchase_equipment(action: EquipAction):
    return shop_service.process_purchase_equipment(action.user_id, action.equipment_id)

@router.post("/equip/change")
def change_equipment(action: EquipAction):
    return shop_service.process_change_equipment(action.user_id, action.equipment_id)

@router.get("/family/chronicle")
def get_family_chronicle():
    return user_service.get_family_chronicle()

# Initialization alias
def seed_data():
    return game_system.sync_master_data()

@router.post("/seed", response_model=SyncResponse)
def seed_data_endpoint():
    return game_system.sync_master_data()

@router.post("/user/update")
def update_user_avatar(action: UpdateUserAction):
    return user_service.update_avatar(action.user_id, action.avatar_url)

# Image Upload Helper
def validate_image_header(header: bytes) -> bool:
    if header.startswith(b'\xff\xd8\xff'): return True
    if header.startswith(b'\x89PNG\r\n\x1a\n'): return True
    if header.startswith(b'GIF87a') or header.startswith(b'GIF89a'): return True
    if header.startswith(b'RIFF') and header[8:12] == b'WEBP': return True
    return False

@router.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    try:
        allowed_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in allowed_extensions:
            raise HTTPException(status_code=400, detail="許可されていないファイル形式です(拡張子)")

        header = await file.read(12)
        if not validate_image_header(header):
            logger.warning(f"Invalid file header detected. Ext: {file_ext}")
            raise HTTPException(status_code=400, detail="ファイルの内容が画像として認識できません")
        
        await file.seek(0)
        new_filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(config.UPLOAD_DIR, new_filename)

        async with aiofiles.open(file_path, "wb") as buffer:
            while content := await file.read(1024 * 1024):
                await buffer.write(content)
            
        logger.info(f"Image Uploaded: {new_filename}")
        return {"url": f"/uploads/{new_filename}"}

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail="画像の保存に失敗しました")
    
@router.post("/test_sound")
def test_sound(req: SoundTestRequest):
    if req.sound_key not in config.SOUND_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid sound key. Options: {list(config.SOUND_MAP.keys())}")
    
    sound_manager.play(req.sound_key)
    return {"status": "playing", "key": req.sound_key}

@router.post("/admin/boss/update")
def admin_update_boss(action: AdminBossUpdate):
    """管理画面からボスのステータスを直接変更する"""
    with common.get_db_cursor(commit=True) as cur:
        updates = []
        params = []
        
        if action.max_hp is not None:
            updates.append("max_hp = ?")
            params.append(action.max_hp)
            
        if action.current_hp is not None:
            updates.append("current_hp = ?")
            params.append(action.current_hp)
            
        if action.is_defeated is not None:
            updates.append("is_defeated = ?")
            params.append(1 if action.is_defeated else 0)
            
        if not updates:
            return {"status": "no_change"}
            
        updates.append("updated_at = ?")
        params.append(common.get_now_iso())
        
        sql = f"UPDATE party_state SET {', '.join(updates)} WHERE id = 1"
        cur.execute(sql, tuple(params))
        
        logger.info(f"👮 Admin Boss Update: {action.dict()}")
        
    return {"status": "updated"}

@router.get("/inventory/{user_id}")
def get_inventory(user_id: str):
    return inventory_service.get_user_inventory(user_id)

@router.post("/inventory/use", response_model=UseItemResponse)
def use_item(action: UseItemAction):
    return inventory_service.use_item(action.user_id, action.inventory_id)

@router.post("/inventory/consume")
def consume_item(action: ConsumeItemAction):
    return inventory_service.consume_item(action.approver_id, action.inventory_id)

@router.post("/inventory/cancel")
def cancel_item_usage(action: UseItemAction):
    return inventory_service.cancel_usage(action.user_id, action.inventory_id)

@router.get("/inventory/admin/pending")
def get_admin_pending_inventory():
    return inventory_service.get_pending_items()

@router.get("/analytics/weekly")
def get_weekly_analytics():
    return quest_service.get_weekly_analytics()