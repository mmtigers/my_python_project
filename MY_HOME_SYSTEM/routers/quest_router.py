# MY_HOME_SYSTEM/routers/quest_router.py
from fastapi import APIRouter, HTTPException, status
from fastapi import File, UploadFile
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import datetime
import shutil 
import os 
import uuid 
import math
import pytz
import importlib
import random
import sys
import aiofiles

import common
import config
import game_logic
import sound_manager
from services import notification_service

# import quest_data with fallback
try:
    import quest_data
except ImportError:
    from .. import quest_data

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

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
    desc: Optional[str] = None
    type: str
    target: str = 'all'
    exp: int
    gold: int
    icon: str
    days: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
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

class UpdateUserAction(BaseModel):
    user_id: str
    avatar_url: str

class SoundTestRequest(BaseModel):
    sound_key: str

# Response Models
class SyncResponse(BaseModel):
    status: str
    message: str

class CompleteResponse(BaseModel):
    status: str
    leveledUp: bool
    newLevel: int
    earnedGold: int
    earnedExp: int
    earnedMedals: int = 0
    message: Optional[str] = None
    # ★追加: ボス演出用データ
    bossEffect: Optional[dict] = None 

class CancelResponse(BaseModel):
    status: str

class PurchaseResponse(BaseModel):
    status: str
    newGold: int

# ★追加: 管理用リクエストモデル
class AdminBossUpdate(BaseModel):
    max_hp: Optional[int] = None
    current_hp: Optional[int] = None
    is_defeated: Optional[bool] = None


# Response Model for Inventory Item
class InventoryItem(BaseModel):
    id: int             # inventory ID
    reward_id: int      # master ID
    title: str
    desc: Optional[str] = None
    icon: str
    status: str         # owned, pending, consumed
    purchased_at: str
    used_at: Optional[str] = None

# Action Models
class UseItemAction(BaseModel):
    user_id: str
    inventory_id: int

class ConsumeItemAction(BaseModel):
    approver_id: str    # 親のID
    inventory_id: int

# ==========================================
# 2. Service Layers (Logic Separation)
# ==========================================

class UserService:
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
    
    def update_avatar(self, user_id: str, avatar_url: str) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            cur.execute("UPDATE quest_users SET avatar = ?, updated_at = ? WHERE user_id = ?", 
                       (avatar_url, common.get_now_iso(), user_id))
            
            logger.info(f"Avatar Updated: User={user_id}, URL={avatar_url}")
            return {"status": "updated", "avatar": avatar_url}


