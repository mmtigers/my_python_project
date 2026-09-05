import datetime
import importlib
import math
import os
import random
import threading
from contextlib import ExitStack
from typing import List, Dict, Any, Optional, Tuple

from fastapi import HTTPException
import common
import config
import game_logic
from core import sound_manager
from services import notification_service, switchbot_service
from core.logger import setup_logging

# モデル定義のインポート (型ヒント用)
from models.quest import MasterUser, MasterQuest, MasterReward

# ロガー設定
logger = setup_logging("quest_service")

# JST(日本標準時、UTC+9固定・DSTなし)。is_within_reset_period/calculate_quest_boost/
# _is_quest_currently_active/filter_active_quests/get_all_view_data がそれぞれ
# 独立に「datetime.timezone(timedelta(hours=9))」または「pytz.timezone("Asia/Tokyo")」
# という2通りの異なる方法でJSTを組み立てていたため、この定数へ一本化する(Issue #293)。
# 標準ライブラリの固定オフセットtzinfoを採用する: pytzのtimezoneオブジェクトは
# datetime.replace(tzinfo=...)に直接使うと不正なオフセット(この地域ではLMT起源の
# +09:19)を返す既知の落とし穴があり、is_within_reset_period内でまさにreplace()に
# 渡している箇所があるため、datetime()/replace()/astimezone()/now()のいずれに
# 使っても常に正しい+09:00になる固定オフセット版のほうが安全。
JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')

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


# _process_complete_quest_locked のスパムチェック間隔(秒)。infiniteクエストのみ
# フロントエンド(family-quest QuestList.tsx)のクールダウン表示(60秒)と揃える(B2)。
SPAM_CHECK_INTERVAL_SECONDS = 10
INFINITE_QUEST_COOLDOWN_SECONDS = 60

# YouTube系ごほうび券(config.YOUTUBE_REWARD_IDS)を使用してから、次のYouTube系
# ごほうび券を使用できるまでのクールダウン(秒)。連続視聴による目の負担を防ぐ。
YOUTUBE_REWARD_COOLDOWN_SECONDS = 15 * 60


def _seconds_since_iso_timestamp(timestamp_str: Optional[str]) -> Optional[float]:
    """
    common.get_now_iso() で保存されたISOタイムスタンプ文字列から、現在までの
    経過秒数(実時間)を返す。パース失敗時・空文字/Noneの場合は None を返す。

    completed_at/redeemed_at 等は common.get_now_iso() によりJST付きで保存される。
    tzinfoを切り捨てて datetime.datetime.now()(サーバーのOSローカル時刻)と比較すると、
    サーバーのOSタイムゾーンがJST以外(例: GitHub ActionsのUTC)の場合に実時間で
    数秒しか経っていなくても差分が約9時間分ズレて算出されてしまう。tzinfoを
    保持したまま比較することで、サーバーのOSタイムゾーンに依存せず常に
    「実時間で何秒経過したか」を正しく判定する。
    """
    if not timestamp_str:
        return None
    try:
        last_time = datetime.datetime.fromisoformat(timestamp_str)
        if last_time.tzinfo is None:
            # tzinfoがない古いデータは、保存規約(common.get_now_iso)に合わせてJSTとみなす
            last_time = last_time.replace(tzinfo=JST)
        now_check = datetime.datetime.now(last_time.tzinfo)
        return (now_check - last_time).total_seconds()
    except Exception:
        return None


def _get_youtube_cooldown_remaining_seconds(cur, user_id: str) -> int:
    """
    直近でYouTube系ごほうび券(config.YOUTUBE_REWARD_IDS)を使用してから、
    次の1枚を使用できるようになるまでの残り秒数を返す。クールダウン対象IDが
    未設定、または対象IDを一度も使用していない場合は0を返す。
    """
    if not config.YOUTUBE_REWARD_IDS:
        return 0

    placeholders = ",".join("?" for _ in config.YOUTUBE_REWARD_IDS)
    row = cur.execute(f"""
        SELECT used_at FROM user_inventory
        WHERE user_id = ? AND status = 'consumed' AND reward_id IN ({placeholders})
        ORDER BY used_at DESC LIMIT 1
    """, (user_id, *config.YOUTUBE_REWARD_IDS)).fetchone()

    if not row or not row['used_at']:
        return 0

    elapsed = _seconds_since_iso_timestamp(row['used_at'])
    if elapsed is None:
        return 0

    remaining = YOUTUBE_REWARD_COOLDOWN_SECONDS - elapsed
    return max(0, math.ceil(remaining))


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
# User Balance Lock (Race Condition Guard for approve/cancel)
# ==========================================
# process_approve_quest / process_cancel_quest は「quest_usersをSELECT →
# Pythonでgold/exp/levelを計算 → UPDATE」というread-modify-writeのため、
# 同一ユーザーへの承認×承認・承認×取消が並行実行されると(例: 親が承認一覧を
# 連続タップするhandleApproveAll)、一方の更新が消失するレースが起こりうる。
# quest_users(gold/exp/level)を書き換える処理は、対象ユーザー単位でプロセス内
# 直列化する。
_user_balance_locks: Dict[str, threading.Lock] = {}
_user_balance_locks_guard = threading.Lock()


def _get_user_balance_lock(user_id: str) -> threading.Lock:
    with _user_balance_locks_guard:
        lock = _user_balance_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _user_balance_locks[user_id] = lock
        return lock


def _acquire_user_balance_locks(user_ids) -> ExitStack:
    # 兄妹連携クエストの承認/取消は、報告者だけでなく相方の quest_users
    # (gold/exp/level)も同一トランザクションで書き換える(Issue #98)。報告者の
    # ロックしか取得しないと、相方を対象とする別の承認/取消と並行実行された
    # 場合に相方側でlost updateが起こりうるため、関係する全ユーザーのロックを
    # まとめて取得する。複数ユーザーを同時にロックする際は、常に同じ順序
    # (user_idの昇順)で取得することで、対向のカスケード処理同士が互いの
    # ロックを取り合うデッドロックを防ぐ。
    stack = ExitStack()
    for uid in sorted(set(user_ids)):
        stack.enter_context(_get_user_balance_lock(uid))
    return stack


# ==========================================
# Purchase Lock (Race Condition Guard)
# ==========================================
# process_purchase_reward は残高チェックと減算を単一のアトミックなUPDATEで行うため
# read-then-writeのレースコンディション自体は起きないが、「直近の購入履歴を読む→
# 履歴を書く」というスパムチェック(#101)は他のスパムチェックと同様のTOCTOUを持つ。
# 購入確認モーダルの「はい」連打で、1回目のレスポンス前に2回目のリクエストが
# ほぼ同時に到達すると、どちらも「直近の購入履歴なし」を読んでしまいスパムチェックを
# すり抜け、残高が足りる限り2回とも独立した正当な購入として成立してしまう
# (ゴールド二重消費+アイテム二重取得)。process_complete_quest の完了ロックと
# 同様に、同一(user_id, reward_id)への処理をプロセス内で直列化する。
_purchase_locks: Dict[Tuple[str, int], threading.Lock] = {}
_purchase_locks_guard = threading.Lock()


