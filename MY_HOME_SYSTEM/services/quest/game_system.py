"""services/quest_service.py から分割(Issue #550)。"""
import collections
import datetime
import importlib
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from core.utils import get_now_iso
from core.database import get_db_cursor
import game_logic
from models.quest import MasterQuest, MasterReward, MasterUser
from services.quest.locks import JST, ROLE_CHILD, logger
from services.quest.master_sync_sql import (
    QUEST_UPSERT_SQL,
    REWARD_UPSERT_SQL,
    quest_upsert_params,
    reward_upsert_params,
)
from services.quest.quest_service import QuestService
from services.quest.shop_service import ShopService
from services.quest.approval_service import ApprovalService
from services.quest.user_service import UserService


def load_master_module():
    """互換シム経由で `quest_data` モジュールの現在値を取得し、reload して返す。

    quest_data は互換シム(services/quest_service.py)側でimportされ、テストが
    `from services import quest_service as qs; monkeypatch.setattr(qs, "quest_data", fake)`
    という形で差し替える(Issue #529等)。ここで `import quest_data` を直接
    モジュールグローバルとして束縛すると、シム側への差し替えが同期処理の
    実行結果に反映されなくなるため、呼び出しのたびにシム経由で現在値を読む。

    Issue #664: 手動CLI(`sync_strict.py`)の安全ガードも「これから同期される
    マスタ」を数える必要があるため、読み込み口をここに切り出して共有する。
    """
    from services import quest_service as _quest_service_shim

    quest_data = _quest_service_shim.quest_data
    if not quest_data:
        logger.error("Quest data module not available for sync.")
        raise ImportError("quest_data module missing")
    importlib.reload(quest_data)
    return quest_data


def _apply_legacy_quest_defaults(q: Dict[str, Any]) -> Dict[str, Any]:
    """strict(手動CLI)経路の後方互換: 旧 `sync_strict.py` と同じ既定値でキーを補う。

    Issue #664 以前の `sync_strict.py` は Pydantic を通さず生の dict から直接
    UPSERT していたため、`type`/`exp`/`gold`/`icon` 等が欠けていても既定値で
    通っていた(`exp_gain`/`gold_gain`/`icon_key` というレガシーな別名も許容)。
    統合にあたって検証は `MasterQuest` に一本化したが、**CLI の受理範囲は
    変えない**ため、検証の前にここで旧実装と同じ既定値を埋める。
    """
    data = dict(q)
    data.setdefault('type', 'daily')
    data.setdefault('target', 'all')
    data['exp'] = q.get('exp_gain', q.get('exp', 0))
    data['gold'] = q.get('gold_gain', q.get('gold', 0))
    data['icon'] = q.get('icon_key', q.get('icon', '📝'))
    data.setdefault('chance', 1.0)
    return data


def _apply_legacy_reward_defaults(r: Dict[str, Any]) -> Dict[str, Any]:
    """strict(手動CLI)経路の後方互換: 旧 `sync_strict.py` と同じ既定値でキーを補う。

    `desc` を「未指定なら空文字」にするのも旧実装の挙動で、`MasterReward` の
    既定値(None)とは異なる。ここで明示的に埋めて差を消す。
    """
    data = dict(r)
    data.setdefault('category', 'small')
    data['cost_gold'] = r.get('cost_gold', r.get('cost', 0))
    data['icon_key'] = r.get('icon_key', r.get('icon', '🎁'))
    data['target'] = r.get('target', 'all')
    data['desc'] = r.get('desc', '')
    return data


def _count_rows_to_delete(cur, table: str, id_column: str, master_ids: List[Any]) -> int:
    """quest_master/reward_master のうち、マスタに存在しないため削除対象になる行数を数える。"""
    if master_ids:
        placeholders = ','.join(['?'] * len(master_ids))
        # 値はパラメータ化済み(f-string で組み立てているのは "?" の個数だけ)。
        row = cur.execute(
            f"SELECT COUNT(*) as c FROM {table} WHERE {id_column} NOT IN ({placeholders})",  # nosec B608
            master_ids,
        ).fetchone()
    else:
        row = cur.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()  # nosec B608
    return row['c'] if row else 0


