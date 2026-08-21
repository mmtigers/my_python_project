import datetime
import importlib
import random
import math
import threading
import pytz
from typing import List, Dict, Any, Optional, Tuple

from fastapi import HTTPException
import common
import config
import game_logic
from core import sound_manager
from services import notification_service
from core.logger import setup_logging

# モデル定義のインポート (型ヒント用)
from models.quest import MasterUser, MasterQuest, MasterReward

# ロガー設定
logger = setup_logging("quest_service")

# quest_users.role の値 (親権限判定はこの2値のみを唯一の判定基準とする)
ROLE_ADULT = 'role_adult'
ROLE_CHILD = 'role_child'

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
# Completion Lock (Race Condition Guard)
# ==========================================
# process_complete_quest は「直近履歴を読む→報酬を書く」という手順のため、
# 同一(user_id, quest_id)への同時リクエスト（クライアントのリトライ・二重タップ等）が
# 別スレッドでほぼ同時に到達すると、どちらも「直近の完了履歴なし」を読んでしまい、
# 経験値・ゴールド・ボスダメージが二重に加算されるレースコンディションが発生しうる。
# そのため、同一キーへの処理はプロセス内で直列化する。
_completion_locks: Dict[Tuple[str, int], threading.Lock] = {}
_completion_locks_guard = threading.Lock()