def _get_purchase_lock(key: Tuple[str, int]) -> threading.Lock:
    with _purchase_locks_guard:
        lock = _purchase_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _purchase_locks[key] = lock
        return lock


# ==========================================
# Item Use Lock (Race Condition Guard for YouTube Cooldown)
# ==========================================
# use_item は「YouTube系ごほうび券の直近used_atを読む→クールダウン判定→consumedへ
# 更新」というTOCTOUを持つ。同一ユーザーが異なるYouTube系ごほうび券(reward_id違い、
# 例: 10:00券と30:00券)をほぼ同時に使用しようとすると、両リクエストがクールダウン
# なし(0秒)を読んでしまい、15分ロックをすり抜けて連続使用が成立し得る。
# ユーザー単位でuse_item全体をプロセス内で直列化し、このレースを防ぐ。
_item_use_locks: Dict[str, threading.Lock] = {}
_item_use_locks_guard = threading.Lock()


def _get_item_use_lock(user_id: str) -> threading.Lock:
    with _item_use_locks_guard:
        lock = _item_use_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _item_use_locks[user_id] = lock
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
            # process_reject_quest が却下履歴を残す(status='rejected')ようになったため、
            # 却下された申請を「達成したクエスト数」に含めないよう明示的に除外する。
            # Q-L5(#409): 承認待ち(pending)行と、use_item が quest_id=0 で挿入する
            # 「アイテム使用」行は達成クエスト数に含めない。
            res = cur.execute("SELECT COUNT(*) as count FROM quest_history WHERE status = 'approved' AND quest_id != 0").fetchone()
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

            old_avatar = user['avatar']

            cur.execute("UPDATE quest_users SET avatar = ?, updated_at = ? WHERE user_id = ?",
                       (avatar_url, common.get_now_iso(), user_id))

            # #372: 旧アバターのファイルを他のユーザーも参照している場合(同じ /uploads/ パスを
            # 指定された場合)、物理削除するとそのユーザーのアバターが404になる。
            # 他ユーザーからの参照が残っている限りファイルは削除しない。
            still_referenced = cur.execute(
                "SELECT 1 FROM quest_users WHERE avatar = ? AND user_id != ? LIMIT 1",
                (old_avatar, user_id),
            ).fetchone() is not None

            logger.info(f"Avatar Updated: User={user_id}, URL={avatar_url}")

        if not still_referenced:
            self._delete_orphaned_avatar(old_avatar, avatar_url)
        return {"status": "updated", "avatar": avatar_url}

    def _delete_orphaned_avatar(self, old_avatar: Optional[str], new_avatar: str) -> None:
        """アップロード済みの旧アバターファイルが差し替え後にディスクへ残り続けるのを防ぐ。
        絵文字などアップロードファイル以外の値や、他ユーザーと共有され得ない
        /uploads/ 配下のファイルのみを対象とし、パストラバーサルを避けるため
        ファイル名部分のみをUPLOAD_DIR基準で解決する。"""
        if not old_avatar or old_avatar == new_avatar:
            return
        if not old_avatar.startswith("/uploads/"):
            return

        filename = os.path.basename(old_avatar)
        file_path = os.path.join(config.UPLOAD_DIR, filename)
        if os.path.dirname(file_path) != os.path.normpath(config.UPLOAD_DIR):
            return

        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Orphaned avatar removed: {file_path}")
        except OSError as e:
            logger.warning(f"Failed to remove orphaned avatar {file_path}: {e}")


