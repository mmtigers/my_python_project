"""services/quest_service.py から分割(Issue #550)。"""
import datetime
import random
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from core.utils import get_now_iso
from core.database import get_db_cursor
from core import sound_manager
from core.jp_holidays import WEEKEND_DAYS, is_offday
from services.quest.locks import (
    INFINITE_QUEST_COOLDOWN_SECONDS,
    JST,
    ROLE_ADULT,
    ROLE_CHILD,
    SPAM_CHECK_INTERVAL_SECONDS,
    _get_completion_lock,
    _get_user_balance_lock,
    _seconds_since_iso_timestamp,
    logger,
)
from services.quest.rewards import apply_quest_rewards
from services.quest.user_service import UserService

# 「平日のみ」を表す曜日指定(月〜金すべて)。会社勤務・小学校に行く等、
# 学校/勤務そのものを表すクエストがこの形で登録されている。
WEEKDAY_ONLY_DAYS = frozenset({0, 1, 2, 3, 4})


def matches_day_of_week(today_date: datetime.date, days_list: list[int]) -> bool:
    """quest_master.day_of_week の曜日指定が今日に合致するかを返す。

    祝日(国民の祝日・振替休日・config.EXTRA_HOLIDAY_DATES の「家の休み」)は
    土日と同じ「休日」として扱う。ただし曜日指定の意味はクエストごとに違うため、
    一律に土日へ読み替えるのではなく次の3つに分ける:

    1. 今日の曜日が指定に含まれていれば合致する。祝日でも「ゴミ捨て(月・木)」の
       ように特定曜日に紐づく用事は通常どおり出す(自治体のゴミ収集は祝日も
       通常どおりのことが多い、という運用判断)。
    2. ただし「月〜金すべて」(=平日のみ)の指定だけは例外で、休日には出さない。
       これは「会社勤務(通常)」「小学校に行く」のような、学校/勤務がある日
       そのものを表すクエストで、祝日に出ると平日の画面になってしまう。
    3. 曜日は一致しないが土日(5と6の両方)を含む指定のクエストは、休日なら出す。
       「朝の会 開催」「洗車」「パパのお手伝い」等の休日向けクエストが該当する。
       日曜だけ(例: 習い事の連絡帳記入)のように片方だけの指定は、その曜日に
       実際に予定が紐づいているものなので祝日には広げない。
    """
    days = set(days_list)
    if today_date.weekday() in days:
        return not (days == WEEKDAY_ONLY_DAYS and is_offday(today_date))
    return WEEKEND_DAYS <= days and is_offday(today_date)


