# MY_HOME_SYSTEM/routers/bounty_router.py
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel
from typing import List, Optional
import datetime
import common
import config

router = APIRouter()
logger = common.setup_logging("bounty_router")

# --- Constants ---
PARENTS = ['dad', 'mom']
CHILDREN = ['daughter', 'son', 'child']

# --- Pydantic Models ---

class BountyCreate(BaseModel):
    title: str
    description: Optional[str] = None
    reward_gold: int
    target_type: str  # 'ALL', 'ADULTS', 'CHILDREN', 'USER'
    target_user_id: Optional[str] = None
    created_by: str

class BountyAction(BaseModel):
    user_id: str

class BountyResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    reward_gold: int
    target_type: str
    target_user_id: Optional[str]
    status: str
    created_by: str
    assignee_id: Optional[str]
    created_at: str
    # UI表示用フラグ
    is_mine: bool            # 自分が作成したか
    is_assigned_to_me: bool  # 自分が受注したか
    can_accept: bool         # 今すぐ受注可能か

# --- Helper Logic ---

def is_target_match(user_id: str, target_type: str, target_user_id: Optional[str]) -> bool:
    """ユーザーが募集対象に含まれるか判定"""
    if target_type == 'ALL':
        return True
    if target_type == 'USER':
        return user_id == target_user_id
    if target_type == 'ADULTS':
        return user_id in PARENTS
    if target_type == 'CHILDREN':
        return user_id in CHILDREN
    return False

# --- Endpoints ---

@router.get("/list", response_model=List[BountyResponse])
def get_bounties(user_id: str = Query(..., description="アクセスしているユーザーID")):
    """
    掲示板に表示すべき依頼一覧を取得する。
    フィルタリング条件:
    1. 自分が作成したもの (進捗確認用)
    2. 自分が受注したもの (実行用)
    3. 自分に向けられた募集中のもの (受注候補)
    """
    with common.get_db_cursor() as cur:
        # 全件取得してからメモリ内でフィルタリング
        # (件数が数百件程度ならSQLを複雑にするより保守性が高い)
        rows = cur.execute("SELECT * FROM bounties ORDER BY created_at DESC").fetchall()
        
        filtered_bounties = []
        for row in rows:
            b = dict(row)
            
            # フラグ判定
            is_creator = (b['created_by'] == user_id)
            is_assignee = (b['assignee_id'] == user_id)
            is_open = (b['status'] == 'OPEN')
            
            # 募集対象チェック
            target_match = is_target_match(user_id, b['target_type'], b['target_user_id'])
            
            # 表示可否の決定
            should_show = False
            can_accept = False

            if is_creator or is_assignee:
                should_show = True
            
            if is_open and target_match:
                should_show = True
                # 自分が作成者でなければ受注可能
                if not is_creator:
                    can_accept = True
            
            if should_show:
                filtered_bounties.append(BountyResponse(
                    id=b['id'],
                    title=b['title'],
                    description=b['description'],
                    reward_gold=b['reward_gold'],
                    target_type=b['target_type'],
                    target_user_id=b['target_user_id'],
                    status=b['status'],
                    created_by=b['created_by'],
                    assignee_id=b['assignee_id'],
                    created_at=b['created_at'],
                    is_mine=is_creator,
                    is_assigned_to_me=is_assignee,
                    can_accept=can_accept
                ))
                
        return filtered_bounties

@router.post("/create")
def create_bounty(bounty: BountyCreate):
    """新しい依頼を作成する"""
    if bounty.reward_gold < 0:
        raise HTTPException(status_code=400, detail="報酬額は0以上で設定してください")

    with common.get_db_cursor(commit=True) as cur:
        now_iso = common.get_now_iso()
        
        cur.execute("""
            INSERT INTO bounties (
                title, description, reward_gold, target_type, target_user_id,
                status, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
        """, (
            bounty.title, bounty.description, bounty.reward_gold,
            bounty.target_type, bounty.target_user_id,
            bounty.created_by, now_iso, now_iso
        ))
        
        logger.info(f"🆕 Bounty Created: {bounty.title} by {bounty.created_by}")
        
    return {"status": "created"}

