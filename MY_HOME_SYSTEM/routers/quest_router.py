# MY_HOME_SYSTEM/routers/quest_router.py
from fastapi import APIRouter, HTTPException, status
from fastapi import File, UploadFile
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import datetime
import shutil # 追加
import os # 既存だが確認
import uuid # 追加
import math
import pytz
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

# ==========================================
# 1. Domain Models (Pydantic)
# ==========================================

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
    start_time: Optional[str] = None
    end_time: Optional[str] = None

class MasterReward(BaseModel):
    id: int
    title: str
    category: str
    cost_gold: int
    icon_key: str

class MasterEquipment(BaseModel):
    id: int
    name: str
    type: str
    power: int
    cost: int
    icon: str

# Request Models
class UserAction(BaseModel):
    user_id: str

class QuestAction(BaseModel):
    user_id: str
    quest_id: int

class RewardAction(BaseModel):
    user_id: str
    reward_id: int

class EquipAction(BaseModel):
    user_id: str
    equipment_id: int

class HistoryAction(BaseModel):
    user_id: str
    history_id: int

class ApproveAction(BaseModel):
    approver_id: str
    history_id: int

# Response Models
class SyncResponse(BaseModel):
    status: str
    message: str

class CompleteResponse(BaseModel):
    status: str # 'success' or 'pending'
    leveledUp: bool
    newLevel: int
    earnedGold: int
    earnedExp: int
    earnedMedals: int = 0
    message: Optional[str] = None

class CancelResponse(BaseModel):
    status: str

class PurchaseResponse(BaseModel):
    status: str
    newGold: int


# ==========================================
# 2. Service Layers (Logic Separation)
# ==========================================

class UserService:
    """ユーザーのステータス計算、レベル管理、ログ取得を担当"""

    @staticmethod
    def calculate_next_level_exp(level: int) -> int:
        return math.floor(100 * math.pow(1.2, level - 1))

    @staticmethod
    def calculate_max_hp(level: int) -> int:
        return level * 20 + 5

    def calc_level_up(self, current_level: int, current_exp: int) -> tuple[int, int, bool]:
        next_exp_req = self.calculate_next_level_exp(current_level)
        leveled_up = False
        while current_exp >= next_exp_req:
            current_exp -= next_exp_req
            current_level += 1
            leveled_up = True
            next_exp_req = self.calculate_next_level_exp(current_level)
        return current_level, current_exp, leveled_up

    def calc_level_down(self, current_level: int, current_exp: int) -> tuple[int, int]:
        new_level = current_level
        new_exp = current_exp
        while new_exp < 0 and new_level > 1:
            new_level -= 1
            prev_level_max = self.calculate_next_level_exp(new_level)
            new_exp += prev_level_max
        if new_exp < 0: new_exp = 0
        return new_level, new_exp

    def get_family_chronicle(self) -> Dict[str, Any]:
        with common.get_db_cursor() as cur:
            users = cur.execute("SELECT level, gold FROM quest_users").fetchall()
            total_level = sum(u['level'] for u in users)
            total_gold = sum(u['gold'] for u in users)
            total_quests = cur.execute("SELECT COUNT(*) as count FROM quest_history").fetchone()['count']
            
            if total_level < 10: rank = "駆け出しの家族"
            elif total_level < 30: rank = "新進気鋭のパーティ"
            elif total_level < 60: rank = "熟練のクラン"
            else: rank = "伝説のギルド"

            logs = self._fetch_full_adventure_logs(cur)

        return {
            "stats": {"totalLevel": total_level, "totalGold": total_gold, "totalQuests": total_quests, "partyRank": rank},
            "chronicle": logs
        }

    def _fetch_full_adventure_logs(self, cur) -> List[dict]:
        # status = 'approved' のものだけを取得
        q_rows = cur.execute("SELECT 'quest' as type, user_id, quest_title as title, gold_earned as gold, exp_earned as exp, completed_at as ts FROM quest_history WHERE status='approved' ORDER BY completed_at DESC LIMIT 100").fetchall()
        r_rows = cur.execute("SELECT 'reward' as type, user_id, reward_title as title, cost_gold as gold, 0 as exp, redeemed_at as ts FROM reward_history ORDER BY redeemed_at DESC LIMIT 100").fetchall()
        e_rows = cur.execute("""
            SELECT 'equip' as type, ue.user_id, em.name as title, em.cost_gold as gold, 0 as exp, ue.acquired_at as ts 
            FROM user_equipments ue JOIN equipment_master em ON ue.equipment_id = em.equipment_id ORDER BY acquired_at DESC LIMIT 100
        """).fetchall()

        all_events = sorted(q_rows + r_rows + e_rows, key=lambda x: x['ts'], reverse=True)[:100]
        user_info = {row['user_id']: {"name": row['name'], "avatar": row['avatar']} for row in cur.execute("SELECT user_id, name, avatar FROM quest_users")}

        formatted = []
        for ev in all_events:
            u = user_info.get(ev['user_id'], {"name": "旅人", "avatar": "👤"})
            text = ""
            if ev['type'] == 'quest': text = f"{u['name']}は {ev['title']} を達成した！"
            elif ev['type'] == 'reward': text = f"{u['name']}は {ev['title']} を獲得した！"
            elif ev['type'] == 'equip': text = f"{u['name']}は {ev['title']} を購入した！"

            formatted.append({
                "type": ev['type'], "userName": u['name'], "userAvatar": u['avatar'],
                "title": ev['title'], "text": text, "gold": ev['gold'], "exp": ev['exp'],
                "timestamp": ev['ts'],
                "dateStr": ev['ts'].split('T')[0] if 'T' in ev['ts'] else ev['ts'].split(' ')[0]
            })
        return formatted


