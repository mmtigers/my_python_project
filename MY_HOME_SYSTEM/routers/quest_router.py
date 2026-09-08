# MY_HOME_SYSTEM/routers/quest_router.py
from fastapi import APIRouter, HTTPException, File, UploadFile
from typing import Dict, Any, Optional
import os
import sys

import config
from core import sound_manager
from core.logger import setup_logging

# 分離したモジュールをインポート
from models.quest import (
    SyncResponse, CompleteResponse, CancelResponse, PurchaseResponse, UseItemResponse,
    QuestAction, ApproveAction, HistoryAction, RewardAction,
    UpdateUserAction, SoundTestRequest, UseItemAction, ResetUserAction, ResetUserResponse
)
from services.quest_service import (
    game_system, quest_service, shop_service, user_service, inventory_service,
    ImageTooLargeError, InvalidImageError,
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
def get_all_data(viewer_user_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        return game_system.get_all_view_data(viewer_user_id)
    except HTTPException:
        # #409: 以前は HTTPException まで 500 に潰していた
        raise
    except Exception:
        # #409: logger.error(f"{e}") ではスタックトレースが失われ原因調査ができなかった
        logger.exception("Data Fetch Error")
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@router.post("/complete", response_model=CompleteResponse)
def complete_quest(action: QuestAction):
    return quest_service.process_complete_quest(action.user_id, action.quest_id)

@router.post("/approve", response_model=CompleteResponse)
def approve_quest(action: ApproveAction):
    return quest_service.process_approve_quest(action.approver_id, action.history_id)

@router.post("/reject", response_model=CancelResponse)
def reject_quest(action: ApproveAction):
    return quest_service.process_reject_quest(action.approver_id, action.history_id, action.reason)

@router.post("/quest/cancel", response_model=CancelResponse)
def cancel_quest(action: HistoryAction):
    return quest_service.process_cancel_quest(action.user_id, action.history_id)

@router.post("/reward/purchase", response_model=PurchaseResponse)
def purchase_reward(action: RewardAction):
    return shop_service.process_purchase_reward(action.user_id, action.reward_id)

@router.get("/family/chronicle")
def get_family_chronicle():
    return user_service.get_family_chronicle()

@router.post("/seed", response_model=SyncResponse)
def seed_data_endpoint():
    return game_system.sync_master_data()

@router.post("/user/update")
def update_user_avatar(action: UpdateUserAction):
    return user_service.update_avatar(action.user_id, action.avatar_url)

# Issue #547: reset_game.py が別プロセスから直接DBを書き換えていたユーザーリセットを
# サーバーAPI経由に置き換える。権限チェック(admin_idがrole_adultか)はProcessApproveQuest等
# と同様サービス層(UserService.reset_user_data)で行う。
@router.post("/admin/reset_user", response_model=ResetUserResponse)
def reset_user(action: ResetUserAction):
    return user_service.reset_user_data(action.admin_id, action.target_user_id)

# #442: AvatarUploader.tsxの2段階アップロード(画像アップロード→ユーザーへの紐付け)の
# うち2段階目が失敗した際、1段階目でアップロード済みの画像をロールバック削除するための
# エンドポイント。まだ紐付いていない自分自身のアップロード直後の画像のみが対象になる
# よう、削除前にどのユーザーにも参照されていないことをサービス層で確認する。
# ベストエフォートの後始末のため、削除できなくても(既に無い/他ユーザーが参照中等)
# エラーにはせず状態を返すのみとする。
@router.delete("/upload/{filename}")
def delete_uploaded_image(filename: str):
    deleted = user_service.delete_unlinked_avatar(filename)
    return {"status": "deleted" if deleted else "skipped"}

@router.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    try:
        url = await user_service.save_avatar_image(file)
        return {"url": url}
    except InvalidImageError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ImageTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except Exception:
        logger.exception("Upload failed")
        raise HTTPException(status_code=500, detail="画像の保存に失敗しました")


@router.post("/test_sound")
def test_sound(req: SoundTestRequest):
    if req.sound_key not in config.SOUND_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid sound key. Options: {list(config.SOUND_MAP.keys())}")

    sound_manager.play(req.sound_key)
    return {"status": "playing", "key": req.sound_key}

@router.get("/inventory/{user_id}")
def get_inventory(user_id: str):
    return inventory_service.get_user_inventory(user_id)

@router.post("/inventory/use", response_model=UseItemResponse)
def use_item(action: UseItemAction):
    return inventory_service.use_item(action.user_id, action.inventory_id)