@router.post("/{bounty_id}/accept")
def accept_bounty(bounty_id: int, action: BountyAction):
    """依頼を受注する（早い者勝ち）"""
    with common.get_db_cursor(commit=True) as cur:
        # 1. 現状確認
        target = cur.execute("SELECT * FROM bounties WHERE id = ?", (bounty_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="依頼が見つかりません")
        
        if target['status'] != 'OPEN':
            raise HTTPException(status_code=409, detail="この依頼は既に締め切られています")
            
        if target['created_by'] == action.user_id:
             raise HTTPException(status_code=400, detail="自分の依頼は受注できません")

        # 2. 対象チェック
        if not is_target_match(action.user_id, target['target_type'], target['target_user_id']):
             raise HTTPException(status_code=403, detail="この依頼の対象ではありません")

        # 3. 更新実行 (排他制御: status='OPEN'を条件に含める)
        cur.execute("""
            UPDATE bounties 
            SET status = 'TAKEN', assignee_id = ?, updated_at = ?
            WHERE id = ? AND status = 'OPEN'
        """, (action.user_id, common.get_now_iso(), bounty_id))
        
        if cur.rowcount == 0:
             raise HTTPException(status_code=409, detail="タッチの差で他の人が受注しました")
            
        logger.info(f"🤝 Bounty Taken: ID={bounty_id} by {action.user_id}")
        
    return {"status": "taken", "assignee_id": action.user_id}


@router.post("/{bounty_id}/complete")
def complete_bounty(bounty_id: int, action: BountyAction):
    """受注者が完了報告をする"""
    with common.get_db_cursor(commit=True) as cur:
        # ステータスが TAKEN かつ、自分が受注者の場合のみ更新可能
        cur.execute("""
            UPDATE bounties 
            SET status = 'PENDING_APPROVAL', updated_at = ?
            WHERE id = ? AND status = 'TAKEN' AND assignee_id = ?
        """, (common.get_now_iso(), bounty_id, action.user_id))
        
        if cur.rowcount == 0:
             raise HTTPException(status_code=400, detail="完了報告に失敗しました（ステータス不整合または権限なし）")
            
        logger.info(f"🚩 Bounty Completed Report: ID={bounty_id} by {action.user_id}")
        
    return {"status": "pending_approval"}

@router.post("/{bounty_id}/approve")
def approve_bounty(bounty_id: int, action: BountyAction):
    """依頼主が承認し、報酬を支払う"""
    with common.get_db_cursor(commit=True) as cur:
        # 1. 依頼情報を取得
        bounty = cur.execute("SELECT * FROM bounties WHERE id = ?", (bounty_id,)).fetchone()
        if not bounty:
            raise HTTPException(status_code=404, detail="依頼が見つかりません")
            
        if bounty['status'] != 'PENDING_APPROVAL':
            raise HTTPException(status_code=400, detail="承認待ちの状態ではありません")
            
        if bounty['created_by'] != action.user_id:
            raise HTTPException(status_code=403, detail="依頼主のみが承認できます")

        # 2. ステータス更新 (COMPLETED)
        now = common.get_now_iso()
        cur.execute("""
            UPDATE bounties 
            SET status = 'COMPLETED', updated_at = ?, completed_at = ?
            WHERE id = ?
        """, (now, now, bounty_id))

        # 3. 報酬付与トランザクション (Assigneeにゴールド追加)
        assignee = bounty['assignee_id']
        reward = bounty['reward_gold']
        
        if assignee and reward > 0:
            cur.execute("""
                UPDATE users 
                SET gold = gold + ? 
                WHERE user_id = ?
            """, (reward, assignee))
            logger.info(f"💰 Reward Paid: {reward}G to {assignee}")

    return {"status": "completed", "reward_paid": reward}