class QuestService:
    def is_within_reset_period(self, completed_at_str: str, reset_period: str) -> bool:
        if not completed_at_str: return False

        now_jst = datetime.datetime.now(JST)
        today_jst = now_jst.date()
        
        try:
            # DBの文字列をdatetimeオブジェクトへ変換
            dt = datetime.datetime.fromisoformat(completed_at_str)
            # M-1-4: タイムゾーン情報がない場合、以前はUTCとして記録されている
            # とみなしていたが、保存規約(common.get_now_iso)は常にJSTで記録する
            # ため、tzinfo無しの古いデータも実際はJSTで記録されている。
            # このファイル内の他の日時比較(スパムチェック等)もJSTとして扱っており、
            # UTCとみなす本実装だけが矛盾して9時間ズレていた。
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)

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

    def _compute_boost_from_last_completed(self, quest: Any, last_completed_at: Optional[str]) -> Dict[str, int]:
        """
        品質(#409 N+1対策): calculate_quest_boostのDBアクセスを伴わない純粋な計算部分。
        「対象クエストの直近の完了日時(last_completed_at、無ければNone)」さえ分かれば
        ボーナスを算出できるため、DBクエリ部分を呼び出し側へ切り出した。
        get_all_view_dataのようにクエスト×ユーザーの組合せ数だけ呼ぶ場面では、
        呼び出し側で全組合せ分の直近完了日時を1クエリでまとめて取得し、この関数へ
        渡すことでN+1クエリを避けられる。単発呼び出し(process_complete_quest)は
        従来どおりcalculate_quest_boost経由でDBへ1回だけ問い合わせる。
        """
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

        # Q-L11(#409): quest_type='daily'だがreset_period='weekly'(=週1回の
        # ペースで達成すればよいクエスト)の場合、以下の「連続達成ボーナス」は
        # 直近の完了日からの経過日数を「サボった日数」とみなして加点する設計のため、
        # 正常に毎週1回のペースで完了しているだけでも days_diff が常に約7となり
        # 毎回+60%相当のボーナスが付与されてしまっていた(潜在バグ・未発火)。
        # このボーナスはdaily(毎日実施が前提)クエストのみを対象とする。
        if (quest['reset_period'] or 'daily') != 'daily':
            return {"gold": 0, "exp": 0}

        # M-1-3系: is_within_reset_periodと同様、経過日数の判定はJST基準で
        # 行う必要がある。以前はdatetime.datetime.now()(OSローカル時刻)を
        # 使っており、サーバーOSのタイムゾーンがJST以外だとJST 0時〜9時の間の
        # 判定でdays_diffが1小さくなる不具合があった。
        now_jst = datetime.datetime.now(JST)
        last_date = None

        if last_completed_at:
            try:
                dt = datetime.datetime.fromisoformat(last_completed_at)
                last_date = dt.date()
            except Exception:
                pass

        if not last_date:
            return {"gold": 0, "exp": 0}

        today_date = now_jst.date()
        days_diff = (today_date - last_date).days

        if days_diff <= 1:
            return {"gold": 0, "exp": 0}

        missed_days = days_diff - 1
        bonus_ratio = min(missed_days * 0.10, 1.0)
        bonus_gold = int(quest['gold_gain'] * bonus_ratio)
        bonus_exp = int(quest['exp_gain'] * bonus_ratio)

        return {"gold": bonus_gold, "exp": bonus_exp}

    def calculate_quest_boost(self, cur, user_id: str, quest: Any) -> Dict[str, int]:
        # 修正: 型ヒントを dict から Any (sqlite3.Row) へ変更し、実態に合わせる

        # ボーナス対象外と分かっているクエスト(daily以外・曜日限定・reset_period≠daily)は
        # DBに問い合わせるまでもないため、_compute_boost_from_last_completed側の
        # 早期returnガードに先に判定させ、無駄なSELECTを避ける。
        if quest['quest_type'] != 'daily' or quest['day_of_week'] or (quest['reset_period'] or 'daily') != 'daily':
            return {"gold": 0, "exp": 0}

        # Q-L1(#409): 以前は status='approved' のみを見ていたため、承認待ち(pending)の日を
        # 「サボった日」と誤判定して連続達成ボーナスが付いていた。process_complete_quest の
        # スパム/周期チェックと同じく rejected 以外を「実施済み」として扱う。
        last_hist = cur.execute("""
            SELECT completed_at FROM quest_history
            WHERE user_id = ? AND quest_id = ? AND status != 'rejected'
            ORDER BY completed_at DESC LIMIT 1
        """, (user_id, quest['quest_id'])).fetchone()

        last_completed_at = last_hist['completed_at'] if last_hist else None
        return self._compute_boost_from_last_completed(quest, last_completed_at)

    def process_complete_quest(self, user_id: str, quest_id: int) -> Dict[str, Any]:
        # 同一ユーザー・同一クエストへの同時多重リクエストによる二重加算を防ぐため、
        # DBトランザクションの外側でプロセス内ロックを取得して処理全体を直列化する。
        #
        # completion lock は (user_id, quest_id) 単位のため、同一ユーザーが
        # 異なる quest_id をほぼ同時に完了すると別々のロックキーとなり並行実行される。
        # 大人の即時完了パス(_apply_quest_rewards)は quest_users(gold/exp/level)への
        # read-modify-write を伴うため、これだけでは対象ユーザーの残高更新が
        # 並行実行から保護されず lost update が起こり得る(Issue #161)。
        # quest_users を書き換えうる全経路(承認・取消・完了)が対象ユーザー単位で
        # 直列化されるよう、completion lock とは独立に user balance lock も取得する。
        # ロック取得順序は常に balance lock → completion/purchase lock に統一し、
        # 経路間のデッドロックを防ぐ。
        # Q-L10(#409): ロック辞書は user_id ごとにエントリが増え、プロセス再起動まで解放されない。
        # 存在しない user_id でロックを作らないよう、ロック取得前に存在確認する
        # (存在チェック後に取得するロック内で改めて検証されるため二重チェックは無害)。
        with common.get_db_cursor() as cur:
            exists = cur.execute("SELECT 1 FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="User not found")

        with _get_user_balance_lock(user_id):
            with _get_completion_lock(self._get_completion_lock_key(user_id, quest_id)):
                return self._process_complete_quest_locked(user_id, quest_id)

    def _get_completion_lock_key(self, user_id: str, quest_id: int) -> Tuple[str, int]:
        # 兄妹連携クエスト(target_user='siblings')は、兄・妹どちらが完了報告しても
        # 同じロックキーで直列化する必要がある。ここを (user_id, quest_id) のままにすると
        # 報告者ごとにロックキーが分かれてしまい、兄妹がほぼ同時に報告した場合、双方の
        # 処理が互いのロック取得を待たずに _process_coop_quest_completion まで進んでしまい、
        # pendingペア(quest_history 2行×2組)が二重生成されて承認時に報酬が2倍になる。
        # そのため、対象クエストが兄妹連携クエストの場合はユーザーIDに依存しない
        # 共通キーを使って直列化する。
        with common.get_db_cursor() as cur:
            quest = cur.execute(
                "SELECT target_user FROM quest_master WHERE quest_id = ?", (quest_id,)
            ).fetchone()
        if quest and quest['target_user'] == 'siblings':
            return ('__coop__', quest_id)
        return (user_id, quest_id)

    def _process_complete_quest_locked(self, user_id: str, quest_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (quest_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()

            if not quest or not user:
                raise HTTPException(status_code=404, detail="Not found")

            # 対象者・出現条件のサーバー側検証(Issue #163)。
            # filter_active_quests(GET /dataの表示整形専用)にしか無かった判定を、
            # API直叩きでバイパスして他人向けクエスト・時間帯外・曜日外・
            # 未出現のrandomクエストを完了できてしまう穴を塞ぐ。報酬購入側は
            # Issue #95で同種のサーバー側targetチェックを追加済みだったが、
            # 完了側には未展開のまま残っていた。
            # target_user は 'all'/本人のuser_id/'siblings'(role_childのみ)の
            # いずれかのみ許可する。'siblings'をrole_adultが完了すると、
            # _process_coop_quest_completionを経由せず単独即時報酬になってしまう
            # (兄妹連携クエストの前提を破る)ため、これも合わせて拒否する。
            is_sibling_target = quest['target_user'] == 'siblings'
            if quest['target_user'] not in ('all', user_id) and not (
                is_sibling_target and user['role'] == ROLE_CHILD
            ):
                raise HTTPException(status_code=403, detail="This quest is not available for you")
            if not self._is_quest_currently_active(quest):
                raise HTTPException(status_code=403, detail="This quest is not currently available")

            # スパムチェック
            last_hist = cur.execute("""
                SELECT completed_at FROM quest_history 
                WHERE user_id = ? AND quest_id = ? AND status != 'rejected'
                ORDER BY completed_at DESC LIMIT 1
            """, (user_id, quest['quest_id'])).fetchone()

            if last_hist and last_hist['completed_at']:
                elapsed = _seconds_since_iso_timestamp(last_hist['completed_at'])
                # B2: infiniteクエストはフロントエンド(QuestList.tsx)が60秒のクールダウンを
                # UIとして提示しているが、サーバー側は全クエスト共通の10秒間隔しか強制していなかった
                # ため、リロードやAPI直叩きで実質10秒間隔まで周回できてしまっていた。
                # infiniteのみフロントの意図(60秒)に合わせてサーバー側の下限も引き上げる。
                min_interval_seconds = INFINITE_QUEST_COOLDOWN_SECONDS if quest['quest_type'] == 'infinite' else SPAM_CHECK_INTERVAL_SECONDS
                if elapsed is not None and elapsed < min_interval_seconds:
                    raise HTTPException(status_code=429, detail="少し時間を空けてから実行してください")

            # M-1-3: daily/weekly の周期リセットをサーバー側でも強制する。
            # is_within_reset_period は元々 get_all_view_data の表示専用
            # (completedQuests算出)にしか使われておらず、上の10秒スパムチェックだけでは
            # API直叩き等で同一クエストを周期内に何度でも完了・多重報酬できてしまっていた。
            # 'infinite' タイプ(「何回でも挑戦しよう」等)は仕様上多重完了が前提のため対象外。
            if quest['quest_type'] != 'infinite' and last_hist and last_hist['completed_at']:
                reset_period = quest['reset_period'] or 'daily'
                if self.is_within_reset_period(last_hist['completed_at'], reset_period):
                    period_label = "今週" if reset_period == 'weekly' else "本日"
                    raise HTTPException(status_code=400, detail=f"{period_label}はこのクエストを完了済みです")

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

    def _get_lock_user_ids_for_history(
        self, history_id: int, primary_user_id: Optional[str] = None
    ) -> List[str]:
        """history_idに対応するquest_history行から、ロック対象ユーザーID一覧を求める。
        兄妹連携クエスト(linked_history_id あり)の場合は相方のuser_idも含める(#98)。

        process_approve_quest/process_reject_quest/process_cancel_questがそれぞれ
        個別に実装していた「対象履歴をpeekして相方を辿り、ロック対象ユーザーを
        まとめる」ロジックを一元化したもの(Issue #293)。

        primary_user_idを指定しない場合はquest_history.user_idから取得し、履歴が
        見つからなければ404を送出する(process_approve_quest/process_reject_quest
        の従来の挙動)。指定した場合はそれを主対象としてそのまま使い、履歴が
        見つからなくても404は送出しない(process_cancel_questの従来の挙動:
        存在確認自体は_process_cancel_quest_locked側に委ねる)。
        """
        with common.get_db_cursor() as cur:
            hist_peek = cur.execute(
                "SELECT user_id, linked_history_id FROM quest_history WHERE id = ?", (history_id,)
            ).fetchone()

        if primary_user_id is not None:
            lock_user_ids = [primary_user_id]
        else:
            if not hist_peek:
                raise HTTPException(status_code=404, detail="History not found")
            lock_user_ids = [hist_peek['user_id']]

        if hist_peek and hist_peek['linked_history_id'] is not None:
            with common.get_db_cursor() as cur:
                linked_peek = cur.execute(
                    "SELECT user_id FROM quest_history WHERE id = ?", (hist_peek['linked_history_id'],)
                ).fetchone()
            if linked_peek:
                lock_user_ids.append(linked_peek['user_id'])

        return lock_user_ids

    def process_approve_quest(self, approver_id: str, history_id: int) -> Dict[str, Any]:
        # ロック対象ユーザー(quest_historyの本来の完了者。gold/exp更新の対象)を
        # 先に特定してから、そのユーザー単位でロックを取得する。兄妹連携クエスト
        # (linked_history_id あり)の場合は、承認時に相方の quest_users も
        # カスケードして書き換えるため、相方のユーザーIDも合わせてロックする(#98)。
        lock_user_ids = self._get_lock_user_ids_for_history(history_id)
        with _acquire_user_balance_locks(lock_user_ids):
            return self._process_approve_quest_locked(approver_id, history_id)

    def _process_approve_quest_locked(self, approver_id: str, history_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            approver = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (approver_id,)).fetchone()
            if not approver or approver['role'] != ROLE_ADULT:
                raise HTTPException(status_code=403, detail="承認権限がありません")

            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist: raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending': raise HTTPException(status_code=400, detail="承認待ちではありません")

            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (hist['user_id'],)).fetchone()
            if not user:
                # #409: 履歴のユーザーがマスタから消えている場合、以前は _apply_quest_rewards 内で
                # TypeError → 500 になっていた(_approve_linked_history 側は None 返却で防御済み)。
                raise HTTPException(status_code=404, detail="User of this history not found")
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (hist['quest_id'],)).fetchone()

            override_rewards = {
                "gold": hist['gold_earned'],
                "exp": hist['exp_earned']
            }

            result = self._apply_quest_rewards(cur, user, quest, common.get_now_iso(), history_id=history_id, override_rewards=override_rewards)

            attacker_id = hist['user_id']

            # --- 兄妹連携クエスト: 連結された相方の履歴も同一トランザクションでカスケード承認 ---
            # #238: _approve_linked_historyは相方のgold/exp/level/medalを正しく
            # 付与していたが戻り値が無く(-> None)、レスポンスに一切含まれないため
            # フロント側は相方のレベルアップ/メダル獲得演出を出しようがなかった。
            if hist['linked_history_id'] is not None:
                partner_result = self._approve_linked_history(cur, hist['linked_history_id'])
                if partner_result:
                    result['partnerUserId'] = partner_result['user_id']
                    result['partnerLeveledUp'] = partner_result['leveledUp']
                    result['partnerNewLevel'] = partner_result['newLevel']
                    result['partnerEarnedMedals'] = partner_result['earnedMedals']

            # --- TV Lock Feature ---
            # quest はマスタから削除された quest_id の pending 履歴を承認する場合 None になり得る
            # (sync_master_data の DELETE ... NOT IN でマスタ行が消えても quest_history は残るため)。
            if quest and quest['quest_id'] in config.TV_UNLOCK_QUEST_IDS and config.TV_PLUG_DEVICE_ID:
                if user['role'] == ROLE_CHILD:
                    self._trigger_tv_unlock(quest['quest_id'])

            logger.info(f"Child Quest Approved: Attacker={attacker_id}, Exp={override_rewards['exp']}, Gold={override_rewards['gold']}")
            return result

    def _approve_linked_history(self, cur, linked_history_id: int) -> Optional[Dict[str, Any]]:
        """兄妹連携クエストの相方側 quest_history 行を承認済みに確定する(冪等)。

        #238: 戻り値で相方のuser_idと_apply_quest_rewardsの結果(leveledUp/newLevel/
        earnedMedals等)を返す。呼び出し元(_process_approve_quest_locked)がこれを
        レスポンスへ含めることで、フロント側が相方のレベルアップ/メダル獲得演出を
        出せるようにするため。
        """
        linked_hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (linked_history_id,)).fetchone()
        if not linked_hist or linked_hist['status'] != 'pending':
            return None

        linked_user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (linked_hist['user_id'],)).fetchone()
        linked_quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (linked_hist['quest_id'],)).fetchone()
        if not linked_user:
            return None

        override_rewards = {"gold": linked_hist['gold_earned'], "exp": linked_hist['exp_earned']}
        reward_result = self._apply_quest_rewards(cur, linked_user, linked_quest, common.get_now_iso(), history_id=linked_history_id, override_rewards=override_rewards)
        logger.info(f"Coop Partner Approved: User={linked_hist['user_id']}, HistoryID={linked_history_id}")
        return {"user_id": linked_hist['user_id'], **reward_result}

    def _trigger_tv_unlock(self, quest_id: int):
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
        # #228: process_approve_quest と同じユーザー単位ロックに参加させる。
        # 以前はここでロックを一切取得していなかったため、同一history_idに対する
        # 承認と却下がほぼ同時に実行されると、承認側が先にquest_usersへgold/expを
        # 加算・コミットした後に却下のUPDATEがコミットされ、quest_history.statusは
        # 'rejected'になるのに付与済みの報酬は一切ロールバックされない不整合が
        # 生じていた。兄妹連携クエスト(linked_history_id あり)の場合は、相方の
        # quest_users もカスケードして書き換えるため相方のユーザーIDも合わせて
        # ロックする(process_approve_quest/process_cancel_questと同じ理由、#98)。
        lock_user_ids = self._get_lock_user_ids_for_history(history_id)
        with _acquire_user_balance_locks(lock_user_ids):
            return self._process_reject_quest_locked(approver_id, history_id, reason)

    def _process_reject_quest_locked(self, approver_id: str, history_id: int, reason: Optional[str] = None) -> Dict[str, str]:
        with common.get_db_cursor(commit=True) as cur:
            approver = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (approver_id,)).fetchone()
            if not approver or approver['role'] != ROLE_ADULT:
                raise HTTPException(status_code=403, detail="承認権限がありません")

            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist: raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending': raise HTTPException(status_code=400, detail="承認待ちではありません")

            # 却下履歴を残す(以前はDELETEしていたため status='rejected' が実際には
            # 生成されず、process_complete_quest のスパムチェック `status != 'rejected'`
            # が常に成立する死に条件になっていた)。
            # #228: 主対象のUPDATEにも AND status = 'pending' を付ける(連結相方向けの
            # 更新には元々付いていたが主対象には無い非対称な実装だった)。ロック取得に
            # よりこの行の承認/却下は既に直列化されているため二重の安全策ではあるが、
            # UPDATE自体を「pendingのままなら却下」という条件付きにすることで、
            # 万一チェックとUPDATEの間に状態が変化しても却下確定を防ぐ。
            cur.execute("UPDATE quest_history SET status = 'rejected' WHERE id = ? AND status = 'pending'", (history_id,))

            # --- 兄妹連携クエスト: 連結された相方の履歴も同一トランザクションでカスケード却下 ---
            if hist['linked_history_id'] is not None:
                cur.execute("UPDATE quest_history SET status = 'rejected' WHERE id = ? AND status = 'pending'", (hist['linked_history_id'],))
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
        
        # Q-L3(#409): メダルドロップ数も履歴(medals_earned、migration 0009)に記録し、
        # キャンセル時に _revert_and_delete_history が戻せるようにする。
        if history_id:
            # completed_at は子供が完了報告した時刻のまま維持する(承認時刻で上書きしない)。
            # 上書きしていた旧実装では、承認が翌日(weeklyなら翌週)にずれた場合に
            # process_complete_quest のスパムチェック/周期リセット判定が「本日(今週)完了済み」
            # と誤判定し、翌日分の完了報告ができなくなる不具合があった(#93)。
            cur.execute("UPDATE quest_history SET status='approved', gold_earned=?, exp_earned=?, medals_earned=? WHERE id=?",
                       (earned_gold, earned_exp, earned_medals, history_id))
        else:
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, medals_earned, completed_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'approved')
            """, (user['user_id'], quest['quest_id'], quest['title'], earned_exp, earned_gold, earned_medals, now_iso))

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
        # 兄妹連携クエスト(linked_history_id あり)の場合は、取消時に相方の
        # quest_users もカスケードしてロールバックするため、相方のユーザーIDも
        # 合わせてロックする(#98)。history_id が不正/他人の履歴の場合の404/403は
        # 従来どおり _process_cancel_quest_locked 側で検出される。
        lock_user_ids = self._get_lock_user_ids_for_history(history_id, primary_user_id=user_id)
        with _acquire_user_balance_locks(lock_user_ids):
            return self._process_cancel_quest_locked(user_id, history_id)

    def _process_cancel_quest_locked(self, user_id: str, history_id: int) -> Dict[str, str]:
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
        quest_history 1行を取り消す。approved であれば付与済みの経験値・ゴールドを
        ロールバックしてから削除する。pending / rejected は報酬がまだ付与されて
        いないため、残高には触れず単純に削除する(#97: 以前は status == 'pending'
        以外を一律「付与済み」とみなしてロールバックしていたため、rejected 履歴を
        cancel すると、もらっていない経験値・ゴールドが残高から減算されていた)。
        """
        if hist['status'] != 'approved':
            cur.execute("DELETE FROM quest_history WHERE id = ?", (hist['id'],))
            return

        # #356: 以前は max(0, gold - gold_earned) で 0 に飽和させていたため、付与された
        # ゴールドを報酬購入で使い切った後に履歴をキャンセルすると残高が減らず、
        # 再完了で再び付与される「無限ゴールド」が成立していた。付与済みゴールドを
        # 既に消費している(残高 < 付与額)場合は取り消し自体を拒否し、キャンセルが
        # 常に「付与の完全な巻き戻し」になることを保証する。
        gold_earned = hist['gold_earned'] or 0
        current_gold = user['gold'] or 0
        if current_gold < gold_earned:
            raise HTTPException(
                status_code=400,
                detail="獲得したゴールドを既に使用しているため、このクエストは取り消せません",
            )

        new_level, new_exp = game_logic.GameLogic.calc_level_down(
            user['level'], user['exp'], hist['exp_earned']
        )
        new_gold = current_gold - gold_earned
        # Q-L3(#409): メダルも戻す(履歴に記録が無い古い行は 0 扱い)
        medals_earned = (hist['medals_earned'] if 'medals_earned' in hist.keys() else 0) or 0

        cur.execute("UPDATE quest_users SET level=?, exp=?, gold=?, medal_count = MAX(0, medal_count - ?), updated_at=? WHERE user_id=?",
                    (new_level, new_exp, new_gold, medals_earned, common.get_now_iso(), user['user_id']))
        cur.execute("DELETE FROM quest_history WHERE id = ?", (hist['id'],))

    def _is_quest_currently_active(self, quest, now: Optional[datetime.datetime] = None) -> bool:
        """quest_master 1行(dict/sqlite3.Row。どちらも `[]` でのアクセスに対応)が
        「今」出現・実行可能な条件(limited型の期間・random型の出現抽選・時間帯・曜日)を
        満たすかを判定する。filter_active_quests(GET /dataの表示フィルタ)と
        _process_complete_quest_locked(完了APIのサーバー側検証、Issue #163)の
        両方から呼ばれる共通ロジック。表示上出現していないクエストがAPI直叩きで
        完了できてしまう食い違いを防ぐため、判定基準を完全に一致させている。"""
        now = now or datetime.datetime.now(JST)
        today_date = now.date()
        current_time_str = now.strftime("%H:%M")

        if quest['quest_type'] == 'limited':
            try:
                if quest['start_date']:
                    y, m, d = map(int, quest['start_date'].split('-'))
                    if today_date < datetime.date(y, m, d):
                        return False
                if quest['end_date']:
                    y, m, d = map(int, quest['end_date'].split('-'))
                    if today_date > datetime.date(y, m, d):
                        return False
            except ValueError as e:
                logger.warning(f"Date parse error for quest {quest['quest_id']}: {e}")
                return False

        if quest['quest_type'] == 'random':
            seed = f"{now.strftime('%Y-%m-%d')}_{quest['quest_id']}"
            # #241: occurrence_chanceがNoneの場合、float > Noneの比較でTypeErrorになる。
            # DBスキーマ(quest_master.occurrence_chance DEFAULT 1.0)とmodels/quest.pyの
            # 既定値(Optional[float] = 1.0)に合わせ、Noneは「常に出現」扱いにする。
            occurrence_chance = quest['occurrence_chance'] if quest['occurrence_chance'] is not None else 1.0
            if random.Random(seed).random() > occurrence_chance:
                return False

        if quest['start_time'] and quest['end_time']:
            if quest['start_time'] <= quest['end_time']:
                if not (quest['start_time'] <= current_time_str <= quest['end_time']):
                    return False
            else:
                if not (current_time_str >= quest['start_time'] or current_time_str <= quest['end_time']):
                    return False

        if quest['day_of_week']:
            days_list = [int(d) for d in quest['day_of_week'].split(',')]
            if today_date.weekday() not in days_list:
                return False

        return True

    def filter_active_quests(self, quests: List[dict]) -> List[dict]:
        filtered = []
        now = datetime.datetime.now(JST)

        for q in quests:
            if not self._is_quest_currently_active(q, now):
                continue

            # #291: quest_master由来の値そのまま(icon_key/quest_type/target_user)を
            # 正とし、以前ここで追加していた icon/type/target というフィールド名の
            # 二重化(useGameData.tsからの起点調査で発覚)は廃止した。
            q['days'] = [int(d) for d in q['day_of_week'].split(',')] if q['day_of_week'] else None
            filtered.append(q)
        return filtered
    

