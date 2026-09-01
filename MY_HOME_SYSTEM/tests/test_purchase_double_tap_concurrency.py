# MY_HOME_SYSTEM/tests/test_purchase_double_tap_concurrency.py
"""
Issue #101: 購入確認モーダルの「はい」連打で二重購入が成立する不具合の回帰防止テスト。

process_purchase_reward は残高チェックと減算を単一のアトミックなUPDATEで行うため
read-then-writeのレースコンディション自体は起きなかったが、「同一操作の連打を拒否する」
スパムチェック(process_complete_quest 等が既に持つもの)が無かったため、1回目の
レスポンス前に届いた2回目のリクエストもサーバー側では独立した正当な購入として処理され、
残高が足りる限り2回とも成功してしまっていた(ゴールド二重消費+アイテム二重取得)。

test_quest_approve_cancel_concurrency.py 等と同様、実際のスレッドを使い、
ファイルベースのSQLite(isolated_db)に対して本物のShopServiceメソッドを
並行呼び出しすることで検証する。
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services.quest_service import ShopService

N_TAPS = 8
REWARD_COST = 100
INITIAL_GOLD = 10000


def _seed_user_and_reward(cur):
    cur.execute(
        "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
        "('son', 'Son', 'Novice', 1, 0, ?, 'role_child')",
        (INITIAL_GOLD,),
    )
    cur.execute(
        "INSERT INTO reward_master (reward_id, title, cost_gold, target) VALUES "
        "(500, 'Popular Reward', ?, 'all')",
        (REWARD_COST,),
    )


class TestPurchaseDoubleTapConcurrency:
    def test_concurrent_double_taps_result_in_single_purchase(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            _seed_user_and_reward(cur)

        shop_service = ShopService()

        def _tap(_):
            try:
                return shop_service.process_purchase_reward("son", 500)
            except Exception as exc:  # スパムチェックでブロックされた側は例外を投げる
                return exc

        with ThreadPoolExecutor(max_workers=N_TAPS) as pool:
            results = list(pool.map(_tap, range(N_TAPS)))

        successes = [r for r in results if isinstance(r, dict)]
        # 連打のうち購入として成立するのは1回だけであること
        assert len(successes) == 1
        assert successes[0]["status"] == "purchased"

        with common.get_db_cursor() as cur:
            son = cur.execute("SELECT gold FROM quest_users WHERE user_id = 'son'").fetchone()
            history_count = cur.execute(
                "SELECT COUNT(*) c FROM reward_history WHERE user_id = 'son' AND reward_id = 500"
            ).fetchone()["c"]
            inventory_count = cur.execute(
                "SELECT COUNT(*) c FROM user_inventory WHERE user_id = 'son' AND reward_id = 500"
            ).fetchone()["c"]

        # ゴールドは1回分しか減っておらず、履歴・所持アイテムも1件だけであること
        assert son["gold"] == INITIAL_GOLD - REWARD_COST
        assert history_count == 1
        assert inventory_count == 1