class QuestService:
    def is_within_reset_period(self, completed_at_str: str, reset_period: str) -> bool:
        if not completed_at_str:
            return False

        now_jst = datetime.datetime.now(JST)
        today_jst = now_jst.date()

        try:
            # DBの文字列をdatetimeオブジェクトへ変換
            dt = datetime.datetime.fromisoformat(completed_at_str)
            # M-1-4: タイムゾーン情報がない場合、以前はUTCとして記録されている
            # とみなしていたが、保存規約(get_now_iso)は常にJSTで記録する
            # ため、tzinfo無しの古いデータも実際はJSTで記録されている。
            # このファイル内の他の日時比較(スパムチェック等)もJSTとして扱っており、
            # UTCとみなす本実装だけが矛盾して9時間ズレていた。
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)

            completed_date = dt.astimezone(JST).date()
        except Exception:
            try:
                completed_date = datetime.datetime.strptime(completed_at_str.split(' ')[0], "%Y-%m-%d").date()
            except Exception:
                return False

        if reset_period == 'daily':
            return completed_date == today_jst
        elif reset_period == 'weekly':
            # 週の月曜日を基準にする
            start_of_week = today_jst - datetime.timedelta(days=today_jst.weekday())
            return completed_date >= start_of_week
        elif reset_period == 'monthly':
            # models.quest.MasterQuest.reset_period は 'monthly' を受け付けるのに、ここに分岐が
            # 無く常に False(=未完了扱い)になっていた。monthly のクエストは周期内に何度でも
            # 完了・多重報酬でき、completedQuests にも一切現れない状態だった(現行の
            # quest_data.py に monthly は無いが、追加した瞬間に発現する)。JST の暦月で判定する。
            return (completed_date.year, completed_date.month) == (today_jst.year, today_jst.month)

        # #446: 'daily'/'weekly'/'monthly' 以外の値(空文字・NULL・想定外の文字列等)は
        # 常に無効(未完了)扱いとなる。原因調査が難航しないよう、想定外の値を
        # 検知したことをログに残す。
        logger.warning(f"⚠️ is_within_reset_period: 未知のreset_period値 ({reset_period!r}) のため常にFalseを返します。")
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
        with get_db_cursor() as cur:
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
        with get_db_cursor() as cur:
            quest = cur.execute(
                "SELECT target_user FROM quest_master WHERE quest_id = ?", (quest_id,)
            ).fetchone()
        if quest and quest['target_user'] == 'siblings':
            return ('__coop__', quest_id)
        return (user_id, quest_id)

    def _process_complete_quest_locked(self, user_id: str, quest_id: int) -> Dict[str, Any]:
        with get_db_cursor(commit=True) as cur:
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

            # 前提クエスト(pre_requisite_quest_id)の達成チェック。フロントエンド
            # (useQuestStatus.getQuestLockState)は「前提クエストの今周期の承認済み履歴」が
            # 無ければカードをロックするが、サーバー側には対応する検査が無く、API直叩きで
            # ロックを素通りして報酬を得られた。フロントと同じ条件(承認済み・前提クエスト自身の
            # reset_period 内)で拒否する。前提クエストがマスタから消えている場合は daily 扱い。
            prereq_id = quest['pre_requisite_quest_id'] if 'pre_requisite_quest_id' in quest.keys() else None
            if prereq_id:
                prereq_quest = cur.execute(
                    "SELECT reset_period FROM quest_master WHERE quest_id = ?", (prereq_id,)
                ).fetchone()
                prereq_period = (prereq_quest['reset_period'] if prereq_quest else None) or 'daily'
                prereq_hist = cur.execute("""
                    SELECT completed_at FROM quest_history
                    WHERE user_id = ? AND quest_id = ? AND status = 'approved'
                    ORDER BY completed_at DESC LIMIT 1
                """, (user_id, prereq_id)).fetchone()
                if not (prereq_hist and prereq_hist['completed_at']
                        and self.is_within_reset_period(prereq_hist['completed_at'], prereq_period)):
                    raise HTTPException(status_code=403, detail="前提クエストがまだ達成されていません")

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
                    period_label = {"weekly": "今週", "monthly": "今月"}.get(reset_period, "本日")
                    raise HTTPException(status_code=400, detail=f"{period_label}はこのクエストを完了済みです")

            now_iso = get_now_iso()
            boost = self.calculate_quest_boost(cur, user_id, quest)
            total_exp = quest['exp_gain'] + boost['exp']
            total_gold = quest['gold_gain'] + boost['gold']

            # Q-M2 (#370): 承認(role == ROLE_ADULT必須)・購入(is_adult = role == ROLE_ADULT)は
            # 元々「ROLE_ADULTのみ大人扱い」だったが、完了処理だけが逆に「ROLE_CHILDのみ子ども扱い、
            # それ以外は大人扱い」だったため、role が NULL/未知のユーザー(migration 0001対象外の
            # user_idや、MasterUser.role=Noneで同期された行)は承認ゲート無しで即時報酬を得られていた。
            # オーナー判断により「不明 = 子ども」(安全側)に統一し、3経路とも
            # 「ROLE_ADULTのときだけ即時、それ以外はpending」の判定に揃える。
            if user['role'] != ROLE_ADULT:
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

    def _apply_quest_rewards(self, cur, user, quest, now_iso, history_id=None, override_rewards=None) -> Dict[str, Any]:
        """報酬付与の実体は `services/quest/rewards.py`(承認系と共有)。

        ここに薄いメソッドを残しているのは、テストが
        `monkeypatch.setattr(service, "_apply_quest_rewards", ...)` で差し込む
        seam をそのまま維持するため。
        """
        return apply_quest_rewards(
            cur, user, quest, now_iso, history_id=history_id, override_rewards=override_rewards
        )

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

        # #529: 以前は quest_type == 'limited' のときだけ期間を評価していたため、'special' 等に
        # start_date/end_date を設定しても無視されていた(期限切れでも出現し続ける)。設定が
        # あればタイプに関わらず評価する('limited' は「期間が本質」のタイプ名として残す)。
        # (quest は sqlite3.Row / dict のどちらも来るため keys() で列の有無を確認する)
        start_date = quest['start_date'] if 'start_date' in quest.keys() else None
        end_date = quest['end_date'] if 'end_date' in quest.keys() else None
        if start_date or end_date:
            try:
                if start_date:
                    y, m, d = map(int, start_date.split('-'))
                    if today_date < datetime.date(y, m, d):
                        return False
                if end_date:
                    y, m, d = map(int, end_date.split('-'))
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
            if not matches_day_of_week(today_date, days_list):
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