class QuestService:
    CHILDREN_IDS = ['daughter', 'son', 'child'] # 調整
    PARENT_IDS = ['dad', 'mom']

    def __init__(self):
        self.user_service = UserService()
    
    # --- ★追加: 攻撃力計算ヘルパー ---
    def _calculate_user_attack_power(self, cur, user_id: str) -> int:
        """
        ユーザーの基礎攻撃力（Lv*3）＋装備攻撃力の合計を算出する
        """
        # 1. ユーザーレベル取得
        user_row = cur.execute("SELECT level FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
        if not user_row: return 0
        level = user_row['level']
        
        # 2. 装備中のアイテムのPower合計を取得
        # (武器だけでなく防具もPowerを持っていれば加算し、総合的な「強さ」とする)
        row = cur.execute("""
            SELECT SUM(em.power) as total_power
            FROM user_equipments ue
            JOIN equipment_master em ON ue.equipment_id = em.equipment_id
            WHERE ue.user_id = ? AND ue.is_equipped = 1
        """, (user_id,)).fetchone()
        
        equip_power = row['total_power'] if row and row['total_power'] else 0
        
        # 攻撃力計算式: Lv * 3 + 装備パワー
        return (level * 3) + equip_power

    # --- ボスロジック開始 ---

    def _check_and_reset_weekly_boss(self, cur):
        """週が変わっていたらボスをリセット・再抽選する"""
        party_row = cur.execute("SELECT * FROM party_state WHERE id = 1").fetchone()
        if not party_row:
            return 
        
        # ★修正: sqlite3.Row を安全に辞書に変換
        party = {k: party_row[k] for k in party_row.keys()}
            
        now = datetime.datetime.now()
        today_date = now.date()
        # 今週の月曜日を算出
        this_monday = today_date - datetime.timedelta(days=today_date.weekday())
        this_monday_str = str(this_monday)
        
        # DB上の週と一致するかチェック
        db_week_start = party.get('week_start_date')
        
        if db_week_start != this_monday_str:
            logger.info(f"🔄 New Week Detected! Resetting Boss... (Old: {db_week_start}, New: {this_monday_str})")
            
            # quest_data.BOSSES の長さを取得して動的にループさせる
            # BOSSES が未定義の場合は安全策として1固定
            boss_list = getattr(quest_data, "BOSSES", [])
            total_bosses = len(boss_list) if boss_list else 5

            next_boss_id = party['current_boss_id'] + 1
            if next_boss_id > total_bosses:
                next_boss_id = 1
                
            # 新しいHPの設定 (例: 基礎HP 1000)
            new_max_hp = 1000
            
            cur.execute("""
                UPDATE party_state 
                SET current_boss_id = ?, 
                    current_hp = ?, 
                    max_hp = ?,
                    week_start_date = ?,
                    is_defeated = 0,
                    total_damage = 0,
                    charge_gauge = 0,
                    updated_at = ?
                WHERE id = 1
            """, (next_boss_id, new_max_hp, new_max_hp, this_monday_str, common.get_now_iso()))

    def _apply_boss_damage(self, cur, damage: int) -> dict:
        """ボスにダメージを与え、状態を更新し、演出データを返す"""
        
        # まず週次チェック
        self._check_and_reset_weekly_boss(cur)
        
        party_row = cur.execute("SELECT * FROM party_state WHERE id = 1").fetchone()
        if not party_row:
            return None
            
        # ★修正: sqlite3.Row を安全に辞書に変換
        party = {k: party_row[k] for k in party_row.keys()}

        current_hp = party['current_hp']
        is_defeated = party['is_defeated']
        
        # すでに撃破済みの場合はダメージ処理スキップ
        if is_defeated:
            return {
                "damage": damage,
                "remainingHp": 0,
                "isDefeated": True,
                "isNewDefeat": False
            }
            
        # ダメージ計算
        new_hp = max(0, current_hp - damage)
        new_defeated = 1 if new_hp == 0 else 0
        
        # DB更新
        cur.execute("""
            UPDATE party_state 
            SET current_hp = ?, 
                total_damage = total_damage + ?, 
                is_defeated = ?,
                updated_at = ?
            WHERE id = 1
        """, (new_hp, damage, new_defeated, common.get_now_iso()))
        
        # サウンド演出トリガー
        is_new_defeat = (new_defeated == 1 and is_defeated == 0)
        
        if is_new_defeat:
            sound_manager.play("boss_defeat_fanfare")
            logger.info("🎉 WEEKLY BOSS DEFEATED!")
        else:
            sound_manager.play("attack_hit")
            
        return {
            "damage": damage,
            "remainingHp": new_hp,
            "isDefeated": bool(new_defeated),
            "isNewDefeat": is_new_defeat
        }

    # --- ボスロジック終了 ---

    def calculate_quest_boost(self, cur, user_id: str, quest: dict) -> Dict[str, int]:
        if quest['quest_type'] != 'daily':
            return {"gold": 0, "exp": 0}

        last_hist = cur.execute("""
            SELECT completed_at FROM quest_history 
            WHERE user_id = ? AND quest_id = ? AND status = 'approved'
            ORDER BY completed_at DESC LIMIT 1
        """, (user_id, quest['quest_id'])).fetchone()

        now = datetime.datetime.now()
        last_date = None

        if last_hist:
            try:
                dt = datetime.datetime.fromisoformat(last_hist['completed_at'])
                last_date = dt.date()
            except Exception:
                pass
        
        if not last_date:
            return {"gold": 0, "exp": 0}

        today_date = now.date()
        days_diff = (today_date - last_date).days

        if days_diff <= 1:
            return {"gold": 0, "exp": 0}
        
        missed_days = days_diff - 1
        bonus_ratio = min(missed_days * 0.10, 1.0)
        bonus_gold = int(quest['gold_gain'] * bonus_ratio)
        bonus_exp = int(quest['exp_gain'] * bonus_ratio)

        return {"gold": bonus_gold, "exp": bonus_exp}

    def process_complete_quest(self, user_id: str, quest_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (quest_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()

            if not quest or not user:
                raise HTTPException(status_code=404, detail="Not found")

            # スパムチェック
            last_hist = cur.execute("""
                SELECT completed_at FROM quest_history 
                WHERE user_id = ? AND quest_id = ? AND status != 'rejected'
                ORDER BY completed_at DESC LIMIT 1
            """, (user_id, quest['quest_id'])).fetchone()

            if last_hist and last_hist['completed_at']:
                try:
                    last_time = datetime.datetime.fromisoformat(last_hist['completed_at'])
                    now_check = datetime.datetime.now()
                    if last_time.tzinfo is not None: 
                        last_time = last_time.replace(tzinfo=None)
                    
                    if (now_check - last_time).total_seconds() < 10:
                        raise HTTPException(status_code=429, detail="少し時間を空けてから実行してください")
                except HTTPException:
                    raise
                except Exception:
                    pass

            now_iso = common.get_now_iso()
            boost = self.calculate_quest_boost(cur, user_id, quest)
            total_exp = quest['exp_gain'] + boost['exp']
            total_gold = quest['gold_gain'] + boost['gold']
            
            if user_id in self.CHILDREN_IDS:
                cur.execute("""
                    INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """, (user_id, quest['quest_id'], quest['title'], total_exp, total_gold, now_iso))
                
                logger.info(f"Quest Pending: User={user_id}, Quest={quest['title']}, BonusG={boost['gold']}")
                sound_manager.play("submit")

                return {
                    "status": "pending",
                    "leveledUp": False, "newLevel": user['level'],
                    "earnedGold": 0, "earnedExp": 0, "earnedMedals": 0,
                    "message": "親の承認待ちです"
                }
            
            # ★大人の場合（即時完了）
            # 1. 報酬適用
            result = self._apply_quest_rewards(cur, user, quest, now_iso, override_rewards={"gold": total_gold, "exp": total_exp})
            
            # 2. ★ダメージ計算 (ステータス反映)
            atk_power = self._calculate_user_attack_power(cur, user_id)
            is_critical = random.random() < 0.1 # 10%でクリティカル
            crit_multiplier = 1.5 if is_critical else 1.0
            
            # ダメージ = (クエストEXP + 攻撃力) * 倍率
            damage_value = int((total_exp + atk_power) * crit_multiplier)
            
            # 3. ボスへ反映
            boss_effect = self._apply_boss_damage(cur, damage_value)
            boss_effect['isCritical'] = is_critical # クリティカル情報を付与
            
            # ★修正: 設定が有効な場合のみ、フロントエンドへ演出データを返す
            if getattr(config, 'ENABLE_BATTLE_EFFECT', True):
                result['bossEffect'] = boss_effect
            
            logger.info(f"Adult Attack: User={user_id}, Base={total_exp}, Atk={atk_power}, Crit={is_critical}, Dmg={damage_value}")
            return result

    def process_approve_quest(self, approver_id: str, history_id: int) -> Dict[str, Any]:
        if approver_id not in self.PARENT_IDS:
            raise HTTPException(status_code=403, detail="承認権限がありません")

        with common.get_db_cursor(commit=True) as cur:
            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist: raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending': raise HTTPException(status_code=400, detail="承認待ちではありません")

            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (hist['user_id'],)).fetchone()
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (hist['quest_id'],)).fetchone()

            # ★変更: 音の重複を避けるため、ここでは承認音は鳴らさない (または attack_hit に任せる)
            # sound_manager.play("approve")
            
            override_rewards = {
                "gold": hist['gold_earned'],
                "exp": hist['exp_earned']
            }

            # 1. 報酬適用
            result = self._apply_quest_rewards(cur, user, quest, common.get_now_iso(), history_id=history_id, override_rewards=override_rewards)
            
            # 2. ★ダメージ計算 (ステータス反映)
            # 攻撃者はクエスト実行者 (hist['user_id'])
            attacker_id = hist['user_id']
            atk_power = self._calculate_user_attack_power(cur, attacker_id)
            is_critical = random.random() < 0.1
            crit_multiplier = 1.5 if is_critical else 1.0
            
            base_damage = override_rewards['exp']
            damage_value = int((base_damage + atk_power) * crit_multiplier)
            
            # 3. ボスへ反映
            boss_effect = self._apply_boss_damage(cur, damage_value)
            boss_effect['isCritical'] = is_critical
            
            # ★修正: 設定が有効な場合のみ、フロントエンドへ演出データを返す
            if getattr(config, 'ENABLE_BATTLE_EFFECT', True):
                result['bossEffect'] = boss_effect
            
            logger.info(f"Child Attack Approved: Attacker={attacker_id}, Atk={atk_power}, Crit={is_critical}, Dmg={damage_value}")
            return result
    
    def process_reject_quest(self, approver_id: str, history_id: int) -> Dict[str, str]:
        if approver_id not in self.PARENT_IDS:
            raise HTTPException(status_code=403, detail="承認権限がありません")

        with common.get_db_cursor(commit=True) as cur:
            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist: raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending': raise HTTPException(status_code=400, detail="承認待ちではありません")

            cur.execute("DELETE FROM quest_history WHERE id = ?", (history_id,))
            logger.info(f"Quest Rejected: Approver={approver_id}, Target={hist['user_id']}")
            return {"status": "rejected"}

    def _apply_quest_rewards(self, cur, user, quest, now_iso, history_id=None, override_rewards=None) -> Dict[str, Any]:
        if override_rewards:
            base_gold = override_rewards['gold']
            base_exp = override_rewards['exp']
        else:
            base_gold = quest['gold_gain']
            base_exp = quest['exp_gain']

        rewards = game_logic.GameLogic.calculate_drop_rewards(base_gold, base_exp)
        earned_gold = rewards['gold']
        earned_exp = rewards['exp']
        earned_medals = rewards['medals']
        is_lucky = rewards['is_lucky']

        new_level, new_exp_val, leveled_up = game_logic.GameLogic.calc_level_progress(
            user['level'], user['exp'], earned_exp
        )
        
        final_gold = user['gold'] + earned_gold

        cur.execute("""
            UPDATE quest_users 
            SET level = ?, exp = ?, gold = ?, medal_count = medal_count + ?, updated_at = ? 
            WHERE user_id = ?
        """, (new_level, new_exp_val, final_gold, earned_medals, now_iso, user['user_id']))
        
        if history_id:
            cur.execute("UPDATE quest_history SET status='approved', completed_at=?, gold_earned=?, exp_earned=? WHERE id=?", 
                       (now_iso, earned_gold, earned_exp, history_id))
        else:
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'approved')
            """, (user['user_id'], quest['quest_id'], quest['title'], earned_exp, earned_gold, now_iso))
        
        # ボスチャージゲージ (念のため残す)
        try:
            cur.execute("UPDATE party_state SET charge_gauge = charge_gauge + 1 WHERE id = 1")
        except Exception:
            pass

        # サウンド (レベルアップ優先)
        if leveled_up:
            sound_manager.play("level_up")
        elif is_lucky:
            sound_manager.play("medal_get")
        elif not history_id:
            # 即時完了時のみここで鳴らす(承認時は_apply_boss_damageで鳴らすため)
            sound_manager.play("quest_clear")

        return {
            "status": "success", 
            "leveledUp": leveled_up, "newLevel": new_level, 
            "earnedGold": earned_gold, "earnedExp": earned_exp, "earnedMedals": earned_medals
        }

    def process_cancel_quest(self, user_id: str, history_id: int) -> Dict[str, str]:
        with common.get_db_cursor(commit=True) as cur:
            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist: raise HTTPException(status_code=404, detail="History not found")
            if hist['user_id'] != user_id: raise HTTPException(status_code=403, detail="User mismatch")

            if hist['status'] == 'pending':
                cur.execute("DELETE FROM quest_history WHERE id = ?", (history_id,))
                return {"status": "cancelled"}

            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            if not user: raise HTTPException(status_code=404, detail="User not found")
            
            new_level, new_exp = game_logic.GameLogic.calc_level_down(
                user['level'], user['exp'], hist['exp_earned']
            )
            
            new_gold = max(0, user['gold'] - hist['gold_earned'])
            
            cur.execute("UPDATE quest_users SET level=?, exp=?, gold=?, updated_at=? WHERE user_id=?", 
                        (new_level, new_exp, new_gold, common.get_now_iso(), user_id))
            cur.execute("DELETE FROM quest_history WHERE id = ?", (history_id,))
            
            logger.info(f"Quest Cancelled: User={user_id}, HistoryID={history_id}")
        return {"status": "cancelled"}

    def filter_active_quests(self, quests: List[dict]) -> List[dict]:
        filtered = []
        now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
        today_date = now.date()
        current_time_str = now.strftime("%H:%M")
        current_weekday = today_date.weekday()

        for q in quests:
            if q['quest_type'] == 'limited':
                try:
                    if q.get('start_date'):
                        y, m, d = map(int, q['start_date'].split('-'))
                        start_dt = datetime.date(y, m, d)
                        if today_date < start_dt: continue
                    if q.get('end_date'):
                        y, m, d = map(int, q['end_date'].split('-'))
                        end_dt = datetime.date(y, m, d)
                        if today_date > end_dt: continue
                except ValueError as e:
                    logger.warning(f"Date parse error for quest {q.get('id')}: {e}")
                    continue
            if q['quest_type'] == 'random':
                seed = f"{now.strftime('%Y-%m-%d')}_{q['quest_id']}"
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
                days_list = [int(d) for d in q['day_of_week'].split(',')]
                q['days'] = days_list
                if current_weekday not in days_list:
                    continue
            else:
                q['days'] = None
            filtered.append(q)
        return filtered


class ShopService:
    def process_purchase_reward(self, user_id: str, reward_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            reward = cur.execute("SELECT * FROM reward_master WHERE reward_id = ?", (reward_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            
            if not reward: raise HTTPException(status_code=404, detail="Reward not found")
            if not user: raise HTTPException(status_code=404, detail="User not found")
            if user['gold'] < reward['cost_gold']: raise HTTPException(status_code=400, detail="Not enough gold")
                
            # 1. お金の減算 (既存)
            new_gold = user['gold'] - reward['cost_gold']
            cur.execute("UPDATE quest_users SET gold = ?, updated_at = ? WHERE user_id = ?", 
                       (new_gold, common.get_now_iso(), user_id))
            
            now_iso = common.get_now_iso()

            # 2. 履歴ログに追加 (既存: 分析・統計用)
            cur.execute("""
                INSERT INTO reward_history (user_id, reward_id, reward_title, cost_gold, redeemed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, reward['reward_id'], reward['title'], reward['cost_gold'], now_iso))
            
            # 3. 【新規】インベントリに追加 (実体管理用)
            cur.execute("""
                INSERT INTO user_inventory (user_id, reward_id, status, purchased_at)
                VALUES (?, ?, 'owned', ?)
            """, (user_id, reward['reward_id'], now_iso))
            
            logger.info(f"Reward Purchased & Stored: User={user_id}, Item={reward['title']}")
            
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


