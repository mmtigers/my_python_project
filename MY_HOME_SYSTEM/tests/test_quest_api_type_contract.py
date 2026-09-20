# MY_HOME_SYSTEM/tests/test_quest_api_type_contract.py
"""
Issue #752 (AUDIT-023): GET /api/quest/data のバックエンド↔フロントエンド型契約の回帰テスト。

`get_all_view_data` は `dict[str, Any]` を返し、ルーターにも `response_model` が
無かったため、OpenAPI にレスポンス形状が出ておらず、乖離は実行時にしか分から
なかった。`models/quest.py` の View Models(`GameDataResponse` 配下)がサーバー側
の契約になったので、本ファイルはそれが以下の3方向でずれないことを固定する。

1. **DBスキーマ ↔ Pydantic** … 新しいマイグレーションで `quest_users` 等に列を
   足したとき、`response_model` がその列を無音で落とすのを防ぐ。
   (`response_model` は宣言外のキーを落とすため、宣言漏れ＝APIからの消失になる)
2. **Pydantic ↔ 手書き Zod** … `family-quest/src/lib/gameDataSchema.ts` が要求する
   フィールドをサーバーが宣言し続けていることを確認する。あわせて「サーバーには
   あるがフロントの Zod には無い」フィールドを明示的な許可リストで固定し、
   バックエンドの新フィールド追加が `.strict()` を使わない Zod に無音で
   無視される穴(#470)を CI で検知できるようにする。
3. **実レスポンス ↔ Pydantic** … 実際に `/api/quest/data` を叩き、サービス層が
   返した dict のキーが `response_model` の適用で1つも失われていないことを見る。

なお Alexa 経路(`handlers/alexa_handler.py`)はサービス層を直接呼ぶため
`response_model` の影響を受けない。
"""
import os
import re
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor
from models.quest import (
    GameDataResponse,
    ViewAdventureLog,
    ViewQuest,
    ViewQuestHistory,
    ViewReward,
    ViewUser,
)
from services.quest_service import game_system

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_ZOD_SCHEMA_PATH = os.path.join(_REPO_ROOT, 'family-quest', 'src', 'lib', 'gameDataSchema.ts')


# ------------------------------------------------------------------
# gameDataSchema.ts(手書き Zod)の簡易パーサ
# ------------------------------------------------------------------
def _parse_zod_object_keys(source: str, const_name: str) -> set:
    """`const <const_name> = z.object({...})` のトップレベルのキー名を取り出す。

    Zod を Python から評価することはできないため、波括弧の対応を数えて該当
    ブロックを切り出し、ネスト階層0(=z.object 直下)の `key:` だけを拾う。
    """
    match = re.search(
        r'(?:export\s+)?const\s+' + re.escape(const_name) + r'\s*=\s*z\.object\(\{',
        source,
    )
    if match is None:
        raise AssertionError(f"{const_name} が gameDataSchema.ts に見つからない")

    start = match.end()  # z.object({ の直後
    depth = 0
    end = None
    for i in range(start, len(source)):
        ch = source[i]
        if ch in '{([':
            depth += 1
        elif ch in '})]':
            if depth == 0:
                end = i
                break
            depth -= 1
    assert end is not None, f"{const_name} の z.object({{...}}) が閉じていない"

    body = source[start:end]
    keys = set()
    depth = 0
    for line in body.splitlines():
        stripped = line.strip()
        if depth == 0 and not stripped.startswith('//'):
            key_match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:', stripped)
            if key_match:
                keys.add(key_match.group(1))
        depth += line.count('{') + line.count('(') - line.count('}') - line.count(')')
    return keys


@pytest.fixture(scope="module")
def zod_source() -> str:
    if not os.path.exists(_ZOD_SCHEMA_PATH):
        pytest.skip("family-quest/src/lib/gameDataSchema.ts が存在しない環境")
    with open(_ZOD_SCHEMA_PATH, encoding='utf-8') as f:
        return f.read()


