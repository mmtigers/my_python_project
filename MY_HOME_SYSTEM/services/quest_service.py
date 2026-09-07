"""services/quest_service.py

Issue #550: 1572行・5クラス同居だった本ファイルは、クラス単位で
services/quest/ パッケージ(locks.py, user_service.py, quest_service.py,
shop_service.py, inventory_service.py, game_system.py)へ分割された。

本ファイルは既存の import パス(`from services.quest_service import ...` /
`from services import quest_service`)を壊さないための互換再エクスポート層
としてのみ存在する。ルーター・テストの移行は段階的に行うため、新規コードも
当面はこのパスからimportして構わないが、内部実装を触る場合は
services/quest/ 配下の対応するファイルを編集すること。
"""
import importlib

import config
import game_logic
from core import sound_manager
from services import notification_service, switchbot_service

# 上記4つ(+importlib)は分割後の services/quest/ 配下では直接importしているが、
# 既存テストの一部は `from services import quest_service as qs_module` の形で
# このシムモジュールを取得し、`monkeypatch.setattr(qs_module.notification_service,
# "send_push", ...)` のように"シム経由で辿った先の共有オブジェクト"を差し替える
# (notification_service/sound_manager/config/game_logic自体は元々プロセス全体で
# 共有される単一のモジュール/クラスオブジェクトなので、どのモジュール経由で
# 参照しても差し替えは同じ実体に効く)。互換性のためこのシムでも同名でimportする。
from services.quest.locks import (
    INFINITE_QUEST_COOLDOWN_SECONDS,
    JST,
    ROLE_ADULT,
    ROLE_CHILD,
    SPAM_CHECK_INTERVAL_SECONDS,
    YOUTUBE_REWARD_COOLDOWN_SECONDS,
    _acquire_user_balance_locks,
    _completion_locks,
    _get_completion_lock,
    _get_item_use_lock,
    _get_purchase_lock,
    _get_user_balance_lock,
    _get_youtube_cooldown_remaining_seconds,
    _is_youtube_cooldown_enforced,
    _item_use_locks,
    _purchase_locks,
    _seconds_since_iso_timestamp,
    _user_balance_locks,
    logger,
)
from services.quest.user_service import ImageTooLargeError, InvalidImageError, UserService
from services.quest.quest_service import QuestService
from services.quest.shop_service import ShopService
from services.quest.inventory_service import InventoryService, inventory_service
from services.quest.game_system import (
    GameSystem,
    game_system,
    quest_service,
    shop_service,
    user_service,
)

# 本ファイルはimportパス互換のための再エクスポート層であり、ここで束縛する名前は
# すべて外部(ルーター・テスト)からの `from services.quest_service import ...` /
# `from services import quest_service; quest_service.xxx` を通じた利用が前提のため、
# ruffのF401(unused-import)を抑制する。
__all__ = [
    "importlib",
    "config",
    "game_logic",
    "sound_manager",
    "notification_service",
    "switchbot_service",
    "INFINITE_QUEST_COOLDOWN_SECONDS",
    "JST",
    "ROLE_ADULT",
    "ROLE_CHILD",
    "SPAM_CHECK_INTERVAL_SECONDS",
    "YOUTUBE_REWARD_COOLDOWN_SECONDS",
    "_acquire_user_balance_locks",
    "_completion_locks",
    "_get_completion_lock",
    "_get_item_use_lock",
    "_get_purchase_lock",
    "_get_user_balance_lock",
    "_get_youtube_cooldown_remaining_seconds",
    "_is_youtube_cooldown_enforced",
    "_item_use_locks",
    "_purchase_locks",
    "_seconds_since_iso_timestamp",
    "_user_balance_locks",
    "logger",
    "UserService",
    "InvalidImageError",
    "ImageTooLargeError",
    "QuestService",
    "ShopService",
    "InventoryService",
    "inventory_service",
    "GameSystem",
    "game_system",
    "quest_service",
    "shop_service",
    "user_service",
    "quest_data",
]

# Issue #487: quest_data は sys.path に PROJECT_ROOT が入っているため通常の
# importで解決する。以前あった `from .. import quest_data` のフォールバックは、
# services がトップレベルパッケージ(MY_HOME_SYSTEM/services/__init__.py の親は
# パッケージではない)であるため構造上決して成功せず、到達不能コードだった上、
# mypyがこの1行で解析を停止しコードベース全体を検証できなくなっていた。
#
# Issue #550: GameSystem.sync_master_data/get_all_view_data は分割後
# services/quest/game_system.py に移ったが、`quest_data` の実体はこの互換シム側に
# 残す。テストが `monkeypatch.setattr(services.quest_service, "quest_data", fake)`
# の形でこのモジュールの属性を差し替えるため、game_system.py 側は
# `import quest_data` でモジュールグローバルとして束縛せず、都度このシムモジュールを
# 経由して現在値を読みに行く(services/quest/game_system.py 内のコメント参照)。
try:
    import quest_data
except ImportError:
    logger.warning("quest_data module not found.")
    quest_data = None
