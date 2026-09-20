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
from fastapi import HTTPException

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


def _require_adult(cur, admin_id: str, detail: str = "権限がありません") -> None:
    """admin_id が親(ROLE_ADULT)であることを検証し、そうでなければ403を送出する。

    Issue #739 (AUDIT-009): 同じ「破壊的な管理操作」でありながら
    `POST /api/quest/admin/reset_user` だけが role チェックを持ち、
    `POST /api/quest/sync_master` と `POST /api/quest/seed`
    (いずれも quest_master/reward_master の DELETE を伴う)は
    認可チェックを一切持たないという非対称があった。判定は
    UserService._reset_user_data_locked にインラインで書かれていて
    共有できなかったため、ここへ切り出して両経路から呼ぶ。

    LAN内を信頼境界とする方針(#321/#614)は変えない。ここで担保するのは
    「親(role_adult)/子(role_child)」というアプリ固有の権限モデルの一貫性であり、
    リクエストボディの user_id を信頼している点は従来どおりである。

    Issue #788: `quest_users` が空のときだけは認可を通す。この表は
    `migrations/0000_baseline_schema.sql` で**空のテーブルとして作られる**だけで、
    行を作るのは seed/sync 自身であるため、まっさらなDBでは「seedするには親が必要、
    親を作るにはseedが必要」という循環になり、どの admin_id でも403になっていた
    (開発環境の初回起動・SDカード故障からの再構築で詰まる)。
    空のDBには守るべきデータも壊すものも無いので、フェイルクローズの趣旨とも矛盾しない。
    """
    # 先に「誰も登録されていない初期状態か」を見る(存在判定なので LIMIT 1 で十分)。
    if cur.execute("SELECT 1 FROM quest_users LIMIT 1").fetchone() is None:
        logger.warning(
            "⚠️ quest_users が空のため認可チェックをスキップします"
            f"(初回ブートストラップ。admin_id={admin_id})"
        )
        return
    row = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (admin_id,)).fetchone()
    if not row or row['role'] != ROLE_ADULT:
        raise HTTPException(status_code=403, detail=detail)

# _process_complete_quest_locked のスパムチェック間隔(秒)。infiniteクエストのみ
# フロントエンド(family-quest QuestList.tsx)のクールダウン表示(60秒)と揃える(B2)。
SPAM_CHECK_INTERVAL_SECONDS = 10
INFINITE_QUEST_COOLDOWN_SECONDS = 60

# YouTube系ごほうび券(config.YOUTUBE_REWARD_IDS)を「見終わってから」次の1枚を
# 使用できるまでに空ける休憩時間(秒)。連続視聴による目の負担を防ぐ。
#
# 注意: これは券の視聴時間を**含まない**「休憩そのものの長さ」である。
# 以前はこの15分がそのままクールダウン全体の長さで、かつ起点が券を使った瞬間
# (used_at)だったため、視聴時間が15分より長い券では見終わる前にクールダウンが
# 明けてしまい、30分券・60分券では休憩が実質ゼロになっていた。現在は
# 「券の視聴分数(config.YOUTUBE_REWARD_DURATION_MINUTES) + この秒数」を
# used_at からの待ち時間とすることで、どの券でも休憩が成立する。
YOUTUBE_REWARD_BREAK_SECONDS = 15 * 60


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


def get_youtube_reward_duration_minutes(reward_id: int) -> int:
    """
    YouTube系ごほうび券1枚の視聴分数(config.YOUTUBE_REWARD_DURATION_MINUTES)を返す。

    対応表に無いreward_idは0分として扱う。これは「視聴時間が分からない券」を
    安全側(=クールダウンは休憩ぶんだけ・日次上限には加算しない)に倒すためで、
    券を新設したのに対応表への追記を忘れても使用自体は壊れないようにしている。
    """
    return config.YOUTUBE_REWARD_DURATION_MINUTES.get(reward_id, 0)