class GameSystem:
    def __init__(self):
        self.quest_service = QuestService()
        self.approval_service = ApprovalService()
        self.user_service = UserService()
        self.shop_service = ShopService()

    def sync_master_data(self, strict: bool = False, dry_run: bool = False) -> Dict[str, str]:
        """quest_data.py の内容で quest_users/quest_master/reward_master を同期する。

        Issue #664: 以前はこれと同じ「マスタ→DB同期」が手動CLIの `sync_strict.py` にも
        別実装で存在し、`DELETE ... NOT IN` と UPSERT が二重に書かれていた
        (列リストの食い違い事故が #100/#164/#165 と3度発生)。同期の実体をここへ
        統合し、CLI 固有の差分は下記2つの引数として表現する。`sync_strict.py` には
        引数解析と破壊的操作の安全ガード(空マスタの拒否・確認プロンプト)だけが残る。

        Args:
            strict: True で手動CLI(`sync_strict.py --yes`)と同じ方針になる。
                - マスタのクエストが空のとき `quest_master` を**全削除**する
                  (`strict=False` では #242 の安全弁として削除自体をスキップする)。
                - `quest_users` は同期しない。CLI の対象は一貫して
                  quest_master/reward_master の2テーブルだけで、ユーザー行は
                  `POST /api/quest/seed`(= `strict=False`)側の責務。
                - マスタ各エントリの欠損キーを旧 `sync_strict.py` と同じ既定値で
                  補ってから検証する(`_apply_legacy_quest_defaults` 等)。
            dry_run: True ならDBを一切変更せず、削除・更新される件数だけをログに出す
                (`get_db_cursor(commit=False)` のため SELECT しか実行しない)。
        """
        logger.info("🔄 Starting Master Data Sync...")
        try:
            quest_data = load_master_module()
            # strict(手動CLI)は quest_users を対象にしない。検証も走らせないことで、
            # USERS 側の不備で CLI が止まる、という旧実装に無かった挙動を持ち込まない。
            valid_users = [] if strict else [MasterUser(**u) for u in quest_data.USERS]
            valid_quests = []
            for q in quest_data.QUESTS:
                q_data = _apply_legacy_quest_defaults(q) if strict else q.copy()
                if 'start_time' not in q_data:
                    q_data['start_time'] = None
                if 'end_time' not in q_data:
                    q_data['end_time'] = None
                valid_quests.append(MasterQuest(**q_data))

            valid_rewards = [
                MasterReward(**(_apply_legacy_reward_defaults(r) if strict else r))
                for r in quest_data.REWARDS
            ]
        except Exception as e:
            logger.error(f"❌ Master Data Validation failed: {e}")
            raise HTTPException(status_code=500, detail=f"Master Data Error: {str(e)}")

        if dry_run:
            return self._report_dry_run(valid_quests, valid_rewards, strict=strict)

        with get_db_cursor(commit=True) as cur:
            # Issue #330: 以前ここにあった「SELECTを試して失敗したらALTER TABLE」式の
            # レガシー実行時マイグレーション(role/reset_period/descriptionカラムの追加)は
            # 完全退役した。スキーマは migrations/ 配下(0000ベースライン+0001以降)が
            # 唯一の定義元であり、unified_serverのlifespanとinit_db()の双方が起動時に
            # apply_pending_migrations() を適用するため、本メソッド到達時点で
            # これらのカラムは必ず存在する。
            # strict のとき valid_users は空リスト(上記参照)なので、このループは回らない。
            for u in valid_users:
                role_val = getattr(u, 'role', None)
                cur.execute("""
                    INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, avatar, role, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        name = excluded.name,
                        job_class = excluded.job_class,
                        role = COALESCE(excluded.role, quest_users.role)
                """, (u.user_id, u.name, u.job_class, u.level, u.exp, u.gold, u.avatar, role_val, get_now_iso()))

            active_q_ids = [q.id for q in valid_quests]
            if active_q_ids:
                ph = ','.join(['?'] * len(active_q_ids))
                # f-stringで組み立てているのは "?" プレースホルダの個数のみで、値自体は
                # 第2引数でパラメータ化して渡している(bandit B608はこの安全なパターンを
                # 文字列連結によるSQLインジェクションと区別できず誤検知する)。
                cur.execute(f"DELETE FROM quest_master WHERE quest_id NOT IN ({ph})", active_q_ids)  # nosec B608
                logger.info(f"Deleted obsolete quests: {cur.rowcount} rows")
            elif strict:
                # Issue #664: 手動CLI(sync_strict.py)は「quest_data.py を唯一の正として
                # 完全同期する」ことが目的で、マスタが空なら全削除が正しい結果になる。
                # 事故を防ぐ役割は削除のスキップではなく、CLI 側の安全ガード
                # (--allow-empty-master が無ければ拒否 + 確認プロンプト)が担う。
                cur.execute("DELETE FROM quest_master")
                logger.info("Deleted ALL quests (Master is empty)")
            else:
                # #242: quest_data.QUESTSが空(コーディングミス等)になった瞬間、
                # 以前は無条件でDELETE FROM quest_masterを実行し全クエストマスタが
                # 消えていた。reward_master側の「参照が残っている行は削除をスキップする」
                # 安全弁と同様、意図しない全消去を防ぐため削除自体をスキップする。
                # (このAPI経路には CLI のような確認プロンプトが無いため。)
                logger.warning(
                    "⚠️ quest_data.QUESTSが空のため、quest_masterへの全削除操作を"
                    "スキップしました(意図しない全消去を防ぐための安全弁)。"
                )

            # Issue #664: UPSERT の SQL と値の並びは services/quest/master_sync_sql.py に
            # 一本化した(sync_strict.py と列リストが食い違う事故が #100/#164/#165 と
            # 3度起きたため)。同期の方針(空マスタ時の安全弁等)だけがここに残る。
            for q in valid_quests:
                cur.execute(QUEST_UPSERT_SQL, quest_upsert_params(
                    quest_id=q.id,
                    title=q.title,
                    description=q.desc,
                    quest_type=q.type,
                    target_user=q.target,
                    exp_gain=q.exp,
                    gold_gain=q.gold,
                    icon_key=q.icon,
                    day_of_week=q.days,
                    start_date=q.start_date,
                    end_date=q.end_date,
                    occurrence_chance=q.chance,
                    start_time=q.start_time,
                    end_time=q.end_time,
                    pre_requisite_quest_id=q.pre_requisite_quest_id,
                    reset_period=q.reset_period,
                ))

            active_r_ids = [r.id for r in valid_rewards]
            if active_r_ids:
                ph = ','.join(['?'] * len(active_r_ids))
                # 上のDELETEと同じ理由でbanditのB608誤検知を抑制する(値はパラメータ化済み)。
                stale_rewards = cur.execute(
                    f"SELECT reward_id FROM reward_master WHERE reward_id NOT IN ({ph})", active_r_ids  # nosec B608
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
                # 同上、値はパラメータ化済みでbanditのB608誤検知を抑制する。
                referenced_reward_ids = {
                    row['reward_id'] for row in cur.execute(
                        f"SELECT DISTINCT reward_id FROM user_inventory WHERE reward_id IN ({ph_stale})",  # nosec B608
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
                cur.execute(REWARD_UPSERT_SQL, reward_upsert_params(
                    reward_id=r.id,
                    title=r.title,
                    category=r.category,
                    cost_gold=r.cost_gold,
                    icon_key=r.icon_key,
                    description=r.desc,
                    target=r.target,
                ))

        logger.info("✅ Master data sync completed.")
        return {"status": "synced", "message": "Master data updated."}

    def _report_dry_run(
        self, valid_quests: List[Any], valid_rewards: List[Any], strict: bool
    ) -> Dict[str, str]:
        """DBを変更せず、削除・更新される件数だけをログに出す(Issue #664)。

        `commit=False` で開くため、ブロックを抜ける際にコミットされず、
        実行するのも `_count_rows_to_delete` のSELECTだけである。
        """
        master_quest_ids = [q.id for q in valid_quests]
        master_reward_ids = [r.id for r in valid_rewards]
        with get_db_cursor(commit=False) as cur:
            logger.info("--- Syncing Quests (Strict Mode) ---" if strict else "--- Syncing Quests ---")
            if master_quest_ids or strict:
                stale_quests = _count_rows_to_delete(cur, "quest_master", "quest_id", master_quest_ids)
            else:
                # strict でないマスタ空は削除自体をスキップする(#242 の安全弁)ため 0 件。
                stale_quests = 0
            logger.info(f"[dry-run] Would delete obsolete quests: {stale_quests} rows")
            logger.info(f"[dry-run] Would upsert {len(valid_quests)} quests.")

            logger.info("--- Syncing Rewards ---")
            stale_rewards = _count_rows_to_delete(cur, "reward_master", "reward_id", master_reward_ids)
            logger.info(f"[dry-run] Would delete obsolete rewards: {stale_rewards} rows")
            logger.info(f"[dry-run] Would upsert {len(valid_rewards)} rewards.")

        logger.info("✅ Dry-run completed. No changes were made.")
        return {"status": "dry-run", "message": "No changes were made."}

    def get_all_view_data(self, viewer_user_id: Optional[str] = None) -> Dict[str, Any]:
        # sync_master_dataと同じ理由(互換シム経由でのquest_data差し替えを尊重するため)、
        # モジュールグローバルとしてimportせずシム経由で参照する。
        from services import quest_service as _quest_service_shim

        with get_db_cursor() as cur:
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
            if _quest_service_shim.quest_data:
                canonical_order = {u['user_id']: i for i, u in enumerate(_quest_service_shim.quest_data.USERS)}
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

            # #662: 以前は filtered_quests × recent_completed の二重ループで、
            # クエスト数 Q・履歴数 H に対して O(Q×H) だった(履歴は直近30日分すべて)。
            # quest_id で1度だけ索引を作れば O(Q+H) になる。recent_completed は
            # completed_at の降順で取得済みで、defaultdict への追加もその順序を保つため、
            # 「ユーザーごとの最新履歴を先に見る」という下の判定はそのまま成り立つ。
            completed_by_quest: Dict[Any, List[Dict[str, Any]]] = collections.defaultdict(list)
            for c in recent_completed:
                completed_by_quest[c['quest_id']].append(c)

            valid_completed = []

            for q in filtered_quests:
                q_id = q['quest_id']
                reset_period = q.get('reset_period') or 'daily'
                is_infinite = (q.get('quest_type') == 'infinite')
                history_for_quest = completed_by_quest.get(q_id, ())

                if is_infinite:
                    # 無限クエストは条件を満たす全履歴を追加
                    for c in history_for_quest:
                        if self.quest_service.is_within_reset_period(c['completed_at'], reset_period):
                            valid_completed.append(c)
                else:
                    # 通常クエストの場合、ユーザーごとに最新の履歴を評価する
                    users_processed = set()
                    for c in history_for_quest:
                        uid = c['user_id']
                        if uid not in users_processed:
                            if self.quest_service.is_within_reset_period(c['completed_at'], reset_period):
                                valid_completed.append(c)
                            # 期間外であっても最新履歴を処理済みにし、同ユーザーの過去履歴検索を終了する
                            users_processed.add(uid)

            # Q-M3/F-M5 (#371): 以前ここにあった target_user が 'role_' プレフィックスの
            # クエストの共有表示判定(is_shared_completed_by/is_shared_pending_by)は、
            # completion API側(_process_complete_quest_locked)が 'all'/本人/'siblings'
            # 以外を無条件403で拒否するため、'role_*' ターゲットは一覧には出ても
            # 誰も完了できないという不整合な潜在バグだった(quest_data.pyに実際の
            # 'role_*' ターゲットが存在しないため顕在化していなかった)。オーナー判断
            # (role_* ターゲットは今後も使わない)により、この表示側の分岐を削除した。
            # 複数人ターゲットの共有表示が必要な唯一のケースである兄妹連携クエスト
            # (target_user='siblings')は、linked_history_idによる連結で別途処理される。

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
            FROM quest_history WHERE status='approved' AND quest_id != 0 ORDER BY id DESC LIMIT 20
        """).fetchall()
        r_logs = cur.execute("""
            SELECT id, user_id, reward_title as title, 'reward' as type, redeemed_at as ts
            FROM reward_history ORDER BY id DESC LIMIT 20
        """).fetchall()
        all_logs = sorted(q_logs + r_logs, key=lambda x: x['ts'], reverse=True)[:20]
        user_map = {row['user_id']: row['name'] for row in cur.execute("SELECT user_id, name FROM quest_users")}
        formatted = []
        for log in all_logs:
            name = user_map.get(log['user_id'], '誰か')
            ts_str = log['ts']
            date_str = ts_str.split('T')[0] if 'T' in ts_str else ts_str.split(' ')[0]
            text = f"{name}は {log['title']} を{'クリアした！' if log['type']=='quest' else '手に入れた！'}"
            formatted.append({"id": f"{log['type']}_{log['id']}", "text": text, "dateStr": date_str, "timestamp": ts_str})
        return formatted


game_system = GameSystem()
quest_service = game_system.quest_service
approval_service = game_system.approval_service
shop_service = game_system.shop_service
user_service = game_system.user_service