class InventoryService:
    def get_user_inventory(self, user_id: str) -> List[dict]:
        with common.get_db_cursor() as cur:
            sql = """
                SELECT ui.id, ui.reward_id, ui.status, ui.purchased_at, ui.used_at,
                       rm.title, rm.icon_key as icon, rm.category
                FROM user_inventory ui
                JOIN reward_master rm ON ui.reward_id = rm.reward_id
                WHERE ui.user_id = ? AND ui.status IN ('owned', 'pending')
                ORDER BY ui.purchased_at DESC
            """
            rows = cur.execute(sql, (user_id,)).fetchall()
            return [dict(row) for row in rows]

    def use_item(self, user_id: str, inventory_id: int) -> Dict[str, str]:
        """子供がアイテムを使用する（申請 or 即時使用）"""
        with common.get_db_cursor(commit=True) as cur:
            sql = """
                SELECT ui.*, rm.title, qu.name as user_name
                FROM user_inventory ui
                JOIN reward_master rm ON ui.reward_id = rm.reward_id
                JOIN quest_users qu ON ui.user_id = qu.user_id
                WHERE ui.id = ?
            """
            item = cur.execute(sql, (inventory_id,)).fetchone()

            if not item: raise HTTPException(404, "Item not found")
            if item['user_id'] != user_id: raise HTTPException(403, "Not your item")
            if item['status'] != 'owned': raise HTTPException(400, "Cannot use this item")

            # 承認フローが有効かどうか
            if config.ENABLE_APPROVAL_FLOW:
                cur.execute("""
                    UPDATE user_inventory 
                    SET status = 'pending', used_at = ? 
                    WHERE id = ?
                """, (common.get_now_iso(), inventory_id))
                
                # 通知送信
                self._send_approval_request(item)

                sound_manager.play("select")
                
                # ★修正②: 誰に承認を求めるかメッセージを分岐
                if user_id == 'dad':
                    msg = "使いました！ママ(はるな)に承認してもらってね"
                elif user_id == 'mom':
                    msg = "使いました！パパ(まさひろ)に承認してもらってね"
                else:
                    msg = "使いました！パパ・ママに承認してもらってね"

                return {"status": "pending", "message": msg}
            
            else:
                # 承認フロー無効 (即時使用)
                cur.execute("""
                    UPDATE user_inventory 
                    SET status = 'consumed', used_at = ? 
                    WHERE id = ?
                """, (common.get_now_iso(), inventory_id))

                msg = f"🎒 {item['user_name']}が「{item['title']}」を使用しました。"
                notification_service.send_push(
                    user_id=config.LINE_USER_ID, 
                    messages=[{"type": "text", "text": msg}],
                    priority="normal"
                )

                sound_manager.play("quest_clear")
                return {"status": "consumed", "message": "アイテムを使いました！"}
    
    def _send_approval_request(self, item):
        """承認依頼通知を送る処理"""
        user_name = item['user_name']
        item_title = item['title']
        
        # 宛先判定: 基本はLINEグループ(両親)へ。設定がなければ管理者個人へ。
        # ※「父→母」「母→父」などの細かい出し分けも、グループに送れば両方が見られるため確実です。
        dest_line_id = config.LINE_PARENTS_GROUP_ID or config.LINE_USER_ID
        
        msg_text = f"【承認依頼】\n{user_name}が「{item_title}」を使いたがっています！\nアプリの「もちもの」タブまたは承認リストから承認してください。"
        
        notification_service.send_push(
            user_id=dest_line_id,
            messages=[{"type": "text", "text": msg_text}],
            priority="high" # ★重要通知: 本番環境ならLINEに飛びます
        )

    def consume_item(self, approver_id: str, inventory_id: int) -> Dict[str, str]:
        """親が使用を承認する（消費済みにする）"""
        # 親権限チェック (QuestServiceの定数を利用)
        if approver_id not in QuestService.PARENT_IDS:
             raise HTTPException(403, "承認権限がありません")

        with common.get_db_cursor(commit=True) as cur:
            item = cur.execute("SELECT * FROM user_inventory WHERE id = ?", (inventory_id,)).fetchone()
            if not item: raise HTTPException(404, "Item not found")
            
            cur.execute("""
                UPDATE user_inventory 
                SET status = 'consumed', used_at = ? 
                WHERE id = ?
            """, (common.get_now_iso(), inventory_id))

            sound_manager.play("quest_clear") # 承認音
            
            return {"status": "consumed", "message": "承認しました"}

    def cancel_usage(self, user_id: str, inventory_id: int) -> Dict[str, str]:
        """申請を取り下げる"""
        with common.get_db_cursor(commit=True) as cur:
            item = cur.execute("SELECT * FROM user_inventory WHERE id = ?", (inventory_id,)).fetchone()
            if not item: raise HTTPException(404, "Item not found")
            if item['user_id'] != user_id: raise HTTPException(403, "Not your item")
            if item['status'] != 'pending': raise HTTPException(400, "Not pending")

            cur.execute("UPDATE user_inventory SET status = 'owned', used_at = NULL WHERE id = ?", (inventory_id,))
            return {"status": "owned", "message": "リュックに戻しました"}
    
    def get_pending_items(self) -> List[dict]:
        """【管理用】全ユーザーの承認待ちアイテムを取得"""
        with common.get_db_cursor() as cur:
            sql = """
                SELECT ui.id, ui.user_id, ui.reward_id, ui.used_at,
                       rm.title, rm.icon_key as icon, rm.category,
                       qu.name as user_name
                FROM user_inventory ui
                JOIN reward_master rm ON ui.reward_id = rm.reward_id
                LEFT JOIN quest_users qu ON ui.user_id = qu.user_id
                WHERE ui.status = 'pending'
                ORDER BY ui.used_at ASC
            """
            rows = cur.execute(sql).fetchall()
            return [dict(row) for row in rows]