def _get_youtube_cooldown_remaining_seconds(cur, user_id: str) -> int:
    """
    直近でYouTube系ごほうび券(config.YOUTUBE_REWARD_IDS)を使用してから、
    次の1枚を使用できるようになるまでの残り秒数を返す。クールダウン対象IDが
    未設定、または対象IDを一度も使用していない場合は0を返す。

    待ち時間は「直近に使った券の視聴分数 + YOUTUBE_REWARD_BREAK_SECONDS」で、
    起点は used_at(使い始めた時刻)である。券の長さを足しているのは、
    used_at 起点の固定15分だと30分券・60分券では見終わる前にクールダウンが
    明けてしまい、休憩が成立しなかったため。
    """
    if not config.YOUTUBE_REWARD_IDS:
        return 0

    placeholders = ",".join("?" for _ in config.YOUTUBE_REWARD_IDS)
    # f-stringで組み立てているのは "?" プレースホルダの個数のみで、値自体は
    # 第2引数でパラメータ化して渡している(bandit B608はこの安全なパターンを
    # 文字列連結によるSQLインジェクションと区別できず誤検知する)。
    row = cur.execute(f"""
        SELECT reward_id, used_at FROM user_inventory
        WHERE user_id = ? AND status = 'consumed' AND reward_id IN ({placeholders})
        ORDER BY used_at DESC LIMIT 1
    """, (user_id, *config.YOUTUBE_REWARD_IDS)).fetchone()  # nosec B608

    if not row or not row['used_at']:
        return 0

    elapsed = _seconds_since_iso_timestamp(row['used_at'])
    if elapsed is None:
        return 0

    watch_seconds = get_youtube_reward_duration_minutes(row['reward_id']) * 60
    remaining = (watch_seconds + YOUTUBE_REWARD_BREAK_SECONDS) - elapsed
    return max(0, math.ceil(remaining))


def get_youtube_daily_limit_minutes(today: datetime.date | None = None) -> int | None:
    """
    その日に使えるYouTube系ごほうび券の合計分数の上限を返す。上限なしの設定
    (0以下)の場合は None を返す。

    平日(月〜金)と休日(土日)で別々の上限を持つ。祝日は「平日」として扱う:
    祝日判定には外部の暦データ(jpholiday等)が必要で、個人用システムに依存を
    増やす割に合わないと判断した。祝日に緩めたい日は親が都度判断する運用でよい。
    """
    if today is None:
        today = datetime.datetime.now(JST).date()
    # weekday(): 月=0 ... 土=5, 日=6
    is_holiday = today.weekday() >= 5
    limit = (
        config.YOUTUBE_DAILY_LIMIT_MINUTES_HOLIDAY
        if is_holiday
        else config.YOUTUBE_DAILY_LIMIT_MINUTES_WEEKDAY
    )
    return limit if limit > 0 else None


def _get_today_jst_prefix() -> str:
    """JSTの今日の日付を "YYYY-MM-DD" で返す(ISO文字列の先頭一致に使う)。

    日付の切り出しにSQLiteの date(...) を使わないのは意図的である:
    used_at / completed_at は core.utils.get_now_iso() が保存するJSTオフセット付き
    ISO文字列("2026-09-20T08:00:00+09:00")で、SQLiteの日付関数はこれをUTCへ変換して
    しまうため、JSTの朝9時より前の記録が「前日」に数えられてしまう。
    文字列の先頭一致(JSTの日付そのもの)で判定する。
    """
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")


def _get_youtube_usages_today(cur, user_id: str) -> list:
    """
    JSTの今日のうちに使用済みのYouTube系ごほうび券を、(used_at, 視聴分数)の
    リストで古い順に返す。
    """
    if not config.YOUTUBE_REWARD_IDS:
        return []

    placeholders = ",".join("?" for _ in config.YOUTUBE_REWARD_IDS)
    # プレースホルダ個数のみf-stringで組み立て、値はパラメータ化している(B608誤検知)。
    rows = cur.execute(f"""
        SELECT used_at, reward_id FROM user_inventory
        WHERE user_id = ? AND status = 'consumed' AND reward_id IN ({placeholders})
          AND used_at LIKE ?
        ORDER BY used_at
    """, (user_id, *config.YOUTUBE_REWARD_IDS, f"{_get_today_jst_prefix()}%")).fetchall()  # nosec B608

    return [(row['used_at'], get_youtube_reward_duration_minutes(row['reward_id'])) for row in rows]


def get_youtube_used_minutes_today(cur, user_id: str) -> int:
    """JSTの今日のうちに使用済みのYouTube系ごほうび券の、視聴分数の合計を返す。"""
    return sum(minutes for _used_at, minutes in _get_youtube_usages_today(cur, user_id))


