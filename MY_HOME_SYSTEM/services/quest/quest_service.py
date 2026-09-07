"""services/quest_service.py から分割(Issue #550)。"""
import datetime
import random
import threading
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

import common
import config
import game_logic
from core import sound_manager
from services import notification_service, switchbot_service
from services.quest.locks import (
    INFINITE_QUEST_COOLDOWN_SECONDS,
    JST,
    ROLE_ADULT,
    ROLE_CHILD,
    SPAM_CHECK_INTERVAL_SECONDS,
    _acquire_user_balance_locks,
    _get_completion_lock,
    _get_user_balance_lock,
    _seconds_since_iso_timestamp,
    logger,
)
from services.quest.user_service import UserService


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

            now_iso = common.get_now_iso()
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
        # TV解錠(SwitchBot API 経由の副作用)はトランザクションのコミット後に起動する。
        # 以前は with ブロック内(コミット前)でスレッドを起動していたため、コミットが
        # 失敗(ディスクフル・ロック待ちタイムアウト等)して承認がロールバックされても
        # TVだけが点く可能性があった(Q-L7 は use_item 側だけを修正していた)。
        tv_unlock_quest_id: Optional[int] = None
        with common.get_db_cursor(commit=True) as cur:
            approver = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (approver_id,)).fetchone()
            if not approver or approver['role'] != ROLE_ADULT:
                raise HTTPException(status_code=403, detail="承認権限がありません")

            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist:
                raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending':
                raise HTTPException(status_code=400, detail="承認待ちではありません")

            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (hist['user_id'],)).fetchone()
            if not user:
                # #409: 履歴のユーザーがマスタから消えている場合、以前は _apply_quest_rewards 内で
                # TypeError → 500 になっていた(_approve_linked_history 側は None 返却で防御済み)。
                raise HTTPException(status_code=404, detail="User of this history not found")
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (hist['quest_id'],)).fetchone()

            # quest_history.gold_earned/exp_earned は NULL 許容列のため、サービス外で挿入された
            # 行を承認すると user['gold'] + None の TypeError → 500 になっていた。0 扱いにする。
            override_rewards = {
                "gold": hist['gold_earned'] or 0,
                "exp": hist['exp_earned'] or 0
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
                    tv_unlock_quest_id = quest['quest_id']

            logger.info(f"Child Quest Approved: Attacker={attacker_id}, Exp={override_rewards['exp']}, Gold={override_rewards['gold']}")

        if tv_unlock_quest_id is not None:
            self._trigger_tv_unlock(tv_unlock_quest_id)
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

        override_rewards = {"gold": linked_hist['gold_earned'] or 0, "exp": linked_hist['exp_earned'] or 0}
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
            if not hist:
                raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending':
                raise HTTPException(status_code=400, detail="承認待ちではありません")

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
            if not hist:
                raise HTTPException(status_code=404, detail="History not found")
            if hist['user_id'] != user_id:
                raise HTTPException(status_code=403, detail="User mismatch")

            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

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
            user['level'], user['exp'], hist['exp_earned'] or 0
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
