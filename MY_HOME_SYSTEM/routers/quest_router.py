# MY_HOME_SYSTEM/routers/quest_router.py
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import datetime
import math
import importlib
import random
import common
import config

# import quest_data with fallback
try:
    import quest_data
except ImportError:
    from .. import quest_data

router = APIRouter()
logger = common.setup_logging("quest_router")

# --- Domain Models & Service Layer ---

class QuestService:
    """
    Questシステムのビジネスロジックとデータアクセスを担当するサービスクラス。
    """

    @staticmethod
    def calculate_next_level_exp(level: int) -> int:
        """レベルに応じた必要経験値を計算する (1.2乗カーブ)"""
        return math.floor(100 * math.pow(1.2, level - 1))

    @staticmethod
    def calculate_max_hp(level: int) -> int:
        """レベルに応じた最大HPを計算する"""
        return level * 20 + 5

    def sync_master_data(self) -> Dict[str, str]:
        """設定ファイル(quest_data.py)の内容をDBのマスタテーブルに同期する"""
        logger.info("🔄 Starting Master Data Sync...")
        try:
            importlib.reload(quest_data)
            # バリデーション用モデルへの変換（データ整合性チェック）
            valid_users = [MasterUser(**u) for u in quest_data.USERS]
            valid_quests = [MasterQuest(**q) for q in quest_data.QUESTS]
            valid_rewards = [MasterReward(**r) for r in quest_data.REWARDS]
        except Exception as e:
            logger.error(f"❌ Master Data Validation failed: {e}")
            raise HTTPException(status_code=500, detail=f"Master Data Error: {str(e)}")
        
        with common.get_db_cursor(commit=True) as cur:
            # 1. ユーザー同期
            for u in valid_users:
                cur.execute("""
                    INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, avatar, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        name = excluded.name,
                        job_class = excluded.job_class,
                        avatar = excluded.avatar
                """, (u.user_id, u.name, u.job_class, u.level, u.exp, u.gold, u.avatar, datetime.datetime.now()))
            
            # 2. クエスト同期
            for q in valid_quests:
                cur.execute("""
                    INSERT INTO quest_master (
                        quest_id, title, quest_type, target_user, exp_gain, gold_gain, 
                        icon_key, day_of_week, start_date, end_date, occurrence_chance
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(quest_id) DO UPDATE SET
                        title = excluded.title,
                        quest_type = excluded.quest_type,
                        target_user = excluded.target_user,
                        exp_gain = excluded.exp_gain,
                        gold_gain = excluded.gold_gain,
                        icon_key = excluded.icon_key
                """, (q.id, q.title, q.type, q.target, q.exp, q.gold, q.icon, q.days, q.start, q.end, q.chance))

            # 3. 報酬同期
            for r in valid_rewards:
                cur.execute("""
                    INSERT INTO reward_master (reward_id, title, category, cost_gold, icon_key)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(reward_id) DO UPDATE SET
                        title = excluded.title,
                        category = excluded.category,
                        cost_gold = excluded.cost_gold,
                        icon_key = excluded.icon_key
                """, (r.id, r.title, r.category, r.cost_gold, r.icon_key))

        return {"status": "synced", "message": "Master data updated successfully."}

    def get_all_view_data(self) -> Dict[str, Any]:
        """フロントエンド描画用の全データを取得する"""
        with common.get_db_cursor() as cur:
            # 1. Users
            users = [dict(row) for row in cur.execute("SELECT * FROM quest_users")]
            for u in users:
                u['nextLevelExp'] = self.calculate_next_level_exp(u['level'])
                u['maxHp'] = self.calculate_max_hp(u['level'])
                u['hp'] = u['maxHp']  # 現在HPはMaxHPと同じとする簡易仕様

            # 2. Quests (Filtering Logic)
            all_quests = [dict(row) for row in cur.execute("SELECT * FROM quest_master")]
            filtered_quests = self._filter_active_quests(all_quests)

            # 3. Rewards
            rewards = [dict(row) for row in cur.execute("SELECT * FROM reward_master")]
            for r in rewards:
                r['icon'] = r['icon_key']
                r['cost'] = r['cost_gold']

            # 4. History (Today)
            today_str = common.get_today_date_str()
            completed = [dict(row) for row in cur.execute(
                "SELECT * FROM quest_history WHERE date(completed_at) = ?", (today_str,)
            )]
            
            # 5. Logs
            logs = self._fetch_recent_logs(cur)

        return {
            "users": users,
            "quests": filtered_quests,
            "rewards": rewards,
            "completedQuests": completed,
            "logs": logs
        }

    def process_complete_quest(self, user_id: str, quest_id: int) -> Dict[str, Any]:
        """クエストを完了し、経験値とゴールドを付与する（トランザクション）"""
        with common.get_db_cursor(commit=True) as cur:
            # Check existence
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (quest_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()

            if not quest:
                raise HTTPException(status_code=404, detail="Quest not found")
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Calc
            current_level = user['level']
            added_exp = user['exp'] + quest['exp_gain']
            added_gold = user['gold'] + quest['gold_gain']
            
            new_level, new_exp, leveled_up = self._calc_level_up(current_level, added_exp)
            
            now_iso = common.get_now_iso()

            # Update DB
            cur.execute("""
                UPDATE quest_users 
                SET level = ?, exp = ?, gold = ?, updated_at = ? 
                WHERE user_id = ?
            """, (new_level, new_exp, added_gold, now_iso, user_id))
            
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, quest['quest_id'], quest['title'], quest['exp_gain'], quest['gold_gain'], now_iso))
            
            logger.info(f"Quest Completed: User={user_id}, Quest={quest['title']}, LvUp={leveled_up}")
            
            return {
                "status": "success",
                "leveledUp": leveled_up,
                "newLevel": new_level,
                "earnedGold": quest['gold_gain'],
                "earnedExp": quest['exp_gain']
            }

    def process_cancel_quest(self, user_id: str, history_id: int) -> Dict[str, str]:
        """完了したクエストを取り消す（トランザクション）"""
        with common.get_db_cursor(commit=True) as cur:
            # 履歴確認
            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist:
                raise HTTPException(status_code=404, detail="History not found")
                
            if hist['user_id'] != user_id:
                raise HTTPException(status_code=403, detail="User mismatch")

            # ユーザー取得
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            # 減算処理
            new_gold = max(0, user['gold'] - hist['gold_earned'])
            raw_exp_diff = user['exp'] - hist['exp_earned']
            
            new_level, new_exp = self._calc_level_down(user['level'], raw_exp_diff)
            
            # 更新
            cur.execute("UPDATE quest_users SET level=?, exp=?, gold=?, updated_at=? WHERE user_id=?", 
                        (new_level, new_exp, new_gold, common.get_now_iso(), user_id))
            
            # 履歴削除
            cur.execute("DELETE FROM quest_history WHERE id = ?", (history_id,))
            
            logger.info(f"Quest Cancelled: User={user_id}, HistoryID={history_id}")
        
        return {"status": "cancelled"}

    def process_purchase_reward(self, user_id: str, reward_id: int) -> Dict[str, Any]:
        """報酬を購入し、ゴールドを消費する（トランザクション）"""
        with common.get_db_cursor(commit=True) as cur:
            reward = cur.execute("SELECT * FROM reward_master WHERE reward_id = ?", (reward_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            
            if not reward:
                raise HTTPException(status_code=404, detail="Reward not found")
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
                
            if user['gold'] < reward['cost_gold']:
                raise HTTPException(status_code=400, detail="Not enough gold")
                
            # 購入処理
            new_gold = user['gold'] - reward['cost_gold']
            cur.execute("UPDATE quest_users SET gold = ?, updated_at = ? WHERE user_id = ?", 
                       (new_gold, common.get_now_iso(), user_id))
            
            cur.execute("""
                INSERT INTO reward_history (user_id, reward_id, reward_title, cost_gold, redeemed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, reward['reward_id'], reward['title'], reward['cost_gold'], common.get_now_iso()))
            
            logger.info(f"Reward Purchased: User={user_id}, Item={reward['title']}")
        
        return {"status": "purchased", "newGold": new_gold}

    # --- Internal Helpers ---

    def _filter_active_quests(self, quests: List[dict]) -> List[dict]:
        """現在有効なクエストのみをフィルタリングし、フォーマットを整える"""
        filtered = []
        today_str = common.get_today_date_str()
        
        for q in quests:
            # 期間限定チェック
            if q['quest_type'] == 'limited':
                if q['start_date'] and today_str < q['start_date']: continue
                if q['end_date'] and today_str > q['end_date']: continue
            
            # ランダム出現チェック (日付+IDをシードにする)
            if q['quest_type'] == 'random':
                seed = f"{today_str}_{q['quest_id']}"
                if random.Random(seed).random() > q['occurrence_chance']:
                    continue
            
            # フロントエンド互換マッピング
            q['icon'] = q['icon_key']
            q['type'] = q['quest_type']
            q['target'] = q['target_user']
            if q['day_of_week']:
                q['days'] = [int(d) for d in q['day_of_week'].split(',')]
            else:
                q['days'] = None
                
            filtered.append(q)
        return filtered

    def _fetch_recent_logs(self, cur) -> List[dict]:
        """クエストと報酬の履歴をマージして時系列順に取得"""
        q_logs = cur.execute("""
            SELECT id, user_id, quest_title as title, 'quest' as type, completed_at as ts 
            FROM quest_history ORDER BY id DESC LIMIT 50
        """).fetchall()
        
        r_logs = cur.execute("""
            SELECT id, user_id, reward_title as title, 'reward' as type, redeemed_at as ts 
            FROM reward_history ORDER BY id DESC LIMIT 50
        """).fetchall()
        
        all_logs = sorted(q_logs + r_logs, key=lambda x: x['ts'], reverse=True)[:50]
        
        # 名前解決
        user_map = {row['user_id']: row['name'] for row in cur.execute("SELECT user_id, name FROM quest_users")}
        formatted_logs = []
        
        for l in all_logs:
            name = user_map.get(l['user_id'], '誰か')
            ts_str = l['ts']
            date_str = ts_str.split('T')[0] if 'T' in ts_str else ts_str.split(' ')[0]
            
            text = ""
            if l['type'] == 'quest':
                text = f"{name}は {l['title']} をクリアした！"
            else:
                text = f"{name}は {l['title']} を手に入れた！"
                
            formatted_logs.append({
                "id": f"{l['type']}_{l['id']}",
                "text": text,
                "dateStr": date_str,
                "timestamp": ts_str
            })
        return formatted_logs

    def _calc_level_up(self, current_level: int, current_exp: int) -> tuple[int, int, bool]:
        """経験値加算後のレベル計算"""
        next_exp_req = self.calculate_next_level_exp(current_level)
        leveled_up = False
        
        while current_exp >= next_exp_req:
            current_exp -= next_exp_req
            current_level += 1
            leveled_up = True
            next_exp_req = self.calculate_next_level_exp(current_level)
            
        return current_level, current_exp, leveled_up

    def _calc_level_down(self, current_level: int, current_exp: int) -> tuple[int, int]:
        """経験値減算後のレベル計算 (キャンセル用)"""
        new_level = current_level
        new_exp = current_exp
        
        while new_exp < 0 and new_level > 1:
            new_level -= 1
            prev_level_max = self.calculate_next_level_exp(new_level)
            new_exp += prev_level_max
            
        if new_exp < 0:
            new_exp = 0  # Lv1でマイナスなら0丸め
            
        return new_level, new_exp


# --- Validation Models (Pydantic) ---

class MasterUser(BaseModel):
    user_id: str
    name: str
    job_class: str
    level: int = 1
    exp: int = 0
    gold: int = 50
    avatar: str = '🙂'

class MasterQuest(BaseModel):
    id: int
    title: str
    type: str  # 'daily', 'weekly', 'random', 'limited'
    target: str = 'all'
    exp: int
    gold: int
    icon: str
    days: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    chance: Optional[float] = 1.0

class MasterReward(BaseModel):
    id: int
    title: str
    category: str
    cost_gold: int
    icon_key: str

class UserAction(BaseModel):
    user_id: str

class QuestAction(BaseModel):
    user_id: str
    quest_id: int

class RewardAction(BaseModel):
    user_id: str
    reward_id: int

class HistoryAction(BaseModel):
    user_id: str
    history_id: int

# Responses
class SyncResponse(BaseModel):
    status: str
    message: str

class CompleteResponse(BaseModel):
    status: str
    leveledUp: bool
    newLevel: int
    earnedGold: int
    earnedExp: int

class CancelResponse(BaseModel):
    status: str

class PurchaseResponse(BaseModel):
    status: str
    newGold: int


# --- API Endpoints ---

service = QuestService()

@router.post("/sync_master", response_model=SyncResponse)
def sync_master_data():
    return service.sync_master_data()

@router.post("/seed", response_model=SyncResponse)
def seed_data():
    return service.sync_master_data()

@router.get("/data")
def get_all_data() -> Dict[str, Any]:
    try:
        return service.get_all_view_data()
    except Exception as e:
        logger.error(f"Data Fetch Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch data")

@router.post("/complete", response_model=CompleteResponse)
def complete_quest(action: QuestAction):
    return service.process_complete_quest(action.user_id, action.quest_id)

@router.post("/quest/cancel", response_model=CancelResponse)
def cancel_quest(action: HistoryAction):
    return service.process_cancel_quest(action.user_id, action.history_id)

@router.post("/reward/purchase", response_model=PurchaseResponse)
def purchase_reward(action: RewardAction):
    return service.process_purchase_reward(action.user_id, action.reward_id)