def _get_extension_quest_completions_today(cur, user_id: str) -> list:
    """
    JSTの今日のうちに完了し、かつ**親に承認された**延長対象クエスト
    (config.YOUTUBE_EXTENSION_QUEST_IDS = プリント等)の completed_at を古い順に返す。

    `status = 'approved'` に限定しているのは、やっていないプリントを「やった」と
    報告するだけで視聴時間を延ばせてしまわないようにするため(承認前の 'pending' は
    数えない)。並べ替えに使うのは承認時刻ではなく completed_at(子どもが実際に
    やった時刻)で、「上限に達した後にやったか」はこちらで判定するのが自然なため。
    """
    if not config.YOUTUBE_EXTENSION_QUEST_IDS:
        return []

    placeholders = ",".join("?" for _ in config.YOUTUBE_EXTENSION_QUEST_IDS)
    # プレースホルダ個数のみf-stringで組み立て、値はパラメータ化している(B608誤検知)。
    rows = cur.execute(f"""
        SELECT completed_at FROM quest_history
        WHERE user_id = ? AND status = 'approved' AND quest_id IN ({placeholders})
          AND completed_at LIKE ?
        ORDER BY completed_at
    """, (user_id, *config.YOUTUBE_EXTENSION_QUEST_IDS, f"{_get_today_jst_prefix()}%")).fetchall()  # nosec B608

    return [row['completed_at'] for row in rows]


def get_youtube_daily_limit_with_extensions(cur, user_id: str, base_limit_minutes: int) -> tuple[int, int]:
    """
    その日の実効上限(分)と、プリントによって延長された回数を返す。

    「上限に達した**後に**やったプリントだけが延長になる」という規則を実装する。
    朝の日課としてやったプリントで上限が最初から伸びているのでは「もっと見たいから
    もう1枚やる」という交換にならないため、今日の出来事(券の使用とプリントの完了)を
    時系列に並べ、プリントの時点で既に上限を使い切っていた場合だけ延長を与える。

    同じ時刻に券の使用とプリントが並んだ場合は券の使用を先に処理する(その使用で
    上限に達したなら、同時刻のプリントは「達した後」として扱う)。

    延長の回数は config.YOUTUBE_EXTENSION_MAX_PER_DAY までに制限する。
    """
    minutes_per_quest = config.YOUTUBE_EXTENSION_MINUTES_PER_QUEST
    max_per_day = config.YOUTUBE_EXTENSION_MAX_PER_DAY
    if minutes_per_quest <= 0 or max_per_day <= 0 or not config.YOUTUBE_EXTENSION_QUEST_IDS:
        return base_limit_minutes, 0

    # (タイムスタンプ, 種別) で並べ替える。種別は 0=券の使用 / 1=プリント完了 で、
    # 同時刻なら使用を先に処理するための第2キーとして効かせる。
    events = [(used_at, 0, minutes) for used_at, minutes in _get_youtube_usages_today(cur, user_id)]
    events += [(completed_at, 1, 0) for completed_at in _get_extension_quest_completions_today(cur, user_id)]
    events.sort(key=lambda e: (e[0], e[1]))

    effective_limit = base_limit_minutes
    used_minutes = 0
    granted = 0
    for _timestamp, kind, minutes in events:
        if kind == 0:
            used_minutes += minutes
        elif used_minutes >= effective_limit and granted < max_per_day:
            effective_limit += minutes_per_quest
            granted += 1

    return effective_limit, granted


def can_extend_youtube_limit_now(used_minutes: int, effective_limit: int, granted: int) -> bool:
    """
    「今プリントを1枚やれば上限が延びる」状態かどうかを返す。

    family-quest側が「プリントを1枚やると+30分」と案内するかどうかの判定に使い、
    get_youtube_daily_limit_with_extensions のループ内の条件と同じ式を共有する。
    """
    if config.YOUTUBE_EXTENSION_MINUTES_PER_QUEST <= 0 or not config.YOUTUBE_EXTENSION_QUEST_IDS:
        return False
    return used_minutes >= effective_limit and granted < config.YOUTUBE_EXTENSION_MAX_PER_DAY


def _is_youtube_cooldown_enforced() -> bool:
    """
    YouTube系ごほうび券のクールダウンを実際に強制する日(config.YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM、
    JST基準)を迎えているかどうかを返す。いきなり制限がかかると子どもが困惑するため、
    この日より前は使用を拒否せず、family-quest側に予告バナーを表示するだけに留める。
    """
    return datetime.datetime.now(JST).date() >= config.YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM


def _is_youtube_daily_limit_enforced() -> bool:
    """
    YouTube系ごほうび券の1日の合計分数上限を実際に強制する日
    (config.YOUTUBE_DAILY_LIMIT_ENFORCE_FROM、JST基準)を迎えているかどうかを返す。

    クールダウン(_is_youtube_cooldown_enforced)とは別の施行日を持つ。両者は
    導入時期が異なり、既に施行済みのクールダウンの猶予期間を新しい上限の導入で
    巻き戻してしまわないよう、判定軸を分けている。
    """
    return datetime.datetime.now(JST).date() >= config.YOUTUBE_DAILY_LIMIT_ENFORCE_FROM


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