# ------------------------------------------------------------------
# 1. DBスキーマ ↔ Pydantic
# ------------------------------------------------------------------
# (モデル, テーブル名, DB列以外の算出フィールド, 意図的に返していない列)
_TABLE_CONTRACTS = [
    # nextLevelExp/maxHp/hp は game_logic による算出値(get_all_view_data)。
    (ViewUser, 'quest_users', {'nextLevelExp', 'maxHp', 'hp'}, set()),
    # bonus_gold/bonus_exp は連続達成ボーナスの算出値。days は day_of_week から
    # 組み立て直した List[int] で同名の TEXT 列を上書きしている。
    (ViewQuest, 'quest_master', {'bonus_gold', 'bonus_exp'}, set()),
    # #291: desc は description の同期用レガシー列で、ビュー応答からは落とす。
    (ViewReward, 'reward_master', set(), {'desc'}),
    (ViewQuestHistory, 'quest_history', set(), set()),
]


@pytest.mark.parametrize(
    "model, table, computed_fields, intentionally_dropped",
    _TABLE_CONTRACTS,
    ids=[c[1] for c in _TABLE_CONTRACTS],
)
def test_view_model_fields_match_table_columns(
    isolated_db, model, table, computed_fields, intentionally_dropped
):
    """View モデルの宣言と実テーブルの列構成が一致していること。

    失敗したときは「マイグレーションで列を足したが View モデルに足していない」
    可能性が高い。response_model は宣言外のキーを落とすので、放置すると
    その列は API から無音で消える(= #752 が防ごうとしている失敗モード)。
    """
    with get_db_cursor() as cur:
        columns = {row['name'] for row in cur.execute(f"PRAGMA table_info({table})")}
    assert columns, f"{table} の列情報が取得できない"

    expected = (columns - intentionally_dropped) | computed_fields
    actual = set(model.model_fields)
    assert actual == expected, (
        f"{model.__name__} と {table} の構成が乖離している。\n"
        f"  モデルにのみ存在: {sorted(actual - expected)}\n"
        f"  テーブルにのみ存在: {sorted(expected - actual)}"
    )


# ------------------------------------------------------------------
# 2. Pydantic ↔ 手書き Zod
# ------------------------------------------------------------------
# サーバーは返しているがフロントの Zod が宣言していないフィールドの許可リスト。
# .strict() を使わない Zod はこれらを無音で捨てるため、「捨ててよい」と判断済み
# のものだけをここに書く。新しいフィールドを増やしたときは、フロントで使うなら
# gameDataSchema.ts に、使わないならここに追記する(どちらもせずに済ませられない
# ようにするのが本テストの目的。#470)。
_BACKEND_ONLY_FIELDS = {
    'userSchema': {
        # #327: HP 表示 UI は廃止済み。サーバーは送出し続けている。
        'hp', 'maxHp',
        # 表示に使っていない監査用カラム。
        'updated_at',
    },
    'questSchema': {
        # days(List[int])の生成元。フロントは days しか見ない。
        'day_of_week',
        # サーバー側の出現判定(_is_quest_currently_active)専用。
        'occurrence_chance', 'start_date', 'end_date', 'reset_period',
    },
    'rewardSchema': set(),
    'questHistorySchema': {
        'completed_at',
        'medals_earned',
    },
}

_SCHEMA_TO_MODEL = {
    'userSchema': ViewUser,
    'questSchema': ViewQuest,
    'rewardSchema': ViewReward,
    'questHistorySchema': ViewQuestHistory,
}


@pytest.mark.parametrize("schema_name", sorted(_SCHEMA_TO_MODEL))
def test_zod_fields_are_declared_by_the_backend(zod_source, schema_name):
    """フロントが期待するフィールドをサーバーが宣言し続けていること。"""
    model = _SCHEMA_TO_MODEL[schema_name]
    zod_keys = _parse_zod_object_keys(zod_source, schema_name)
    assert zod_keys, f"{schema_name} のキーを1つも抽出できなかった(パーサの破損)"

    missing = zod_keys - set(model.model_fields)
    assert not missing, (
        f"gameDataSchema.ts の {schema_name} が要求する {sorted(missing)} を "
        f"{model.__name__} が宣言していない。response_model 適用後は常に "
        f"undefined になるため、どちらかを直すこと。"
    )


