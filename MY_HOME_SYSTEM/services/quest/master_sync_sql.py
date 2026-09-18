"""quest_master / reward_master への UPSERT を1箇所に集約するモジュール(Issue #664)。

`services/quest/game_system.py` の `GameSystem.sync_master_data()` と、手動実行の
CLI である `sync_strict.py` は、どちらも quest_data.py の内容で
quest_master/reward_master を同期していた。両者は「マスタに無い行をどう扱うか」
(前者は空マスタなら削除をスキップする安全弁、後者は確認プロンプト付きで全削除を許す)
という点で目的が異なるため、まずここには **UPSERT の列リストだけ** を寄せた。実際、
繰り返し食い違ってきたのはこの部分である:

- #100: sync_strict 側が `reset_period` を書いておらず、DB列デフォルトの
  'weekly_monday' が入って周期内多重完了ガードが壊れた
- #164: sync_strict 側に `start_time`/`end_time`/`start_date`/`end_date`/
  `occurrence_chance`/`pre_requisite_quest_id` が無く、時間帯限定クエストが
  再UPSERT時に NULL(=終日扱い)へ上書きされた
- #165: sync_strict 側がレガシー列 `desc` だけを書き、アプリが実際に読む
  `description` を更新していなかった(実行順で表示が食い違った)

列の追加漏れは「実行した経路によって結果が変わる」という形で表面化し、
どちらを実行したかを後から特定しづらい。SQL と値の並びをここへ一本化して、
片方だけが古いという状態を構造的に作れなくする。

その後 **Issue #664 の改善案2 で同期処理そのものも統合された**。目的の違いは
`sync_master_data(strict=...)` の引数として表現され、`sync_strict.py` は引数解析と
安全ガードだけの CLI になったため、現在このモジュールの呼び出し元は
`services/quest/game_system.py` の1箇所だけである(それでもここを独立させておく
のは、上記3件の事故が「UPSERT の列リストは同期方針と分けて管理する」という
教訓を残しているため)。

なお `reward_master.desc` はアプリが読まないレガシー列だが(読むのは
`description`)、過去に書かれた行が残っているため、両経路とも
`description` と同じ値を書いて食い違いを残さない方針とする。
"""
from typing import Any, Optional, Tuple

# quest_master の全同期対象列。並びは QUEST_UPSERT_PARAMS と対応する。
QUEST_UPSERT_SQL = """
    INSERT INTO quest_master (
        quest_id, title, description, quest_type, target_user,
        exp_gain, gold_gain, icon_key, day_of_week,
        start_date, end_date, occurrence_chance,
        start_time, end_time, pre_requisite_quest_id, reset_period
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(quest_id) DO UPDATE SET
        title = excluded.title,
        description = excluded.description,
        quest_type = excluded.quest_type,
        target_user = excluded.target_user,
        exp_gain = excluded.exp_gain,
        gold_gain = excluded.gold_gain,
        icon_key = excluded.icon_key,
        day_of_week = excluded.day_of_week,
        start_date = excluded.start_date,
        end_date = excluded.end_date,
        occurrence_chance = excluded.occurrence_chance,
        start_time = excluded.start_time,
        end_time = excluded.end_time,
        pre_requisite_quest_id = excluded.pre_requisite_quest_id,
        reset_period = excluded.reset_period
"""

REWARD_UPSERT_SQL = """
    INSERT INTO reward_master (
        reward_id, title, category, cost_gold, icon_key, description, desc, target
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(reward_id) DO UPDATE SET
        title = excluded.title,
        category = excluded.category,
        cost_gold = excluded.cost_gold,
        icon_key = excluded.icon_key,
        description = excluded.description,
        desc = excluded.desc,
        target = excluded.target
"""


def quest_upsert_params(
    *,
    quest_id: Any,
    title: Any,
    description: Any,
    quest_type: Any,
    target_user: Any,
    exp_gain: Any,
    gold_gain: Any,
    icon_key: Any,
    day_of_week: Any,
    start_date: Any,
    end_date: Any,
    occurrence_chance: Any,
    start_time: Any,
    end_time: Any,
    pre_requisite_quest_id: Any,
    reset_period: Any,
) -> Tuple[Any, ...]:
    """`QUEST_UPSERT_SQL` に渡す値のタプルを、列の並びを間違えない形で組み立てる。

    呼び出し元は Pydantic モデル(`GameSystem.sync_master_data`)と生の dict
    (`sync_strict.py`)で表現が違うため、キーワード引数で受けてここで並べ替える。
    """
    return (
        quest_id, title, description, quest_type, target_user,
        exp_gain, gold_gain, icon_key, day_of_week,
        start_date, end_date, occurrence_chance,
        start_time, end_time, pre_requisite_quest_id, reset_period,
    )


def reward_upsert_params(
    *,
    reward_id: Any,
    title: Any,
    category: Any,
    cost_gold: Any,
    icon_key: Any,
    description: Optional[str],
    target: Any,
) -> Tuple[Any, ...]:
    """`REWARD_UPSERT_SQL` に渡す値のタプルを組み立てる。

    レガシー列 `desc` には `description` と同じ値を入れる(モジュールdocstring参照)。
    """
    return (reward_id, title, category, cost_gold, icon_key, description, description, target)