class QuestService:
    """クエストの進行管理、完了/キャンセル、承認フローを担当"""
    
    # 承認が必要なユーザーID
    CHILDREN_IDS = ['son', 'daughter']
    # 承認権限を持つユーザーID
    PARENT_IDS = ['dad', 'mom']

    def __init__(self):
        self.user_service = UserService()

    def process_complete_quest(self, user_id: str, quest_id: int) -> Dict[str, Any]:
        """クエストを完了する（子供の場合は承認待ちステータスにする）"""
        with common.get_db_cursor(commit=True) as cur:
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (quest_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()

            if not quest or not user:
                raise HTTPException(status_code=404, detail="Not found")

            now_iso = common.get_now_iso()
            
            # --- 承認フロー分岐 ---
            if user_id in self.CHILDREN_IDS:
                # 子供の場合: 報酬を与えず、status='pending'で履歴作成
                cur.execute("""
                    INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """, (user_id, quest['quest_id'], quest['title'], quest['exp_gain'], quest['gold_gain'], now_iso))
                
                logger.info(f"Quest Pending: User={user_id}, Quest={quest['title']}")
                return {
                    "status": "pending",
                    "leveledUp": False, "newLevel": user['level'],
                    "earnedGold": 0, "earnedExp": 0, "earnedMedals": 0,
                    "message": "親の承認待ちです"
                }
            
            # 大人の場合: 即時承認 (既存ロジック)
            return self._apply_quest_rewards(cur, user, quest, now_iso)

    def process_approve_quest(self, approver_id: str, history_id: int) -> Dict[str, Any]:
        """親が保留中のクエストを承認し、報酬を付与する"""
        if approver_id not in self.PARENT_IDS:
            raise HTTPException(status_code=403, detail="承認権限がありません")

        with common.get_db_cursor(commit=True) as cur:
            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist: raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending': raise HTTPException(status_code=400, detail="このクエストは承認待ちではありません")

            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (hist['user_id'],)).fetchone()
            # クエスト定義を一応取得（報酬額は履歴にあるが、詳細情報用）
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (hist['quest_id'],)).fetchone()
            
            # 報酬ロジックの適用
            result = self._apply_quest_rewards(cur, user, quest, common.get_now_iso(), history_id=history_id)
            
            logger.info(f"Quest Approved: Approver={approver_id}, Target={user['user_id']}")
            return result

    def _apply_quest_rewards(self, cur, user, quest, now_iso, history_id=None) -> Dict[str, Any]:
        """報酬計算・DB更新の共通ロジック"""
        # 経験値計算
        current_level = user['level']
        added_exp = user['exp'] + quest['exp_gain']
        new_level, new_exp, leveled_up = self.user_service.calc_level_up(current_level, added_exp)
        
        added_gold = user['gold'] + quest['gold_gain']

        # メダルドロップ判定 (5%)
        earned_medals = 0
        if random.random() < 0.05:
            earned_medals = 1
            logger.info(f"✨ Lucky! Medal dropped for {user['user_id']}")

        # ユーザー更新
        cur.execute("""
            UPDATE quest_users 
            SET level = ?, exp = ?, gold = ?, medal_count = medal_count + ?, updated_at = ? 
            WHERE user_id = ?
        """, (new_level, new_exp, added_gold, earned_medals, now_iso, user['user_id']))
        
        if history_id:
            # 承認時: 既存履歴を更新
            cur.execute("UPDATE quest_history SET status='approved', completed_at=? WHERE id=?", (now_iso, history_id))
        else:
            # 即時完了時: 新規履歴作成
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'approved')
            """, (user['user_id'], quest['quest_id'], quest['title'], quest['exp_gain'], quest['gold_gain'], now_iso))
        
        # ボスバトルチャージ
        try:
            cur.execute("UPDATE party_state SET charge_gauge = charge_gauge + 1 WHERE id = 1")
        except Exception:
            pass

        return {
            "status": "success", 
            "leveledUp": leveled_up, "newLevel": new_level, 
            "earnedGold": quest['gold_gain'], "earnedExp": quest['exp_gain'], "earnedMedals": earned_medals
        }

    def process_cancel_quest(self, user_id: str, history_id: int) -> Dict[str, str]:
        with common.get_db_cursor(commit=True) as cur:
            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist: raise HTTPException(status_code=404, detail="History not found")
            if hist['user_id'] != user_id: raise HTTPException(status_code=403, detail="User mismatch")

            # 承認待ち(pending)なら単に削除して終わり
            if hist['status'] == 'pending':
                cur.execute("DELETE FROM quest_history WHERE id = ?", (history_id,))
                return {"status": "cancelled"}

            # 承認済み(approved)なら減算処理が必要
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            if not user: raise HTTPException(status_code=404, detail="User not found")
            
            new_gold = max(0, user['gold'] - hist['gold_earned'])
            raw_exp_diff = user['exp'] - hist['exp_earned']
            new_level, new_exp = self.user_service.calc_level_down(user['level'], raw_exp_diff)
            
            cur.execute("UPDATE quest_users SET level=?, exp=?, gold=?, updated_at=? WHERE user_id=?", 
                        (new_level, new_exp, new_gold, common.get_now_iso(), user_id))
            cur.execute("DELETE FROM quest_history WHERE id = ?", (history_id,))
            
            try:
                cur.execute("UPDATE party_state SET charge_gauge = MAX(0, charge_gauge - 1) WHERE id = 1")
            except Exception:
                pass
            
            logger.info(f"Quest Cancelled: User={user_id}, HistoryID={history_id}")
        return {"status": "cancelled"}

    def filter_active_quests(self, quests: List[dict]) -> List[dict]:
        """現在の時間・条件に合わせてクエストをフィルタリングする"""
        filtered = []
        now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
        today_str = now.strftime("%Y-%m-%d")
        current_time_str = now.strftime("%H:%M")

        for q in quests:
            if q['quest_type'] == 'limited':
                if q['start_date'] and today_str < q['start_date']: continue
                if q['end_date'] and today_str > q['end_date']: continue
            if q['quest_type'] == 'random':
                seed = f"{today_str}_{q['quest_id']}"
                if random.Random(seed).random() > q['occurrence_chance']: continue
            if q.get('start_time') and q.get('end_time'):
                if q['start_time'] <= q['end_time']:
                    if not (q['start_time'] <= current_time_str <= q['end_time']): continue
                else:
                    if not (current_time_str >= q['start_time'] or current_time_str <= q['end_time']): continue

            q['icon'] = q['icon_key']
            q['type'] = q['quest_type']
            q['target'] = q['target_user']
            if q['day_of_week']:
                q['days'] = [int(d) for d in q['day_of_week'].split(',')]
            else:
                q['days'] = None
            filtered.append(q)
        return filtered


class ShopService:
    """報酬交換、装備購入、装備変更を担当"""

    def process_purchase_reward(self, user_id: str, reward_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            reward = cur.execute("SELECT * FROM reward_master WHERE reward_id = ?", (reward_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            
            if not reward: raise HTTPException(status_code=404, detail="Reward not found")
            if not user: raise HTTPException(status_code=404, detail="User not found")
            if user['gold'] < reward['cost_gold']: raise HTTPException(status_code=400, detail="Not enough gold")
                
            new_gold = user['gold'] - reward['cost_gold']
            cur.execute("UPDATE quest_users SET gold = ?, updated_at = ? WHERE user_id = ?", 
                       (new_gold, common.get_now_iso(), user_id))
            
            cur.execute("""
                INSERT INTO reward_history (user_id, reward_id, reward_title, cost_gold, redeemed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, reward['reward_id'], reward['title'], reward['cost_gold'], common.get_now_iso()))
            
            logger.info(f"Reward Purchased: User={user_id}, Item={reward['title']}")
        return {"status": "purchased", "newGold": new_gold}

    def process_purchase_equipment(self, user_id: str, equipment_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            item = cur.execute("SELECT * FROM equipment_master WHERE equipment_id=?", (equipment_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id=?", (user_id,)).fetchone()
            
            if not item: raise HTTPException(404, "Item not found")
            if not user: raise HTTPException(404, "User not found")
            
            owned = cur.execute("SELECT * FROM user_equipments WHERE user_id=? AND equipment_id=?", (user_id, equipment_id)).fetchone()
            if owned: raise HTTPException(400, "Already owned")
            if user['gold'] < item['cost_gold']: raise HTTPException(400, "Not enough gold")
            
            new_gold = user['gold'] - item['cost_gold']
            cur.execute("UPDATE quest_users SET gold=? WHERE user_id=?", (new_gold, user_id))
            cur.execute("""
                INSERT INTO user_equipments (user_id, equipment_id, is_equipped, acquired_at)
                VALUES (?, ?, 0, ?)
            """, (user_id, equipment_id, common.get_now_iso()))
            
            logger.info(f"Equip Purchased: User={user_id}, Item={item['name']}")
            return {"status": "purchased", "newGold": new_gold}

    def process_change_equipment(self, user_id: str, equipment_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            target_item = cur.execute("""
                SELECT ue.*, em.type FROM user_equipments ue
                JOIN equipment_master em ON ue.equipment_id = em.equipment_id
                WHERE ue.user_id=? AND ue.equipment_id=?
            """, (user_id, equipment_id)).fetchone()
            
            if not target_item: raise HTTPException(404, "Equipment not owned")
            
            item_type = target_item['type']
            cur.execute("""
                UPDATE user_equipments SET is_equipped = 0
                WHERE user_id = ? AND equipment_id IN (
                      SELECT em.equipment_id FROM equipment_master em WHERE em.type = ?)
            """, (user_id, item_type))
            
            cur.execute("UPDATE user_equipments SET is_equipped = 1 WHERE user_id = ? AND equipment_id = ?", (user_id, equipment_id))
            
            logger.info(f"Equip Changed: User={user_id}, ItemID={equipment_id}")
            return {"status": "equipped", "equipment_id": equipment_id}


class GameSystem:
    """ゲームシステム全体（マスタ管理、初期化、一括データ取得）のファサード"""
    
    def __init__(self):
        self.quest_service = QuestService()
        self.user_service = UserService()
        self.shop_service = ShopService()

    def sync_master_data(self) -> Dict[str, str]:
        """マスタデータの同期"""
        # (Step A-1と同じ内容のため省略せず記述)
        logger.info("🔄 Starting Master Data Sync...")
        try:
            importlib.reload(quest_data)
            valid_users = [MasterUser(**u) for u in quest_data.USERS]
            valid_quests = []
            for q in quest_data.QUESTS:
                q_data = q.copy()
                if 'start_time' not in q_data: q_data['start_time'] = None
                if 'end_time' not in q_data: q_data['end_time'] = None
                valid_quests.append(MasterQuest(**q_data))
                
            valid_rewards = [MasterReward(**r) for r in quest_data.REWARDS]
            valid_equipments = [MasterEquipment(**e) for e in quest_data.EQUIPMENTS]
        except Exception as e:
            logger.error(f"❌ Master Data Validation failed: {e}")
            raise HTTPException(status_code=500, detail=f"Master Data Error: {str(e)}")
        
        with common.get_db_cursor(commit=True) as cur:
            for u in valid_users:
                cur.execute("""
                    INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, avatar, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        name = excluded.name, job_class = excluded.job_class, avatar = excluded.avatar
                """, (u.user_id, u.name, u.job_class, u.level, u.exp, u.gold, u.avatar, datetime.datetime.now()))
            
            active_q_ids = [q.id for q in valid_quests]
            if active_q_ids:
                ph = ','.join(['?'] * len(active_q_ids))
                cur.execute(f"DELETE FROM quest_master WHERE quest_id NOT IN ({ph})", active_q_ids)
            else:
                cur.execute("DELETE FROM quest_master")

            for q in valid_quests:
                cur.execute("""
                    INSERT INTO quest_master (
                        quest_id, title, quest_type, target_user, exp_gain, gold_gain, 
                        icon_key, day_of_week, start_date, end_date, occurrence_chance,
                        start_time, end_time
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(quest_id) DO UPDATE SET
                        title = excluded.title, quest_type = excluded.quest_type, target_user = excluded.target_user,
                        exp_gain = excluded.exp_gain, gold_gain = excluded.gold_gain, icon_key = excluded.icon_key,
                        day_of_week = excluded.day_of_week, start_time = excluded.start_time, end_time = excluded.end_time
                """, (q.id, q.title, q.type, q.target, q.exp, q.gold, q.icon, 
                      q.days, q.start, q.end, q.chance, q.start_time, q.end_time))

            active_r_ids = [r.id for r in valid_rewards]
            if active_r_ids:
                ph = ','.join(['?'] * len(active_r_ids))
                cur.execute(f"DELETE FROM reward_master WHERE reward_id NOT IN ({ph})", active_r_ids)
            else:
                cur.execute("DELETE FROM reward_master")

            for r in valid_rewards:
                cur.execute("""
                    INSERT INTO reward_master (reward_id, title, category, cost_gold, icon_key)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(reward_id) DO UPDATE SET
                        title = excluded.title, category = excluded.category,
                        cost_gold = excluded.cost_gold, icon_key = excluded.icon_key
                """, (r.id, r.title, r.category, r.cost_gold, r.icon_key))
            
            active_e_ids = [e.id for e in valid_equipments]
            if active_e_ids:
                ph = ','.join(['?'] * len(active_e_ids))
                cur.execute(f"DELETE FROM equipment_master WHERE equipment_id NOT IN ({ph})", active_e_ids)
            else:
                cur.execute("DELETE FROM equipment_master")

            for e in valid_equipments:
                cur.execute("""
                    INSERT INTO equipment_master (equipment_id, name, type, power, cost_gold, icon_key)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(equipment_id) DO UPDATE SET
                        name = excluded.name, type = excluded.type, power = excluded.power,
                        cost_gold = excluded.cost_gold, icon_key = excluded.icon_key
                """, (e.id, e.name, e.type, e.power, e.cost, e.icon))
        
        logger.info("✅ Master data sync completed.")
        return {"status": "synced", "message": "Master data updated."}

    def get_all_view_data(self) -> Dict[str, Any]:
        with common.get_db_cursor() as cur:
            users = [dict(row) for row in cur.execute("SELECT * FROM quest_users")]
            for u in users:
                u['nextLevelExp'] = self.user_service.calculate_next_level_exp(u['level'])
                u['maxHp'] = self.user_service.calculate_max_hp(u['level'])
                u['hp'] = u['maxHp']

            all_quests = [dict(row) for row in cur.execute("SELECT * FROM quest_master")]
            filtered_quests = self.quest_service.filter_active_quests(all_quests)

            rewards = [dict(row) for row in cur.execute("SELECT * FROM reward_master")]
            for r in rewards:
                r['icon'] = r['icon_key']
                r['cost'] = r['cost_gold']

            today_str = common.get_today_date_str()
            # 完了済みクエスト: status='approved' または 'pending' (pendingも表示して「承認待ち」と出すため)
            completed = [dict(row) for row in cur.execute(
                "SELECT * FROM quest_history WHERE completed_at LIKE ?", (f"{today_str}%",)
            )]
            
            # ★追加: 承認待ち(全期間): pendingを取得（親の承認リスト用）
            pending = [dict(row) for row in cur.execute(
                "SELECT * FROM quest_history WHERE status='pending' ORDER BY completed_at ASC"
            )]

            logs = self._fetch_recent_logs(cur)

            equipments = [dict(row) for row in cur.execute("SELECT * FROM equipment_master")]
            for e in equipments:
                e['icon'] = e['icon_key']
                e['cost'] = e['cost_gold']

            owned_equipments = [dict(row) for row in cur.execute("""
                SELECT ue.*, em.name, em.type, em.power, em.icon_key 
                FROM user_equipments ue
                JOIN equipment_master em ON ue.equipment_id = em.equipment_id
            """)]
            
            boss_state = self._get_party_state(cur)

        return {
            "users": users, "quests": filtered_quests, "rewards": rewards,
            "completedQuests": completed, "logs": logs,
            "pendingQuests": pending, 
            "equipments": equipments, "ownedEquipments": owned_equipments,
            "boss": boss_state
        }

    def _fetch_recent_logs(self, cur) -> List[dict]:
        # ログには承認済みだけを表示
        q_logs = cur.execute("""
            SELECT id, user_id, quest_title as title, 'quest' as type, completed_at as ts 
            FROM quest_history WHERE status='approved' ORDER BY id DESC LIMIT 20
        """).fetchall()
        r_logs = cur.execute("""
            SELECT id, user_id, reward_title as title, 'reward' as type, redeemed_at as ts 
            FROM reward_history ORDER BY id DESC LIMIT 20
        """).fetchall()
        all_logs = sorted(q_logs + r_logs, key=lambda x: x['ts'], reverse=True)[:20]
        user_map = {row['user_id']: row['name'] for row in cur.execute("SELECT user_id, name FROM quest_users")}
        formatted = []
        for l in all_logs:
            name = user_map.get(l['user_id'], '誰か')
            ts_str = l['ts']
            date_str = ts_str.split('T')[0] if 'T' in ts_str else ts_str.split(' ')[0]
            text = f"{name}は {l['title']} を{'クリアした！' if l['type']=='quest' else '手に入れた！'}"
            formatted.append({"id": f"{l['type']}_{l['id']}", "text": text, "dateStr": date_str, "timestamp": ts_str})
        return formatted

    def _get_party_state(self, cur) -> Dict[str, Any]:
        try:
            row = cur.execute("SELECT * FROM party_state WHERE id = 1").fetchone()
            if not row: return None
            boss_def = next((b for b in quest_data.BOSSES if b['id'] == row['current_boss_id']), None)
            if not boss_def: return None
            return {
                "bossId": boss_def['id'], "bossName": boss_def['name'], "bossIcon": boss_def['icon'],
                "maxHp": boss_def['hp'], "currentHp": row['current_hp'], "charge": row['charge_gauge'],
                "desc": boss_def['desc']
            }
        except Exception:
            return None


# ==========================================
# 3. API Endpoints (Wiring)
# ==========================================

game_system = GameSystem()
quest_service = game_system.quest_service
shop_service = game_system.shop_service
user_service = game_system.user_service

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

# ★新規追加: 承認エンドポイント
@router.post("/approve", response_model=CompleteResponse)
def approve_quest(action: ApproveAction):
    return quest_service.process_approve_quest(action.approver_id, action.history_id)

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

# ★追加: unified_server.py から呼ばれる初期化用関数を復活
def seed_data():
    return game_system.sync_master_data()

# ★追加: 同期用エンドポイント（エイリアス）
@router.post("/seed", response_model=SyncResponse)
def seed_data_endpoint():
    return game_system.sync_master_data()

@router.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    """画像をアップロードし、アクセス用URLを返す"""
    try:
        # 拡張子の検証
        allowed_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in allowed_extensions:
            raise HTTPException(status_code=400, detail="許可されていないファイル形式です")

        # ユニークなファイル名を生成 (衝突防止)
        new_filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(config.UPLOAD_DIR, new_filename)

        # ファイル保存
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        logger.info(f"Image Uploaded: {new_filename}")

        # アクセス用URLを返す
        # ※本番環境のドメインに合わせて調整が必要だが、ローカルならこれでOK
        return {"url": f"/uploads/{new_filename}"}

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail="画像の保存に失敗しました")