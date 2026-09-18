# MY_HOME_SYSTEM/tests/test_quest_package_direct_imports.py
"""Issue #664: services/quest/ 配下のモジュールを「互換シム経由ではなく直接」import して検証する。

Issue #550 で services/quest_service.py(1572行・5クラス同居)は services/quest/ パッケージへ
分割されたが、`services/quest/locks.py`・`inventory_service.py`・`routine_data.py` を
**直接 import するテストは0件**で、すべて互換シム `services/quest_service.py` 経由だった。
そのためシムを縮小・削除しようとするとテストの import が壊れ、移行を進めにくい状態にある。

ここでは分割後の実体を直接 import して、シムを介さずに使えることを固定する。シムが
再エクスポートしているオブジェクトが実体と同一であることも合わせて確認し、将来
シム側を薄くする(あるいは消す)ときの安全網にする。
"""
import datetime
import os
import sys
import threading

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import routine_data
from services.quest import inventory_service as inventory_module
from services.quest import locks as locks_module


class TestLocksModuleDirectly:
    def test_seconds_since_iso_timestamp_handles_none_and_values(self):
        assert locks_module._seconds_since_iso_timestamp(None) is None
        assert locks_module._seconds_since_iso_timestamp("") is None
        assert locks_module._seconds_since_iso_timestamp("壊れた値") is None

        one_minute_ago = (datetime.datetime.now(locks_module.JST) - datetime.timedelta(minutes=1)).isoformat()
        elapsed = locks_module._seconds_since_iso_timestamp(one_minute_ago)
        assert elapsed is not None and 55 <= elapsed <= 120

    def test_completion_lock_is_per_key_and_reentrant_across_calls(self):
        key = ("user_direct", 1)
        other = ("user_direct", 2)

        with locks_module._get_completion_lock(key):
            # 別キーは待たされない(同じキーの多重取得はデッドロックになるので試さない)
            acquired = threading.Event()

            def take_other():
                with locks_module._get_completion_lock(other):
                    acquired.set()

            t = threading.Thread(target=take_other)
            t.start()
            t.join(timeout=5)
            assert acquired.is_set()

    def test_balance_locks_are_acquired_in_a_stable_order(self):
        """複数ユーザー分のロックは、呼び出し順に関わらず同じ順序で取得する(デッドロック防止)。"""
        with locks_module._acquire_user_balance_locks(["son", "dad"]):
            pass
        with locks_module._acquire_user_balance_locks(["dad", "son"]):
            pass

    def test_youtube_cooldown_enforcement_reads_config(self, monkeypatch):
        monkeypatch.setattr(config, "YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM", datetime.date(2000, 1, 1))
        assert locks_module._is_youtube_cooldown_enforced() is True
        monkeypatch.setattr(config, "YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM", datetime.date(2999, 1, 1))
        assert locks_module._is_youtube_cooldown_enforced() is False


class TestInventoryServiceDirectly:
    def test_can_be_instantiated_without_the_shim(self):
        service = inventory_module.InventoryService()
        assert hasattr(service, "use_item")
        assert hasattr(service, "get_user_inventory")

    def test_shim_reexports_the_same_class(self):
        from services import quest_service as shim

        assert shim.InventoryService is inventory_module.InventoryService


class TestRoutineDataDirectly:
    def test_flow_constants_are_consistent(self):
        assert routine_data.WEEKEND_DAYS.issubset(set(routine_data.ALL_DAYS))
        assert routine_data.FULL_BONUS_GOLD > 0
        assert routine_data.FULL_BONUS_EXP > 0

    def test_adult_flows_are_built_for_every_day(self):
        for flow in routine_data.DAD_ROUTINE_FLOWS.values():
            assert flow, "ルーティンのステップが空になっている"


class TestShimAndPackageAreTheSameObjects:
    """シムが再エクスポートしているシングルトン・クラスが実体と同一であること。"""

    @pytest.mark.parametrize(
        "name",
        ["game_system", "quest_service", "shop_service", "user_service", "inventory_service"],
    )
    def test_singletons_are_identical(self, name):
        from services import quest_service as shim
        from services.quest import game_system as game_system_module

        shim_obj = getattr(shim, name)
        if name == "inventory_service":
            # inventory_service だけは game_system.py ではなく inventory_service.py 側に住む
            assert isinstance(shim_obj, inventory_module.InventoryService)
        else:
            assert shim_obj is getattr(game_system_module, name)