class ShopService:
    def process_purchase_reward(self, user_id: str, reward_id: int) -> Dict[str, Any]:
        # 同一ユーザー・同一報酬への同時多重リクエスト(購入確認モーダルの連打等)による
        # 二重購入を防ぐため、DBトランザクションの外側でプロセス内ロックを取得して
        # 処理全体を直列化する(#101)。
        #
        # purchase lock は (user_id, reward_id) 単位の直列化に過ぎず、
        # process_approve_quest/process_cancel_quest が保持する user balance lock
        # とは独立している。購入はゴールドをアトミックな "gold = gold - ?" で減算する
        # ため read-modify-write レース自体は起きないが、承認/取消は
        # "SELECT→Pythonで計算→絶対値でSET" のため、購入のUPDATEコミット後に
        # 承認/取消が古いgoldを基準にした絶対値SETを行うと、購入による減算が
        # 上書きされて消失する(Issue #161)。quest_users を書き換えうる全経路
        # (承認・取消・完了・購入)が対象ユーザー単位で直列化されるよう、
        # purchase lock とは独立に user balance lock も取得する。
        # ロック取得順序は常に balance lock → completion/purchase lock に統一し、
        # 経路間のデッドロックを防ぐ。
        with _get_user_balance_lock(user_id):
            with _get_purchase_lock((user_id, reward_id)):
                return self._process_purchase_reward_locked(user_id, reward_id)

    def _process_purchase_reward_locked(self, user_id: str, reward_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            reward = cur.execute("SELECT * FROM reward_master WHERE reward_id = ?", (reward_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()

            if not reward: raise HTTPException(status_code=404, detail="Reward not found")
            if not user: raise HTTPException(status_code=404, detail="User not found")

            # スパムチェック(#101): 購入確認モーダルの「はい」連打で、1回目のレスポンス前に
            # 2回目のリクエストが送られると、ロックが無ければサーバー側は各リクエストを
            # 独立した正当な購入として処理してしまい、残高が足りれば2回とも成功して
            # 二重購入(ゴールド二重消費+アイテム二重取得)が成立する。process_complete_quest
            # と同じ「直近10秒以内の同一操作は拒否」というスパムチェックを行う。
            last_purchase = cur.execute("""
                SELECT redeemed_at FROM reward_history
                WHERE user_id = ? AND reward_id = ?
                ORDER BY redeemed_at DESC LIMIT 1
            """, (user_id, reward_id)).fetchone()

            if last_purchase and last_purchase['redeemed_at']:
                elapsed = _seconds_since_iso_timestamp(last_purchase['redeemed_at'])
                if elapsed is not None and elapsed < 10:
                    raise HTTPException(status_code=429, detail="少し時間を空けてから実行してください")

            target = reward['target'] or 'all'
            if target != 'all':
                is_adult = user['role'] == ROLE_ADULT
                allowed = (
                    (target == 'children' and not is_adult) or
                    (target == 'adults' and is_adult) or
                    (target == user_id)
                )
                if not allowed:
                    raise HTTPException(status_code=403, detail="This reward is not available for you")

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
    def get_user_inventory(self, user_id: str) -> Dict[str, Any]:
        with common.get_db_cursor() as cur:
            sql = """
                SELECT ui.id, ui.reward_id, ui.status, ui.purchased_at, ui.used_at,
                       rm.title, rm.description as desc, rm.icon_key as icon, rm.category
                FROM user_inventory ui
                JOIN reward_master rm ON ui.reward_id = rm.reward_id
                WHERE ui.user_id = ? AND ui.status = 'owned'
                ORDER BY ui.purchased_at DESC
            """
            rows = cur.execute(sql, (user_id,)).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                # フロントエンド(InventoryList.tsx)がYouTube系ごほうび券のクールダウン
                # UIを出し分けられるよう、判定ロジックはconfig側に集約したままフラグだけ渡す。
                item['is_youtube_reward'] = item['reward_id'] in config.YOUTUBE_REWARD_IDS
                items.append(item)

            youtube_cooldown_remaining_seconds = _get_youtube_cooldown_remaining_seconds(cur, user_id)

        return {
            "items": items,
            "youtube_cooldown_remaining_seconds": youtube_cooldown_remaining_seconds,
        }

    def use_item(self, user_id: str, inventory_id: int) -> Dict[str, str]:
        with _get_item_use_lock(user_id):
            return self._use_item_locked(user_id, inventory_id)

    def _use_item_locked(self, user_id: str, inventory_id: int) -> Dict[str, str]:
        """
        アイテムを使用し、即座に消費を確定する(親の承認は不要)。
        """
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

            # 連続視聴による目の負担を防ぐため、YouTube系ごほうび券は前回使用から
            # YOUTUBE_REWARD_COOLDOWN_SECONDS(15分)経過するまで再使用できないようにする。
            if item['reward_id'] in config.YOUTUBE_REWARD_IDS:
                cooldown_remaining = _get_youtube_cooldown_remaining_seconds(cur, user_id)
                if cooldown_remaining > 0:
                    remaining_minutes = math.ceil(cooldown_remaining / 60)
                    raise HTTPException(
                        429,
                        f"YouTubeのごほうび券は、目を休めるためあと{remaining_minutes}分ほど使えません",
                    )

            now_iso = common.get_now_iso()

            # #369: SELECT→Python判定→無条件UPDATE では、WALで読み取りがブロックされない
            # ため連打された2リクエストが両方 'owned' を読み、両方が消費処理・履歴INSERT・
            # 通知を実行していた(二重使用)。status='owned' を条件に含めた条件付きUPDATEに
            # し、rowcount==0(先行リクエストが既に消費済み)なら400で拒否する。
            cur.execute("""
                UPDATE user_inventory
                SET status = 'consumed', used_at = ?
                WHERE id = ? AND status = 'owned'
            """, (now_iso, inventory_id))
            if cur.rowcount == 0:
                raise HTTPException(400, "Cannot use this item")

            log_title = f"アイテム使用: {item['title']}"
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES (?, 0, ?, 0, 0, ?, 'approved')
            """, (item['user_id'], log_title, now_iso))

            msg = f"🎒 {item['user_name']}が「{item['title']}」を使用しました。"

        # 外部副作用(LINE送信・効果音)はコミット後に実行する。以前はトランザクション内で
        # LINE APIの往復を待っていたため、その間SQLiteの書き込みロックを保持し続け、
        # 他のwriterが "database is locked" 待ちになっていた(Q-L7)。
        notification_service.send_push(
            user_id=config.LINE_USER_ID,
            messages=[{"type": "text", "text": msg}]
        )
        sound_manager.play("quest_clear")

        return {"status": "consumed", "message": "つかいました！"}


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
            # Issue #330: 以前ここにあった「SELECTを試して失敗したらALTER TABLE」式の
            # レガシー実行時マイグレーション(role/reset_period/descriptionカラムの追加)は
            # 完全退役した。スキーマは migrations/ 配下(0000ベースライン+0001以降)が
            # 唯一の定義元であり、unified_serverのlifespanとinit_db()の双方が起動時に
            # apply_pending_migrations() を適用するため、本メソッド到達時点で
            # これらのカラムは必ず存在する。
            for u in valid_users:
                role_val = getattr(u, 'role', None)
                cur.execute("""
                    INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, avatar, role, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        name = excluded.name, 
                        job_class = excluded.job_class,
                        role = COALESCE(excluded.role, quest_users.role)
                """, (u.user_id, u.name, u.job_class, u.level, u.exp, u.gold, u.avatar, role_val, common.get_now_iso()))
            
            active_q_ids = [q.id for q in valid_quests]
            if active_q_ids:
                ph = ','.join(['?'] * len(active_q_ids))
                cur.execute(f"DELETE FROM quest_master WHERE quest_id NOT IN ({ph})", active_q_ids)
            else:
                # #242: quest_data.QUESTSが空(コーディングミス等)になった瞬間、
                # 以前は無条件でDELETE FROM quest_masterを実行し全クエストマスタが
                # 消えていた。reward_master側の「参照が残っている行は削除をスキップする」
                # 安全弁と同様、意図しない全消去を防ぐため削除自体をスキップする。
                logger.warning(
                    "⚠️ quest_data.QUESTSが空のため、quest_masterへの全削除操作を"
                    "スキップしました(意図しない全消去を防ぐための安全弁)。"
                )

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
            
            active_r_ids = [r.id for r in valid_rewards]
            if active_r_ids:
                ph = ','.join(['?'] * len(active_r_ids))
                stale_rewards = cur.execute(
                    f"SELECT reward_id FROM reward_master WHERE reward_id NOT IN ({ph})", active_r_ids
                ).fetchall()
            else:
                stale_rewards = cur.execute("SELECT reward_id FROM reward_master").fetchall()

            # user_inventory は reward_master(reward_id) へのFK(PRAGMA foreign_keys=ON)を持つため、
            # 所持者がいる(所有中/申請中/使用済問わずuser_inventoryに行が残る)報酬を削除すると
            # IntegrityErrorでsync_master_data全体が失敗する。参照が残っている報酬は削除をスキップし、
            # 警告ログのみ出す(マスタからは消えているが所持データは保持される)。
            # 品質(#409 N+1対策): 以前はstale_rewards1件ごとに個別SELECTを発行していた。
            # 対象のreward_id群についてuser_inventory側の参照有無を1クエリでまとめて
            # 取得し、以降はPython側の集合演算で判定する。
            stale_reward_ids = [row['reward_id'] for row in stale_rewards]
            if stale_reward_ids:
                ph_stale = ','.join(['?'] * len(stale_reward_ids))
                referenced_reward_ids = {
                    row['reward_id'] for row in cur.execute(
                        f"SELECT DISTINCT reward_id FROM user_inventory WHERE reward_id IN ({ph_stale})",
                        stale_reward_ids,
                    )
                }
            else:
                referenced_reward_ids = set()

            for stale_reward_id in stale_reward_ids:
                if stale_reward_id in referenced_reward_ids:
                    logger.warning(
                        f"⚠️ reward_id={stale_reward_id} はマスタから削除されましたが、"
                        "user_inventoryに参照が残っているため削除をスキップします。"
                    )
                    continue
                cur.execute("DELETE FROM reward_master WHERE reward_id = ?", (stale_reward_id,))
            
            for r in valid_rewards:
                cur.execute("""
                    INSERT INTO reward_master (reward_id, title, category, cost_gold, icon_key, description, target)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(reward_id) DO UPDATE SET
                        title = excluded.title,
                        category = excluded.category,
                        cost_gold = excluded.cost_gold,
                        icon_key = excluded.icon_key,
                        description = excluded.description,
                        target = excluded.target
                """, (r.id, r.title, r.category, r.cost_gold, r.icon_key, r.desc, r.target))

        logger.info("✅ Master data sync completed.")
        return {"status": "synced", "message": "Master data updated."}

    def get_all_view_data(self, viewer_user_id: Optional[str] = None) -> Dict[str, Any]:
        with common.get_db_cursor() as cur:
            users = [dict(row) for row in cur.execute("SELECT * FROM quest_users")]

            # SQLiteは "SELECT * FROM quest_users" にORDER BYが無いと、user_idが
            # TEXT PRIMARY KEYであるため主キーのアルファベット順(dad, daughter, mom, son)
            # で返すことがあり、quest_data.USERSの宣言順(dad, mom, son, daughter)と
            # 一致しない。family-quest側の App.tsx は users[currentUserIdx] という
            # 配列インデックスでタブと現在のユーザーを対応づけているため、この順序の
            # 食い違いがあるとタブの位置と実際に表示される家族が入れ替わってしまう
            # (例: 「ともや」のタブに寝かしつけ(mom/dad向け)クエストが出る)。
            # quest_data.USERS の宣言順を唯一の正としてソートし直すことで、
            # DBの内部的な返却順に依存しないようにする。
            if quest_data:
                canonical_order = {u['user_id']: i for i, u in enumerate(quest_data.USERS)}
                users.sort(key=lambda u: canonical_order.get(u['user_id'], len(canonical_order)))

            for u in users:
                u['nextLevelExp'] = game_logic.GameLogic.calculate_next_level_exp(u['level'])
                u['maxHp'] = game_logic.GameLogic.calculate_max_hp(u['level'])
                u['hp'] = u['maxHp']

            all_quests = [dict(row) for row in cur.execute("SELECT * FROM quest_master")]
            filtered_quests = self.quest_service.filter_active_quests(all_quests)

            # quest_master.target_user は実際の quest_users.user_id (例: 'dad')の他に、
            # 'siblings' のようなグループ指定も取りうる。後者を calculate_quest_boost に
            # そのまま user_id として渡すと quest_history に一致行が存在しないため、
            # 実際の履歴に関わらずボーナスが常に0固定になっていた(実害はないが意味が誤り)。
            # target_user が実在ユーザーでない場合は、閲覧中のユーザー(viewer_user_id)の
            # 履歴を代表として使う。
            known_user_ids = {u['user_id'] for u in users}
            # F-L6(#412): target_user='siblings'(兄妹連携)クエストは、_process_coop_quest_completion
            # が兄妹2人分のquest_historyを同一completed_atで必ずセットで作成するため、
            # どちらの子のuser_idで見ても連続達成ボーナスは同じ結果になるはずだが、
            # 以前は下のelse節でviewer_user_idにフォールバックしていたため、viewer_user_idが
            # 兄妹のどちらでもない場合(親が閲覧中、または横画面4分割ビューでviewer_user_idが
            # 常にusers[0]固定になる場合。Echo Show等)、その閲覧者にはこのクエストの
            # quest_history行が一切無いため連続達成ボーナスが常に0固定になっていた。
            # 兄妹のいずれか(以下の実装では最初に見つかった方)のuser_idを使う。
            sibling_child_ids = [u['user_id'] for u in users if u.get('role') == ROLE_CHILD]

            # 品質(#409 N+1対策): 以前はここでクエストごとにcalculate_quest_boostを呼び、
            # クエストごとにquest_historyへの個別SELECTを発行していた(GET /data 1回で
            # クエスト数分のクエリが発生)。対象となりうる全(user_id, quest_id)組合せの
            # 「直近の非rejected完了日時」を1クエリでまとめて取得し、辞書引きに
            # 置き換える。completed_atはcore.utils.get_now_iso()(常にJSTのisoformat、
            # 固定長・ゼロ埋め)で記録されるため、文字列としてのMAX()が時系列上の
            # 最新値と一致する(calculate_quest_boost個別呼び出し版のORDER BY DESC
            # LIMIT 1と同じ前提)。
            last_completed_map: Dict[tuple, str] = {
                (row['user_id'], row['quest_id']): row['last_completed_at']
                for row in cur.execute("""
                    SELECT user_id, quest_id, MAX(completed_at) AS last_completed_at
                    FROM quest_history
                    WHERE status != 'rejected'
                    GROUP BY user_id, quest_id
                """)
            }

            for q in filtered_quests:
                # Q-L2(#409): target_user='all' の daily クエストも、完了時には閲覧ユーザーの
                # 履歴に基づくボーナスが付くのに、表示側は常に 0 固定だった。'all' の場合は
                # 閲覧中のユーザー(viewer_user_id)の履歴で算出する。
                if q['target_user'] == 'all' or not q['target_user']:
                    boost_user_id = viewer_user_id
                elif q['target_user'] == 'siblings' and sibling_child_ids:
                    boost_user_id = sibling_child_ids[0]
                else:
                    boost_user_id = q['target_user'] if q['target_user'] in known_user_ids else viewer_user_id
                if boost_user_id:
                    last_completed_at = last_completed_map.get((boost_user_id, q['quest_id']))
                    boost = self.quest_service._compute_boost_from_last_completed(q, last_completed_at)
                    q['bonus_gold'] = boost['gold']
                    q['bonus_exp'] = boost['exp']
                else:
                    q['bonus_gold'] = 0
                    q['bonus_exp'] = 0

            rewards = [dict(row) for row in cur.execute("SELECT * FROM reward_master")]
            for r in rewards:
                # #291: icon/cost という重複フィールド名の付与(icon_key/cost_gold
                # の別名)を廃止し、DBの実カラム名に一本化する。desc は
                # description の同期用レガシー列(sync_strict.py参照)であり、
                # このビュー応答では description のみを正としてdesc自体を落とす。
                r.pop('desc', None)

            # 過去1ヶ月の完了履歴を取得して周期を判定する
            # ※SQLiteの date('now') はUTC基準のため、Python側でJSTの閾値文字列を生成する
            # JST は固定オフセットの timezone なので now()/strftime が失敗することはない
            # (#409: 到達不能な try/except フォールバックを削除)
            now_jst = datetime.datetime.now(JST)
            one_month_ago = (now_jst - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

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