class GameSystem:
    def __init__(self):
        self.quest_service = QuestService()
        self.user_service = UserService()
        self.shop_service = ShopService()

    def sync_master_data(self) -> Dict[str, str]:
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
                        name = excluded.name, 
                        job_class = excluded.job_class
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
                        quest_id, title, description, quest_type, target_user, exp_gain, gold_gain, 
                        icon_key, day_of_week, start_date, end_date, occurrence_chance,
                        start_time, end_time
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(quest_id) DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        quest_type = excluded.quest_type, target_user = excluded.target_user,
                        exp_gain = excluded.exp_gain, gold_gain = excluded.gold_gain, icon_key = excluded.icon_key,
                        day_of_week = excluded.day_of_week, start_time = excluded.start_time, end_time = excluded.end_time,
                        start_date = excluded.start_date, end_date = excluded.end_date, occurrence_chance = excluded.occurrence_chance
                """, (
                    q.id, q.title, q.desc, q.type, q.target, q.exp, q.gold, q.icon,
                    q.days, 
                    q.start_date, q.end_date, 
                    q.chance, q.start_time, q.end_time
                ))
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
                u['nextLevelExp'] = game_logic.GameLogic.calculate_next_level_exp(u['level'])
                u['maxHp'] = game_logic.GameLogic.calculate_max_hp(u['level'])
                u['hp'] = u['maxHp']

            all_quests = [dict(row) for row in cur.execute("SELECT * FROM quest_master")]
            filtered_quests = self.quest_service.filter_active_quests(all_quests)

            for q in filtered_quests:
                if q['target_user'] and q['target_user'] != 'all':
                    boost = self.quest_service.calculate_quest_boost(cur, q['target_user'], q)
                    q['bonus_gold'] = boost['gold']
                    q['bonus_exp'] = boost['exp']
                else:
                    q['bonus_gold'] = 0
                    q['bonus_exp'] = 0

            rewards = [dict(row) for row in cur.execute("SELECT * FROM reward_master")]
            for r in rewards:
                r['icon'] = r['icon_key']
                r['cost'] = r['cost_gold']

            today_str = common.get_today_date_str()
            completed = [dict(row) for row in cur.execute(
                "SELECT * FROM quest_history WHERE completed_at LIKE ?", (f"{today_str}%",)
            )]
            
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

    # ★修正: _get_party_state
    def _get_party_state(self, cur) -> Dict[str, Any]:
        # データ取得時にも週次チェック (Lazy Init)
        self.quest_service._check_and_reset_weekly_boss(cur)
        
        try:
            row_obj = cur.execute("SELECT * FROM party_state WHERE id = 1").fetchone()
            if not row_obj: return None
            
            # ★修正: sqlite3.Row を安全に辞書に変換
            row = {k: row_obj[k] for k in row_obj.keys()}
            
            # quest_data からボスマスタ情報を取得
            # BOSSES が存在しない場合のガード
            boss_list = getattr(quest_data, "BOSSES", [])
            boss_def = next((b for b in boss_list if b['id'] == row['current_boss_id']), None)
            
            # 定義が見つからない場合のフォールバック
            if not boss_def:
                boss_def = {"id": 99, "name": "謎の影", "icon": "❓", "desc": "正体不明の敵", "hp": 1000}

            # max_hp は DBの値（難易度調整後）を優先する
            current_max_hp = row.get('max_hp', boss_def['hp'])

            return {
                "bossId": boss_def['id'],
                "bossName": boss_def['name'],
                "bossIcon": boss_def['icon'],
                "maxHp": current_max_hp,
                "currentHp": row['current_hp'],
                "hpPercentage": (row['current_hp'] / current_max_hp) * 100 if current_max_hp > 0 else 0,
                "charge": row['charge_gauge'],
                "desc": boss_def['desc'],
                "isDefeated": bool(row.get('is_defeated', 0)),
                "weekStartDate": row.get('week_start_date')
            }
        except Exception as e:
            logger.error(f"Error getting party state: {e}")
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


inventory_service = InventoryService() # インスタンス化

@router.get("/inventory/{user_id}")
def get_inventory(user_id: str):
    return inventory_service.get_user_inventory(user_id)

@router.post("/inventory/use")
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