def _get_completion_lock(key: Tuple[str, int]) -> threading.Lock:
    with _completion_locks_guard:
        lock = _completion_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _completion_locks[key] = lock
        return lock


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

        all_events = sorted(q_rows + r_rows, key=lambda x: x['ts'], reverse=True)[:100]
        user_info = {row['user_id']: {"name": row['name'], "avatar": row['avatar']} for row in cur.execute("SELECT user_id, name, avatar FROM quest_users")}

        formatted = []
        for ev in all_events:
            u = user_info.get(ev['user_id'], {"name": "旅人", "avatar": "👤"})
            text = ""
            if ev['type'] == 'quest': text = f"{u['name']}は {ev['title']} を達成した！"
            elif ev['type'] == 'reward': text = f"{u['name']}は {ev['title']} を獲得した！"

            formatted.append({
                "type": ev['type'], "userId": ev['user_id'], "userName": u['name'], "userAvatar": u['avatar'],
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
    def is_within_reset_period(self, completed_at_str: str, reset_period: str) -> bool:
        if not completed_at_str: return False
        
        import datetime
        # 外部ライブラリを使わず、標準機能でJST（+9時間）を定義
        JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')
        now_jst = datetime.datetime.now(JST)
        today_jst = now_jst.date()
        
        try:
            # DBの文字列をdatetimeオブジェクトへ変換
            dt = datetime.datetime.fromisoformat(completed_at_str)
            # タイムゾーン情報がない（UTCとして記録されている）場合、UTCとみなしてJSTに変換
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            
            completed_date = dt.astimezone(JST).date()
        except Exception:
            try:
                completed_date = datetime.datetime.strptime(completed_at_str.split(' ')[0], "%Y-%m-%d").date()
            except:
                return False

        if reset_period == 'daily':
            return completed_date == today_jst
        elif reset_period == 'weekly':
            # 週の月曜日を基準にする
            start_of_week = today_jst - datetime.timedelta(days=today_jst.weekday())
            return completed_date >= start_of_week
        
        return False

    def __init__(self):
        self.user_service = UserService()

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
        # 同一ユーザー・同一クエストへの同時多重リクエストによる二重加算を防ぐため、
        # DBトランザクションの外側でプロセス内ロックを取得して処理全体を直列化する。
        with _get_completion_lock((user_id, quest_id)):
            return self._process_complete_quest_locked(user_id, quest_id)

    def _process_complete_quest_locked(self, user_id: str, quest_id: int) -> Dict[str, Any]:
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
                    # completed_at は common.get_now_iso() によりJST付きで保存される。
                    # 以前はここで tzinfo を切り捨てた上で datetime.datetime.now()(サーバーのOSローカル時刻)
                    # と比較していたため、サーバーのOSタイムゾーンがJST以外(例: GitHub ActionsのUTC)だと
                    # 実時間で10秒経過しても差分が約9時間分ズレたままになり、同じクエストが
                    # 約9時間もの間 429 (「少し時間を空けてから」)で完了できなくなる不具合があった。
                    # tzinfoを保持したまま比較することで、サーバーのOSタイムゾーンに依存せず
                    # 常に「実時間で10秒経過したか」を正しく判定する。
                    if last_time.tzinfo is None:
                        # tzinfoがない古いデータは、保存規約(common.get_now_iso)に合わせてJSTとみなす
                        last_time = last_time.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
                    now_check = datetime.datetime.now(last_time.tzinfo)

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
            
            if user['role'] == ROLE_CHILD:
                if quest['target_user'] == 'siblings':
                    return self._process_coop_quest_completion(cur, user, quest, now_iso, total_exp, total_gold)

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
            logger.info(f"Adult Quest Completed: User={user_id}, Exp={total_exp}, Gold={total_gold}")
            return result

    def _get_sibling_partner_id(self, cur, user_id: str) -> str:
        """
        兄妹連携クエスト(target_user='siblings')の相方の user_id を返す。
        現状の家族構成では role_child のユーザーがちょうど2人(兄・妹)いることを前提とする。
        """
        rows = cur.execute("SELECT user_id FROM quest_users WHERE role = ?", (ROLE_CHILD,)).fetchall()
        child_ids = [row['user_id'] for row in rows]
        if user_id not in child_ids or len(child_ids) != 2:
            raise HTTPException(status_code=400, detail="兄妹クエストの対象ユーザー構成が不正です")
        return next(uid for uid in child_ids if uid != user_id)

    def _process_coop_quest_completion(self, cur, user, quest, now_iso: str, total_exp: int, total_gold: int) -> Dict[str, Any]:
        """
        兄妹連携クエスト: どちらか一方が完了報告すると、2人分の quest_history 行(共に pending)を
        作成し、互いを linked_history_id で連結する。承認は1回のタップで2人分同時に確定する。
        """
        partner_id = self._get_sibling_partner_id(cur, user['user_id'])

        cur.execute("""
            INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """, (user['user_id'], quest['quest_id'], quest['title'], total_exp, total_gold, now_iso))
        reporter_history_id = cur.lastrowid

        cur.execute("""
            INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status, linked_history_id)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (partner_id, quest['quest_id'], quest['title'], total_exp, total_gold, now_iso, reporter_history_id))
        partner_history_id = cur.lastrowid

        cur.execute("UPDATE quest_history SET linked_history_id = ? WHERE id = ?", (partner_history_id, reporter_history_id))

        logger.info(f"Coop Quest Pending: Reporter={user['user_id']}, Partner={partner_id}, Quest={quest['title']}")
        sound_manager.play("submit")

        return {
            "status": "pending",
            "leveledUp": False, "newLevel": user['level'],
            "earnedGold": 0, "earnedExp": 0, "earnedMedals": 0,
            "message": "親の承認待ちです（兄妹クエスト）"
        }

    def process_approve_quest(self, approver_id: str, history_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            approver = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (approver_id,)).fetchone()
            if not approver or approver['role'] != ROLE_ADULT:
                raise HTTPException(status_code=403, detail="承認権限がありません")

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

            # --- 兄妹連携クエスト: 連結された相方の履歴も同一トランザクションでカスケード承認 ---
            if hist['linked_history_id'] is not None:
                self._approve_linked_history(cur, hist['linked_history_id'])

            # --- TV Lock Feature ---
            if quest['quest_id'] in config.TV_UNLOCK_QUEST_IDS and config.TV_PLUG_DEVICE_ID:
                if user['role'] == ROLE_CHILD:
                    self._trigger_tv_unlock(quest['quest_id'])

            logger.info(f"Child Quest Approved: Attacker={attacker_id}, Exp={override_rewards['exp']}, Gold={override_rewards['gold']}")
            return result

    def _approve_linked_history(self, cur, linked_history_id: int) -> None:
        """兄妹連携クエストの相方側 quest_history 行を承認済みに確定する(冪等)。"""
        linked_hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (linked_history_id,)).fetchone()
        if not linked_hist or linked_hist['status'] != 'pending':
            return

        linked_user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (linked_hist['user_id'],)).fetchone()
        linked_quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (linked_hist['quest_id'],)).fetchone()
        if not linked_user:
            return

        override_rewards = {"gold": linked_hist['gold_earned'], "exp": linked_hist['exp_earned']}
        self._apply_quest_rewards(cur, linked_user, linked_quest, common.get_now_iso(), history_id=linked_history_id, override_rewards=override_rewards)
        logger.info(f"Coop Partner Approved: User={linked_hist['user_id']}, HistoryID={linked_history_id}")

    def _trigger_tv_unlock(self, quest_id: int):
        import threading
        from services import switchbot_service
        from services import notification_service
        
        def unlock_task():
            logger.info(f"📺 Initiating TV Unlock (Turn ON) for quest_id: {quest_id}")
            try:
                res = switchbot_service.send_device_command(config.TV_PLUG_DEVICE_ID, "turnOn")
                if res and res.get("statusCode") == 100:
                    logger.info("✅ TV Unlock successful.")
                else:
                    raise Exception(f"API returned error: {res}")
            except Exception as e:
                logger.error(f"❌ TV Unlock failed: {e}")
                # Fail-Soft: エラー時は親グループへ通知
                if config.LINE_PARENTS_GROUP_ID:
                    msg = "⚠️ テレビの電源ON（自動ロック解除）に失敗しました。お手数ですが、SwitchBotアプリ等から手動でつけてあげてください。"
                    notification_service.send_push(
                        user_id=config.LINE_PARENTS_GROUP_ID,
                        messages=[{"type": "text", "text": msg}]
                    )
        
        # APIコールでAPIルーティング（メインスレッド）をブロックしないよう非同期で実行
        t = threading.Thread(target=unlock_task, daemon=True)
        t.start()
    
    def process_reject_quest(self, approver_id: str, history_id: int, reason: Optional[str] = None) -> Dict[str, str]:
        with common.get_db_cursor(commit=True) as cur:
            approver = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (approver_id,)).fetchone()
            if not approver or approver['role'] != ROLE_ADULT:
                raise HTTPException(status_code=403, detail="承認権限がありません")

            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist: raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending': raise HTTPException(status_code=400, detail="承認待ちではありません")

            cur.execute("DELETE FROM quest_history WHERE id = ?", (history_id,))

            # --- 兄妹連携クエスト: 連結された相方の履歴も同一トランザクションでカスケード却下 ---
            if hist['linked_history_id'] is not None:
                cur.execute("DELETE FROM quest_history WHERE id = ? AND status = 'pending'", (hist['linked_history_id'],))
                logger.info(f"Coop Partner Rejected: HistoryID={hist['linked_history_id']}")

            logger.info(f"Quest Rejected: Approver={approver_id}, Target={hist['user_id']}, Reason={reason or '(未指定)'}")
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

            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            if not user: raise HTTPException(status_code=404, detail="User not found")

            self._revert_and_delete_history(cur, hist, user)

            # --- 兄妹連携クエスト: 連結された相方の履歴も同一トランザクションでカスケード取り消し ---
            linked_id = hist['linked_history_id']
            if linked_id is not None:
                linked_hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (linked_id,)).fetchone()
                if linked_hist:
                    linked_user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (linked_hist['user_id'],)).fetchone()
                    if linked_user:
                        self._revert_and_delete_history(cur, linked_hist, linked_user)
                        logger.info(f"Coop Partner Cancelled: HistoryID={linked_id}")

            logger.info(f"Quest Cancelled: User={user_id}, HistoryID={history_id}")
        return {"status": "cancelled"}

    def _revert_and_delete_history(self, cur, hist, user) -> None:
        """
        quest_history 1行を取り消す。pending であれば単純に削除、approved であれば
        付与済みの経験値・ゴールドをロールバックしてから削除する。
        """
        if hist['status'] == 'pending':
            cur.execute("DELETE FROM quest_history WHERE id = ?", (hist['id'],))
            return

        new_level, new_exp = game_logic.GameLogic.calc_level_down(
            user['level'], user['exp'], hist['exp_earned']
        )
        new_gold = max(0, user['gold'] - hist['gold_earned'])

        cur.execute("UPDATE quest_users SET level=?, exp=?, gold=?, updated_at=? WHERE user_id=?",
                    (new_level, new_exp, new_gold, common.get_now_iso(), user['user_id']))
        cur.execute("DELETE FROM quest_history WHERE id = ?", (hist['id'],))

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

            # 残高チェックと減算を単一のアトミックなUPDATEにすることで、
            # 同時多重リクエストによる read-then-write のレースコンディション
            # (二重購入でゴールドが1回分しか減らない不具合) を防ぐ。
            cur.execute(
                "UPDATE quest_users SET gold = gold - ?, updated_at = ? WHERE user_id = ? AND gold >= ?",
                (reward['cost_gold'], common.get_now_iso(), user_id, reward['cost_gold'])
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=400, detail="Not enough gold")

            new_gold = cur.execute(
                "SELECT gold FROM quest_users WHERE user_id = ?", (user_id,)
            ).fetchone()['gold']
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
        with common.get_db_cursor(commit=True) as cur:
            approver = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (approver_id,)).fetchone()
            if not approver or approver['role'] != ROLE_ADULT:
                raise HTTPException(403, "承認権限がありません")

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
                        start_time, end_time, pre_requisite_quest_id, reset_period
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(quest_id) DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        quest_type = excluded.quest_type, target_user = excluded.target_user,
                        exp_gain = excluded.exp_gain, gold_gain = excluded.gold_gain, icon_key = excluded.icon_key,
                        day_of_week = excluded.day_of_week, start_time = excluded.start_time, end_time = excluded.end_time,
                        start_date = excluded.start_date, end_date = excluded.end_date, occurrence_chance = excluded.occurrence_chance,
                        pre_requisite_quest_id = excluded.pre_requisite_quest_id,
                        reset_period = excluded.reset_period
                """, (
                    q.id, q.title, q.desc, q.type, q.target, q.exp, q.gold, q.icon,
                    q.days,
                    q.start_date, q.end_date,
                    q.chance, q.start_time, q.end_time,
                    q.pre_requisite_quest_id, q.reset_period
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
            # ※SQLiteの date('now') はUTC基準のため、Python側でJSTの閾値文字列を生成する
            try:
                now_jst = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
                one_month_ago = (now_jst - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
            except Exception as jst_err:
                # 万が一のタイムゾーンエラーに対する防御型フォールバック（Safety Guard）
                logger.error(f"❌ Failed to calculate JST time for analytics: {jst_err}")
                now_native = datetime.datetime.now()
                one_month_ago = (now_native - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

            recent_completed = [dict(row) for row in cur.execute(
                "SELECT * FROM quest_history WHERE status='approved' AND completed_at >= ? ORDER BY completed_at DESC",
                (one_month_ago,)
            )]

            pending = [dict(row) for row in cur.execute(
                "SELECT * FROM quest_history WHERE status='pending' ORDER BY completed_at DESC"
            )]

            # ユーザーマップ作成
            user_map = {u['user_id']: u['name'] for u in users}

            valid_completed = []

            for q in filtered_quests:
                q_id = q['quest_id']
                reset_period = q.get('reset_period') or 'daily'
                is_infinite = (q.get('quest_type') == 'infinite')
                
                if is_infinite:
                    # 無限クエストは条件を満たす全履歴を追加
                    for c in recent_completed:
                        if c['quest_id'] == q_id:
                            if self.quest_service.is_within_reset_period(c['completed_at'], reset_period):
                                valid_completed.append(c)
                else:
                    # 通常クエストの場合、ユーザーごとに最新の履歴を評価する
                    users_processed = set()
                    for c in recent_completed:
                        if c['quest_id'] == q_id:
                            uid = c['user_id']
                            if uid not in users_processed:
                                if self.quest_service.is_within_reset_period(c['completed_at'], reset_period):
                                    valid_completed.append(c)
                                # 期間外であっても最新履歴を処理済みにし、同ユーザーの過去履歴検索を終了する
                                users_processed.add(uid)

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

        return {
            "users": users, "quests": filtered_quests, "rewards": rewards,
            "completedQuests": completed, "logs": logs,
            "pendingQuests": pending,
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

# ==========================================
# Singleton Instances
# ==========================================
game_system = GameSystem()
quest_service = game_system.quest_service
shop_service = game_system.shop_service
user_service = game_system.user_service
inventory_service = InventoryService()