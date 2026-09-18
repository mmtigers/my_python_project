"""services/quest_service.py から分割(Issue #550)。

並行制御(プロセス内ロック)とYouTubeクールダウン判定など、特定のサービスクラスに
属さないモジュールレベルのヘルパー・定数を集約する。QuestService/ShopService/
InventoryService/GameSystemはいずれもここから必要なロック・定数をimportする。
"""
import datetime
import math
from contextlib import ExitStack
from typing import Optional, Tuple

import config
from core.logger import setup_logging
from core.utils import RefCountedLockRegistry

# ロガー設定。分割後もログの出所が分かるよう、旧ファイルと同じロガー名を維持する
# (services/quest/ 配下の各モジュールはこのloggerをimportして使い回し、
# モジュールごとにsetup_loggingを呼び直してハンドラを二重登録しない)。
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
    # f-stringで組み立てているのは "?" プレースホルダの個数のみで、値自体は
    # 第2引数でパラメータ化して渡している(bandit B608はこの安全なパターンを
    # 文字列連結によるSQLインジェクションと区別できず誤検知する)。
    row = cur.execute(f"""
        SELECT used_at FROM user_inventory
        WHERE user_id = ? AND status = 'consumed' AND reward_id IN ({placeholders})
        ORDER BY used_at DESC LIMIT 1
    """, (user_id, *config.YOUTUBE_REWARD_IDS)).fetchone()  # nosec B608

    if not row or not row['used_at']:
        return 0

    elapsed = _seconds_since_iso_timestamp(row['used_at'])
    if elapsed is None:
        return 0

    remaining = YOUTUBE_REWARD_COOLDOWN_SECONDS - elapsed
    return max(0, math.ceil(remaining))


def _is_youtube_cooldown_enforced() -> bool:
    """
    YouTube系ごほうび券のクールダウンを実際に強制する日(config.YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM、
    JST基準)を迎えているかどうかを返す。いきなり制限がかかると子どもが困惑するため、
    この日より前は使用を拒否せず、family-quest側に予告バナーを表示するだけに留める。
    """
    return datetime.datetime.now(JST).date() >= config.YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM


# ==========================================
# Completion Lock (Race Condition Guard)
# ==========================================
# process_complete_quest は「直近履歴を読む→報酬を書く」という手順のため、
# 同一(user_id, quest_id)への同時リクエスト（クライアントのリトライ・二重タップ等）が
# 別スレッドでほぼ同時に到達すると、どちらも「直近の完了履歴なし」を読んでしまい、
# 経験値・ゴールド・ボスダメージが二重に加算されるレースコンディションが発生しうる。
# そのため、同一キーへの処理はプロセス内で直列化する。
# #435: 参照カウント付きレジストリを使い、使用を終えたキーは自動的に
# 辞書から削除する(ユーザーID×クエストIDの組み合わせが増え続けても無制限に
# 肥大化しない)。
_completion_locks = RefCountedLockRegistry()


def _get_completion_lock(key: Tuple[str, int]):
    return _completion_locks.acquire(key)


# ==========================================
# User Balance Lock (Race Condition Guard for approve/cancel)
# ==========================================
# process_approve_quest / process_cancel_quest は「quest_usersをSELECT →
# Pythonでgold/exp/levelを計算 → UPDATE」というread-modify-writeのため、
# 同一ユーザーへの承認×承認・承認×取消が並行実行されると(例: 親が承認一覧を
# 連続タップするhandleApproveAll)、一方の更新が消失するレースが起こりうる。
# quest_users(gold/exp/level)を書き換える処理は、対象ユーザー単位でプロセス内
# 直列化する。
# #435: 参照カウント付きレジストリを使い、使用を終えたユーザーIDは自動的に
# 辞書から削除する。
_user_balance_locks = RefCountedLockRegistry()


def _get_user_balance_lock(user_id: str):
    return _user_balance_locks.acquire(user_id)


def _acquire_user_balance_locks(user_ids):
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
# #435: 参照カウント付きレジストリを使い、使用を終えたキーは自動的に
# 辞書から削除する。
_purchase_locks = RefCountedLockRegistry()


def _get_purchase_lock(key: Tuple[str, int]):
    return _purchase_locks.acquire(key)


# ==========================================
# Item Use Lock (Race Condition Guard for YouTube Cooldown)
# ==========================================
# use_item は「YouTube系ごほうび券の直近used_atを読む→クールダウン判定→consumedへ
# 更新」というTOCTOUを持つ。同一ユーザーが異なるYouTube系ごほうび券(reward_id違い、
# 例: 10:00券と30:00券)をほぼ同時に使用しようとすると、両リクエストがクールダウン
# なし(0秒)を読んでしまい、15分ロックをすり抜けて連続使用が成立し得る。
# ユーザー単位でuse_item全体をプロセス内で直列化し、このレースを防ぐ。
# #435: 他の3レジストリと同様、参照カウント付きレジストリを使い、使用を終えた
# キーは自動的に辞書から削除する。
_item_use_locks = RefCountedLockRegistry()


def _get_item_use_lock(user_id: str):
    return _item_use_locks.acquire(user_id)
