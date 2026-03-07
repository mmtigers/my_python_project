import datetime
import importlib
import random
import math
import pytz
from typing import List, Dict, Any, Optional

from fastapi import HTTPException
import common
import config
import game_logic
import sound_manager
from services import notification_service
from core.logger import setup_logging

# モデル定義のインポート (型ヒント用)
from models.quest import MasterUser, MasterQuest, MasterReward, MasterEquipment

# ロガー設定
logger = setup_logging("quest_service")

# quest_data import fallback
try:
    import quest_data
except ImportError:
    try:
        from .. import quest_data
    except ImportError:
        logger.warning("quest_data module not found via relative import.")
        quest_data = None

# ==========================================
# Service Classes
# ==========================================

class UserService:
    def get_family_chronicle(self) -> Dict[str, Any]:
        with common.get_db_cursor() as cur:
            users = cur.execute("SELECT level, gold FROM quest_users").fetchall()
            total_level = sum(u['level'] for u in users) if users else 0
            total_gold = sum(u['gold'] for u in users) if users else 0
            res = cur.execute("SELECT COUNT(*) as count FROM quest_history").fetchone()
            total_quests = res['count'] if res else 0
            
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
    CHILDREN_IDS = ['daughter', 'son', 'child']
    PARENT_IDS = ['dad', 'mom']

    def is_within_reset_period(self, completed_at_str: str, reset_period: str) -> bool:
        if not completed_at_str: return False
        now = datetime.datetime.now()
        try:
            completed_date = datetime.datetime.fromisoformat(completed_at_str).date()
        except Exception:
            completed_date = datetime.datetime.strptime(completed_at_str.split(' ')[0], "%Y-%m-%d").date()
        
        today = now.date()
        
        if reset_period == 'daily':
            return completed_date == today
        elif reset_period == 'weekly_monday':
            this_monday = today - datetime.timedelta(days=today.weekday())
            return completed_date >= this_monday
        elif reset_period == 'monthly_1st':
            this_month_1st = today.replace(day=1)
            return completed_date >= this_month_1st
        else: # デフォルト
            this_monday = today - datetime.timedelta(days=today.weekday())
            return completed_date >= this_monday

    def __init__(self):
        self.user_service = UserService()
    
    def _calculate_user_attack_power(self, cur, user_id: str) -> int:
        user_row = cur.execute("SELECT level FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
        if not user_row: return 0
        level = user_row['level']
        
        row = cur.execute("""
            SELECT SUM(em.power) as total_power
            FROM user_equipments ue
            JOIN equipment_master em ON ue.equipment_id = em.equipment_id
            WHERE ue.user_id = ? AND ue.is_equipped = 1
        """, (user_id,)).fetchone()
        
        equip_power = row['total_power'] if row and row['total_power'] else 0
        return (level * 3) + equip_power

    def _check_and_reset_weekly_boss(self, cur):
        """
        週次ボスのリセット判定を行い、party_stateレコードが存在しない場合は初期化する。
        """
        party_row = cur.execute("SELECT * FROM party_state WHERE id = 1").fetchone()
        
        now = datetime.datetime.now()
        today_date = now.date()
        this_monday = today_date - datetime.timedelta(days=today_date.weekday())
        this_monday_str = str(this_monday)
        
        # ---------------------------------------------------------
        # FIX: レコードが存在しない場合の自己修復 (Auto-Healing)
        # ---------------------------------------------------------
        if not party_row:
            logger.warning("⚠️ party_state record not found. Initializing new record...")
            initial_boss_id = 1
            initial_hp = 1000
            
            cur.execute("""
                INSERT INTO party_state (id, current_boss_id, current_hp, max_hp, week_start_date, is_defeated, total_damage, charge_gauge, updated_at)
                VALUES (1, ?, ?, ?, ?, 0, 0, 0, ?)
            """, (initial_boss_id, initial_hp, initial_hp, this_monday_str, common.get_now_iso()))
            
            # 初期化後に再度取得
            party_row = cur.execute("SELECT * FROM party_state WHERE id = 1").fetchone()

        party = {k: party_row[k] for k in party_row.keys()}
        db_week_start = party.get('week_start_date')
        
        # 週替わり判定
        if db_week_start != this_monday_str:
            logger.info(f"🔄 New Week Detected! Resetting Boss... (Old: {db_week_start}, New: {this_monday_str})")
            
            boss_list = getattr(quest_data, "BOSSES", [])
            total_bosses = len(boss_list) if boss_list else 5

            next_boss_id = party['current_boss_id'] + 1
            if next_boss_id > total_bosses:
                next_boss_id = 1
                
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
        # ここで修復ロジックが走るため、以降は party_row が確実に取得できる
        self._check_and_reset_weekly_boss(cur)
        
        party_row = cur.execute("SELECT * FROM party_state WHERE id = 1").fetchone()
        # 万が一のSafety Check
        if not party_row:
            logger.error("CRITICAL: Failed to initialize party_state even after check.")
            return {
                "damage": damage,
                "remainingHp": 0,
                "isDefeated": False,
                "isNewDefeat": False
            }

        party = {k: party_row[k] for k in party_row.keys()}

        current_hp = party['current_hp']
        is_defeated = party['is_defeated']
        
        if is_defeated:
            return {
                "damage": damage,
                "remainingHp": 0,
                "isDefeated": True,
                "isNewDefeat": False
            }
            
        new_hp = max(0, current_hp - damage)
        new_defeated = 1 if new_hp == 0 else 0
        
        cur.execute("""
            UPDATE party_state 
            SET current_hp = ?, 
                total_damage = total_damage + ?, 
                is_defeated = ?,
                updated_at = ?
            WHERE id = 1
        """, (new_hp, damage, new_defeated, common.get_now_iso()))
        
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

    def calculate_quest_boost(self, cur, user_id: str, quest: Any) -> Dict[str, int]:
        # 修正: 型ヒントを dict から Any (sqlite3.Row) へ変更し、実態に合わせる
        
        # 1. クエストタイプのチェック
        # sqlite3.Row は辞書のように [] でアクセス可能です
        if quest['quest_type'] != 'daily':
            return {"gold": 0, "exp": 0}
        
        # 2. 曜日指定のチェック (修正箇所)
        # 原因: DB生データには 'days' キーがなく、'day_of_week' カラムが存在する。
        # また sqlite3.Row に .get() は存在しないためAttributeErrorになる。
        # 修正: 'day_of_week' カラムの値を確認する。値が入っていれば曜日限定なのでブースト対象外。
        if quest['day_of_week']: 
            return {"gold": 0, "exp": 0}

        # --- 以下、既存ロジック ---
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
            
            # 大人
            result = self._apply_quest_rewards(cur, user, quest, now_iso, override_rewards={"gold": total_gold, "exp": total_exp})
            
            atk_power = self._calculate_user_attack_power(cur, user_id)
            is_critical = random.random() < 0.1
            crit_multiplier = 1.5 if is_critical else 1.0
            damage_value = int((total_exp + atk_power) * crit_multiplier)
            
            boss_effect = self._apply_boss_damage(cur, damage_value)
            
            # Safety Guard
            if boss_effect:
                boss_effect['isCritical'] = is_critical
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
            
            override_rewards = {
                "gold": hist['gold_earned'],
                "exp": hist['exp_earned']
            }

            result = self._apply_quest_rewards(cur, user, quest, common.get_now_iso(), history_id=history_id, override_rewards=override_rewards)
            
            attacker_id = hist['user_id']
            atk_power = self._calculate_user_attack_power(cur, attacker_id)
            is_critical = random.random() < 0.1
            crit_multiplier = 1.5 if is_critical else 1.0
            
            base_damage = override_rewards['exp']
            damage_value = int((base_damage + atk_power) * crit_multiplier)
            
            boss_effect = self._apply_boss_damage(cur, damage_value)
            
            # Safety Guard
            if boss_effect:
                boss_effect['isCritical'] = is_critical
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
        
        try:
            cur.execute("UPDATE party_state SET charge_gauge = charge_gauge + 1 WHERE id = 1")
        except Exception:
            pass

        if leveled_up:
            sound_manager.play("level_up")
        elif is_lucky:
            sound_manager.play("medal_get")
        elif not history_id:
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
    
    def get_weekly_analytics(self) -> Dict[str, Any]:
        try:
            with common.get_db_cursor() as cur:
                # 1. 期間計算 (今週の月曜〜現在)
                now = datetime.datetime.now()
                today_date = now.date()
                start_date = today_date - datetime.timedelta(days=today_date.weekday()) # Monday
                start_str = start_date.strftime("%Y-%m-%d")
                
                # 2. ユーザー情報の取得
                users = {row['user_id']: dict(row) for row in cur.execute("SELECT user_id, name, avatar FROM quest_users").fetchall()}
                
                # 3. クエスト履歴データの取得 (承認済み)
                sql_quest = """
                    SELECT user_id, exp_earned, gold_earned, completed_at, quest_title
                    FROM quest_history 
                    WHERE status = 'approved' AND date(completed_at) >= ?
                    ORDER BY completed_at ASC
                """
                quest_logs = cur.execute(sql_quest, (start_str,)).fetchall()

                # 4. ごほうび購入履歴の取得
                sql_inv = """
                    SELECT user_id, count(*) as count
                    FROM inventory
                    WHERE date(purchased_at) >= ?
                    GROUP BY user_id
                """
                try:
                    inv_rows = cur.execute(sql_inv, (start_str,)).fetchall()
                except Exception:
                    # inventory テーブル名変更対応 (user_inventoryの場合あり)
                    sql_inv = sql_inv.replace("FROM inventory", "FROM user_inventory")
                    inv_rows = cur.execute(sql_inv, (start_str,)).fetchall()

                shopping_counts = {uid: 0 for uid in users.keys()}
                for row in inv_rows:
                    if row['user_id'] in shopping_counts:
                        shopping_counts[row['user_id']] = row['count']

                # 5. データ集計
                daily_map = {}
                for i in range(7):
                    d = start_date + datetime.timedelta(days=i)
                    d_str = d.strftime("%Y-%m-%d")
                    daily_map[d_str] = {uid: {"exp": 0, "gold": 0} for uid in users.keys()}

                total_stats = {uid: {"exp": 0, "gold": 0, "count": 0, "shopping": 0} for uid in users.keys()}
                quest_counts = {}

                for log in quest_logs:
                    ts = log['completed_at']
                    date_part = ts.split('T')[0] if 'T' in ts else ts.split(' ')[0]
                    uid = log['user_id']
                    
                    if uid not in users: continue
                    
                    if date_part in daily_map:
                        daily_map[date_part][uid]['exp'] += log['exp_earned']
                        daily_map[date_part][uid]['gold'] += log['gold_earned']
                    
                    total_stats[uid]['exp'] += log['exp_earned']
                    total_stats[uid]['gold'] += log['gold_earned']
                    total_stats[uid]['count'] += 1
                    
                    q_title = log['quest_title']
                    quest_counts[q_title] = quest_counts.get(q_title, 0) + 1

                for uid, count in shopping_counts.items():
                    if uid in total_stats:
                        total_stats[uid]['shopping'] = count

                # 6. レスポンス整形
                daily_stats_list = []
                weekdays = ["月", "火", "水", "木", "金", "土", "日"]
                cumulative = {uid: {"exp": 0, "gold": 0} for uid in users.keys()}
                
                for i in range(7):
                    d = start_date + datetime.timedelta(days=i)
                    d_str = d.strftime("%Y-%m-%d")
                    day_data = daily_map.get(d_str, {})
                    
                    current_snapshot = {}
                    for uid in users.keys():
                        val = day_data.get(uid, {"exp": 0, "gold": 0})
                        cumulative[uid]['exp'] += val['exp']
                        cumulative[uid]['gold'] += val['gold']
                        current_snapshot[uid] = cumulative[uid].copy()
                    
                    daily_stats_list.append({
                        "date": d_str,
                        "day_label": weekdays[i],
                        "users": current_snapshot
                    })

                def make_rank(key, label_suffix):
                    sorted_users = sorted(total_stats.items(), key=lambda x: x[1][key], reverse=True)
                    return [
                        {
                            "user_id": uid, 
                            "user_name": users[uid]['name'], 
                            "avatar": users[uid]['avatar'], 
                            "value": data[key],
                            "label": f"{data[key]}{label_suffix}"
                        } 
                        for uid, data in sorted_users if data[key] > 0
                    ]

                rankings = {
                    "exp": make_rank("exp", " XP"),
                    "gold": make_rank("gold", " G"),
                    "count": make_rank("count", " クエスト"),
                    "shopping": make_rank("shopping", " 個")
                }

                mvp_data = rankings['exp'][0] if rankings['exp'] else None
                popular_quest = max(quest_counts.items(), key=lambda x: x[1])[0] if quest_counts else "なし"

                return {
                    "startDate": start_str,
                    "endDate": datetime.datetime.now().strftime("%Y-%m-%d"),
                    "dailyStats": daily_stats_list,
                    "rankings": rankings,
                    "mvp": mvp_data,
                    "mostPopularQuest": popular_quest
                }
        except Exception as e:
            logger.error(f"Weekly Analytics Error: {e}")
            # エラー時も空データを返すことで500を防ぐ
            return {
                "startDate": "",
                "endDate": "",
                "dailyStats": [],
                "rankings": {"exp": [], "gold": [], "count": [], "shopping": []},
                "mvp": None,
                "mostPopularQuest": "エラー"
            }


class ShopService:
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
            
            now_iso = common.get_now_iso()

            cur.execute("""
                INSERT INTO reward_history (user_id, reward_id, reward_title, cost_gold, redeemed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, reward['reward_id'], reward['title'], reward['cost_gold'], now_iso))
            
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

            now_iso = common.get_now_iso()
            
            cur.execute("""
                UPDATE user_inventory 
                SET status = 'consumed', used_at = ? 
                WHERE id = ?
            """, (now_iso, inventory_id))

            log_title = f"アイテム使用: {item['title']}"
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES (?, 0, ?, 0, 0, ?, 'approved')
            """, (user_id, log_title, now_iso))

            msg = f"🎒 {item['user_name']}が「{item['title']}」を使用しました。"
            notification_service.send_push(
                user_id=config.LINE_USER_ID, 
                messages=[{"type": "text", "text": msg}]
            )
            sound_manager.play("quest_clear")

            return {"status": "consumed", "message": "アイテムを使いました！"}
    
    def consume_item(self, approver_id: str, inventory_id: int) -> Dict[str, str]:
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

            sound_manager.play("quest_clear") 
            
            return {"status": "consumed", "message": "承認しました"}

    def cancel_usage(self, user_id: str, inventory_id: int) -> Dict[str, str]:
        with common.get_db_cursor(commit=True) as cur:
            item = cur.execute("SELECT * FROM user_inventory WHERE id = ?", (inventory_id,)).fetchone()
            if not item: raise HTTPException(404, "Item not found")
            if item['user_id'] != user_id: raise HTTPException(403, "Not your item")
            if item['status'] != 'pending': raise HTTPException(400, "Not pending")

            cur.execute("UPDATE user_inventory SET status = 'owned', used_at = NULL WHERE id = ?", (inventory_id,))
            return {"status": "owned", "message": "リュックに戻しました"}
    
    def get_pending_items(self) -> List[dict]:
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
            if quest_data:
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
            else:
                logger.error("Quest data module not available for sync.")
                raise ImportError("quest_data module missing")
        except Exception as e:
            logger.error(f"❌ Master Data Validation failed: {e}")
            raise HTTPException(status_code=500, detail=f"Master Data Error: {str(e)}")
        
        with common.get_db_cursor(commit=True) as cur:
            # ★追加: マイグレーション処理 (role カラムの追加と初期化)
            try:
                cur.execute("SELECT role FROM quest_users LIMIT 1")
            except Exception:
                logger.info("⚠️ 'role' column missing in quest_users. Adding it now...")
                cur.execute("ALTER TABLE quest_users ADD COLUMN role TEXT")
                cur.execute("UPDATE quest_users SET role = 'role_adult' WHERE user_id IN ('dad', 'mom')")
                cur.execute("UPDATE quest_users SET role = 'role_child' WHERE user_id IN ('daughter', 'son', 'child')")

            # ★追加: マイグレーション処理 (reset_period カラムの追加)
            try:
                cur.execute("SELECT reset_period FROM quest_master LIMIT 1")
            except Exception:
                logger.info("⚠️ 'reset_period' column missing in quest_master. Adding it now...")
                cur.execute("ALTER TABLE quest_master ADD COLUMN reset_period TEXT DEFAULT 'weekly_monday'")

            for u in valid_users:
                role_val = getattr(u, 'role', None)
                cur.execute("""
                    INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, avatar, role, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        name = excluded.name, 
                        job_class = excluded.job_class,
                        role = COALESCE(excluded.role, quest_users.role)
                """, (u.user_id, u.name, u.job_class, u.level, u.exp, u.gold, u.avatar, role_val, datetime.datetime.now()))
            
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
                        start_time, end_time, pre_requisite_quest_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(quest_id) DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        quest_type = excluded.quest_type, target_user = excluded.target_user,
                        exp_gain = excluded.exp_gain, gold_gain = excluded.gold_gain, icon_key = excluded.icon_key,
                        day_of_week = excluded.day_of_week, start_time = excluded.start_time, end_time = excluded.end_time,
                        start_date = excluded.start_date, end_date = excluded.end_date, occurrence_chance = excluded.occurrence_chance,
                        pre_requisite_quest_id = excluded.pre_requisite_quest_id
                """, (
                    q.id, q.title, q.desc, q.type, q.target, q.exp, q.gold, q.icon,
                    q.days, 
                    q.start_date, q.end_date, 
                    q.chance, q.start_time, q.end_time,
                    q.pre_requisite_quest_id
                ))
            
            try:
                cur.execute("SELECT description FROM reward_master LIMIT 1")
            except Exception:
                logger.info("⚠️ 'description' column missing in reward_master. Adding it now...")
                cur.execute("ALTER TABLE reward_master ADD COLUMN description TEXT")

            active_r_ids = [r.id for r in valid_rewards]
            if active_r_ids:
                ph = ','.join(['?'] * len(active_r_ids))
                cur.execute(f"DELETE FROM reward_master WHERE reward_id NOT IN ({ph})", active_r_ids)
            else:
                cur.execute("DELETE FROM reward_master")
            
            for r in valid_rewards:
                cur.execute("""
                    INSERT INTO reward_master (reward_id, title, category, cost_gold, icon_key, description)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(reward_id) DO UPDATE SET
                        title = excluded.title, 
                        category = excluded.category,
                        cost_gold = excluded.cost_gold, 
                        icon_key = excluded.icon_key,
                        description = excluded.description
                """, (r.id, r.title, r.category, r.cost_gold, r.icon_key, r.desc))

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

            # 過去1ヶ月の完了履歴を取得して周期を判定する
            recent_completed = [dict(row) for row in cur.execute(
                "SELECT * FROM quest_history WHERE status='approved' AND completed_at >= date('now', '-1 month') ORDER BY completed_at DESC"
            )]
            pending = [dict(row) for row in cur.execute(
                "SELECT * FROM quest_history WHERE status='pending' ORDER BY completed_at DESC"
            )]

            # ユーザーマップ作成
            user_map = {u['user_id']: u['name'] for u in users}

            valid_completed = []
            quest_latest_history = {} # クエスト+ユーザー ごとの最新履歴
            for c in recent_completed:
                key = f"{c['quest_id']}_{c['user_id']}"
                if key not in quest_latest_history:
                    quest_latest_history[key] = c

            for q in filtered_quests:
                q_id = q['quest_id']
                reset_period = q.get('reset_period') or 'weekly_monday'
                
                # 自分や家族の履歴が現在の周期内かを判定
                for key, c in quest_latest_history.items():
                    if c['quest_id'] == q_id:
                        if self.quest_service.is_within_reset_period(c['completed_at'], reset_period):
                            valid_completed.append(c)

                # 共有クエスト(複数人ターゲット)の他者対応状況を判定
                target = q.get('target_user')
                if target and target.startswith('role_'):
                    completed_by_someone = next((c for c in valid_completed if c['quest_id'] == q_id), None)
                    if completed_by_someone:
                        q['is_shared_completed_by'] = completed_by_someone['user_id']
                        q['shared_completed_by_name'] = user_map.get(completed_by_someone['user_id'], '誰か')
                    else:
                        pending_by_someone = next((p for p in pending if p['quest_id'] == q_id), None)
                        if pending_by_someone:
                            q['is_shared_pending_by'] = pending_by_someone['user_id']
                            q['shared_pending_by_name'] = user_map.get(pending_by_someone['user_id'], '誰か')

            completed = valid_completed
           
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

    def _get_party_state(self, cur) -> Dict[str, Any]:
        self.quest_service._check_and_reset_weekly_boss(cur)
        
        try:
            row_obj = cur.execute("SELECT * FROM party_state WHERE id = 1").fetchone()
            if not row_obj: return None
            
            row = {k: row_obj[k] for k in row_obj.keys()}
            
            boss_list = getattr(quest_data, "BOSSES", [])
            boss_def = next((b for b in boss_list if b['id'] == row['current_boss_id']), None)
            
            if not boss_def:
                boss_def = {"id": 99, "name": "謎の影", "icon": "❓", "desc": "正体不明の敵", "hp": 1000}

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
# Singleton Instances
# ==========================================
game_system = GameSystem()
quest_service = game_system.quest_service
shop_service = game_system.shop_service
user_service = game_system.user_service
inventory_service = InventoryService()