@pytest.mark.parametrize("schema_name", sorted(_SCHEMA_TO_MODEL))
def test_backend_only_fields_are_explicitly_acknowledged(zod_source, schema_name):
    """サーバー側にしか無いフィールドが許可リストと完全に一致すること。"""
    model = _SCHEMA_TO_MODEL[schema_name]
    zod_keys = _parse_zod_object_keys(zod_source, schema_name)

    backend_only = set(model.model_fields) - zod_keys
    expected = _BACKEND_ONLY_FIELDS[schema_name]
    assert backend_only == expected, (
        f"{model.__name__} とフロントの {schema_name} の差分が想定と違う。\n"
        f"  新たにフロントへ届かなくなる/届くようになったフィールド: "
        f"{sorted(backend_only ^ expected)}\n"
        f"  フロントで使うなら gameDataSchema.ts に、使わないなら本テストの "
        f"_BACKEND_ONLY_FIELDS に追記すること。"
    )


def test_top_level_response_keys_match_the_frontend_schema(zod_source):
    """トップレベルのキー(users/quests/... )が Zod と整合していること。"""
    zod_keys = _parse_zod_object_keys(zod_source, 'gameDataResponseSchema')
    model_keys = set(GameDataResponse.model_fields)
    assert zod_keys - model_keys == set(), (
        f"フロントが要求するトップレベルキー {sorted(zod_keys - model_keys)} を "
        f"GameDataResponse が宣言していない"
    )
    # #412: logs はフロントが参照しないため Zod に無いが、サーバーは返している。
    assert model_keys - zod_keys == {'logs'}


# ------------------------------------------------------------------
# 3. 実レスポンス ↔ Pydantic
# ------------------------------------------------------------------
def _seed_view_data():
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
            "('dad', 'Dad', 'Warrior', 1, 0, 100, 'role_adult'), "
            "('son', 'Son', 'Novice', 1, 0, 10, 'role_child')"
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES "
            "(101, 'TestQuest', 'daily', 10, 5)"
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (201, 'TestReward', 50)"
        )


def test_response_model_drops_no_field_of_the_service_payload(isolated_db, api_client):
    """response_model の適用でサービス層の返すキーが1つも失われないこと。"""
    _seed_view_data()
    # 承認済み/承認待ちの履歴を1件ずつ作り、completedQuests/pendingQuests/logs を
    # 空でない状態にしてから比較する(空配列だと要素のキー比較ができない)。
    # 大人の完了報告は即時承認、子どもの完了報告は承認待ちになる。
    adult = api_client.post("/api/quest/complete", json={"user_id": "dad", "quest_id": 101})
    assert adult.status_code == 200
    child = api_client.post("/api/quest/complete", json={"user_id": "son", "quest_id": 101})
    assert child.status_code == 200

    raw = game_system.get_all_view_data("dad")
    res = api_client.get("/api/quest/data", params={"viewer_user_id": "dad"})
    assert res.status_code == 200
    body = res.json()

    assert set(body) == set(raw), (
        f"トップレベルのキーが response_model で変化した: "
        f"{sorted(set(raw) ^ set(body))}"
    )

    for key in raw:
        assert raw[key], f"{key} が空でこの比較が意味を成さない(シードの見直しが必要)"
        assert len(body[key]) == len(raw[key]), f"{key} の要素数が変化した"
        for served, original in zip(body[key], raw[key]):
            dropped = set(original) - set(served)
            assert not dropped, (
                f"{key} の要素から {sorted(dropped)} が response_model で落ちた。"
                f"models/quest.py の View モデルに宣言を追加すること。"
            )


def test_openapi_exposes_the_game_data_response_schema(api_client):
    """/api/quest/data のレスポンス形状が OpenAPI に出ていること(#752 の主目的)。"""
    schema = api_client.get("/openapi.json").json()
    responses = schema['paths']['/api/quest/data']['get']['responses']
    ref = responses['200']['content']['application/json']['schema']['$ref']
    assert ref.endswith('/GameDataResponse')

    components = schema['components']['schemas']
    for model in (ViewUser, ViewQuest, ViewReward, ViewQuestHistory, ViewAdventureLog):
        assert model.__name__ in components
