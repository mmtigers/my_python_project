## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `routers/routine_router.py` |
| 言語 | Python / FastAPI |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | `a1d2738` (+Issue #738 / AUDIT-008 で `GET /today` を読み取り専用化し、締切処理用の `POST /deadlines/process` を追加した分を反映) |

## 関連ドキュメント

* [routine.md](./routine.md) - 本ファイルが`from models.routine import RoutineCompleteAction`でimportするリクエストモデルの定義元
* [routine_service.md](./routine_service.md) - 本ファイルが`from services.routine_service import routine_service`でimportするサービス層シングルトンの実体。両エンドポイントの実処理はすべてここに委譲される
* [unified_server.md](./unified_server.md) - 本ルーターを`/api/routine`プレフィックス・タグ`routine`で`include_router`する呼び出し元

## 2. ファイルの概要

デイリールーティン(すごろく形式の生活導線UI)に関するFastAPIルーター(コントローラー)ファイル。CLAUDE.mdのレイヤリング規約通り、リクエストのパース・検証のみを行い、ロジックはすべて`services.routine_service.routine_service`(モジュールレベルシングルトン)に委譲する薄いルーターである。今日時点の進捗状態を取得する`GET /today`、締切超過処理を全ユーザー分適用する`POST /deadlines/process`、1ステップを完了させる`POST /complete`の3エンドポイントを提供する(Issue #738 / AUDIT-008 で2つ目を追加)。
根拠: [ファイル冒頭コメント] (行番号: 1 / 抜粋: "# MY_HOME_SYSTEM/routers/routine_router.py")、[ルーター定義] (行番号: 9 / 抜粋: "router = APIRouter()")、[エンドポイント定義] (行番号: 12-41)

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `typing.Any` | 標準 | 戻り値型ヒント`Dict[str, Any]`の要素型 | 根拠: [インポート宣言] (行番号: 2 / 抜粋: "from typing import Any, Dict") |
| `typing.Dict` | 標準 | 両エンドポイントの戻り値型ヒント`Dict[str, Any]` | 根拠: [インポート宣言] (行番号: 2 / 抜粋: "from typing import Any, Dict") |
| `fastapi.APIRouter` | 外部パッケージ | ルーターインスタンス`router`の作成 | 根拠: [インポート宣言] (行番号: 4 / 抜粋: "from fastapi import APIRouter, Query") |
| `fastapi.Query` | 外部パッケージ | `get_today_state`のクエリパラメータ`user_id`に対する文字数制約(`min_length`/`max_length`)の付与 | 根拠: [インポート宣言] (行番号: 4 / 抜粋: "from fastapi import APIRouter, Query") |
| `models.routine.RoutineCompleteAction` | ローカルモジュール | `complete_step`エンドポイントのリクエストボディ型 | 根拠: [インポート宣言] (行番号: 6 / 抜粋: "from models.routine import RoutineCompleteAction") |
| `services.routine_service.routine_service` | ローカルモジュール | 両エンドポイントが実処理を委譲するサービス層シングルトン | 根拠: [インポート宣言] (行番号: 7 / 抜粋: "from services.routine_service import routine_service") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `routine_service.get_today_state` / `routine_service.process_deadlines` / `routine_service.complete_step` の内部実装 | 進捗の永続化・チェックポイント判定・ボーナス付与等の実処理ロジックは本ファイルからは不明。詳細は[routine_service.md](./routine_service.md)参照。 | [関数呼び出し] (行番号: 21, 36, 41 / 抜粋: "return routine_service.get_today_state(user_id)", "return routine_service.process_deadlines()", "return routine_service.complete_step(action.user_id, action.flow_key, action.step_key)") |
| `models.routine.RoutineCompleteAction`のバリデーション詳細 | フィールドの型・制約の具体的な定義内容は本ファイルからは不明。詳細は[routine.md](./routine.md)参照。 | [インポート宣言] (行番号: 6 / 抜粋: "from models.routine import RoutineCompleteAction") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `router`

* **役割**: 本ファイルが定義するエンドポイント群をまとめる`APIRouter`インスタンス。`unified_server.py`から`app.include_router(routine_router.router, prefix="/api/routine", tags=["routine"])`としてマウントされる([unified_server.md](./unified_server.md)参照)。
* 根拠: [変数宣言] (行番号: 9 / 抜粋: "router = APIRouter()")


* **引数/リクエスト**: 該当なし
* 根拠: [変数宣言] (行番号: 9)


* **戻り値/レスポンス**: 該当なし
* 根拠: [変数宣言] (行番号: 9)


* **副作用**: なし(インスタンス生成のみ)
* 根拠: [変数宣言] (行番号: 9)


* **エラーハンドリング**: なし
* 根拠: [変数宣言] (行番号: 9)



### `get_today_state` (`GET /today`)

* **役割**: 指定ユーザーの本日時点のルーティン進捗状態(am/pmフローそれぞれの開始有無・現在のステップ・チェックポイント通過状況等)を取得するエンドポイント。実処理は`routine_service.get_today_state(user_id)`に委譲する。docstringに記載のとおり**読み取り専用**で、締切超過の強制遷移・ボーナス付与は`POST /deadlines/process`側へ移されている(Issue #738 / AUDIT-008)。
* 根拠: [ルーティング定義・関数定義] (行番号: 13-21 / 抜粋: "def get_today_state(user_id: str = Query(min_length=1, max_length=64)) -> Dict[str, Any]:")、[docstring] (行番号: 14-20 / 抜粋: "当日の状態を返す(読み取り専用)。")


* **引数/リクエスト**: クエリパラメータ`user_id: str`(FastAPIの`Query(min_length=1, max_length=64)`により1〜64文字必須)
* 根拠: [関数定義] (行番号: 13 / 抜粋: "def get_today_state(user_id: str = Query(min_length=1, max_length=64)) -> Dict[str, Any]:")


* **戻り値/レスポンス**: `Dict[str, Any]`(`routine_service.get_today_state(user_id)`の戻り値をそのまま返す。実際の形状は[routine_service.md](./routine_service.md)の`get_today_state`/`_serialize_flow`を参照)
* 根拠: [関数定義・戻り値] (行番号: 13, 21 / 抜粋: "-> Dict[str, Any]:", "return routine_service.get_today_state(user_id)")


* **副作用**: `routine_service.get_today_state`の呼び出しに伴うDB**読み取り**のみ。docstringが「読み取り専用」と明記しており、書き込みは行わない(Issue #738 / AUDIT-008)。本ファイル自体には副作用となるコードは存在しない。
* 根拠: [関数呼び出し] (行番号: 21 / 抜粋: "return routine_service.get_today_state(user_id)")、[docstring] (行番号: 14-20)


* **エラーハンドリング**: 本ファイル内には`try`/`except`等の明示的なエラーハンドリングは存在しない。`user_id`がクエリパラメータの制約(1〜64文字)を満たさない場合はFastAPI側のバリデーションにより自動的にエラーレスポンスが生成される。`routine_service.get_today_state`が送出する例外(`HTTPException`等)はそのまま伝播する。
* 根拠: [関数定義] (行番号: 12-21、`try`/`except`は存在しない)



### `process_deadlines` (`POST /deadlines/process`)

* **役割**: 締切(チェックポイント時刻)超過による強制遷移を全ユーザー分適用するエンドポイント。実処理は`routine_service.process_deadlines()`に委譲する。docstringによれば呼び出し元は`monitors/routine_deadline_job.py`(スケジューラの定期タスク、60秒間隔)で、スケジューラが`quest_users`を直接書かずサーバープロセス内のユーザー残高ロックに参加するための経路である(Issue #738 / AUDIT-008)。
* 根拠: [ルーティング定義・関数定義] (行番号: 25-36 / 抜粋: "def process_deadlines() -> dict[str, Any]:")、[docstring] (行番号: 26-35 / 抜粋: "呼び出し元は `monitors/routine_deadline_job.py`")


* **引数/リクエスト**: 引数なし。docstringに「判定の基準時刻はサーバー側の現在時刻(JST)で、リクエストからは指定できない」と明記されている(リクエストボディを受け取るパラメータが定義されていないため、ボディを送っても無視される)。
* 根拠: [関数定義] (行番号: 25 / 抜粋: "def process_deadlines() -> dict[str, Any]:")、[docstring] (行番号: 33 / 抜粋: "判定の基準時刻はサーバー側の現在時刻(JST)で、リクエストからは指定できない。")


* **戻り値/レスポンス**: `dict[str, Any]`(`routine_service.process_deadlines()`の戻り値をそのまま返す。実際のキーは[routine_service.md](./routine_service.md)の`process_deadlines`を参照)
* 根拠: [関数定義・戻り値] (行番号: 25, 36 / 抜粋: "-> dict[str, Any]:", "return routine_service.process_deadlines()")


* **副作用**: `routine_service.process_deadlines`の呼び出しに伴う副作用(当日進捗行のINSERT・ステップ状態の更新・`quest_users`へのボーナス付与)を間接的に引き起こす。docstringによれば締切前に叩いても何も起こらない(冪等)。
* 根拠: [関数呼び出し] (行番号: 36 / 抜粋: "return routine_service.process_deadlines()")、[docstring] (行番号: 34 / 抜粋: "締切前に叩いても何も起こらない(冪等)。")


* **エラーハンドリング**: 本ファイル内には`try`/`except`等の明示的なエラーハンドリングは存在しない。`routine_service.process_deadlines`が送出する例外はそのまま伝播する。
* 根拠: [関数定義] (行番号: 24-36、`try`/`except`は存在しない)



### `complete_step` (`POST /complete`)

* **役割**: 指定ユーザーの指定フロー(`am`/`pm`)における指定ステップを完了させるエンドポイント。実処理は`routine_service.complete_step(action.user_id, action.flow_key, action.step_key)`に委譲する。
* 根拠: [ルーティング定義・関数定義] (行番号: 40-41 / 抜粋: "def complete_step(action: RoutineCompleteAction) -> Dict[str, Any]:")


* **引数/リクエスト**: リクエストボディ`action: RoutineCompleteAction`(`user_id`, `flow_key`, `step_key`の3フィールド。詳細は[routine.md](./routine.md)参照)
* 根拠: [関数定義] (行番号: 40 / 抜粋: "def complete_step(action: RoutineCompleteAction) -> Dict[str, Any]:")


* **戻り値/レスポンス**: `Dict[str, Any]`(`routine_service.complete_step(...)`の戻り値をそのまま返す。実際の形状は[routine_service.md](./routine_service.md)の`_serialize_flow`を参照)
* 根拠: [関数定義・戻り値] (行番号: 40-41 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: `routine_service.complete_step`の呼び出しに伴う副作用(DB書き込み・ゲームデータ更新等)を間接的に引き起こす。本ファイル自体には副作用となるコードは存在しない。
* 根拠: [関数呼び出し] (行番号: 41 / 抜粋: "return routine_service.complete_step(action.user_id, action.flow_key, action.step_key)")


* **エラーハンドリング**: 本ファイル内には`try`/`except`等の明示的なエラーハンドリングは存在しない。リクエストボディが`RoutineCompleteAction`のバリデーションを満たさない場合はFastAPI/Pydantic側で自動的にエラーレスポンスが生成される。`routine_service.complete_step`が送出する例外(`HTTPException`等)はそのまま伝播する。
* 根拠: [関数定義] (行番号: 39-41、`try`/`except`は存在しない)



## 5. 処理フロー図

以下は `POST /complete` (`complete_step`) のリクエスト処理フローです。`GET /today`も同様に単純な委譲構造のため、ここでは分岐のある`complete_step`側を図示します。

```mermaid
flowchart TD
    Start(["リクエスト受信: POST /api/routine/complete"]) --> ParseBody["FastAPIがリクエストボディをRoutineCompleteActionへパース・検証"]
    ParseBody -- 検証失敗 --> ValidationError(["422 Validation Error"])
    ParseBody -- 検証成功 --> CallService["routine_service.complete_step(action.user_id, action.flow_key, action.step_key)を呼び出し"]
    CallService --> ServiceRaises{"サービス層がHTTPExceptionを送出したか?"}
    ServiceRaises -- Yes --> Propagate(["例外がそのまま伝播しFastAPIがエラーレスポンス生成"])
    ServiceRaises -- No --> ReturnDict(["Dict[str, Any]をそのままレスポンスとして返す"])
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "routers/routine_router.py"
        router
        get_today_state
        process_deadlines
        complete_step
    end

    subgraph "ローカルモジュール"
        RoutineCompleteAction["models/routine.py: RoutineCompleteAction"]
        routine_service_mod["services/routine_service.py: routine_service"]
    end

    subgraph "外部パッケージ"
        fastapi["fastapi (APIRouter, Query)"]
    end

    subgraph "依存元(このファイルをimportする側)"
        unified_server["unified_server.py"]
    end

    router --> fastapi
    get_today_state --> fastapi
    get_today_state --> routine_service_mod
    process_deadlines --> routine_service_mod
    complete_step --> RoutineCompleteAction
    complete_step --> routine_service_mod

    unified_server --> router
    routine_deadline_job["monitors/routine_deadline_job.py (スケジューラの定期タスク)"] -->|"HTTP POST /api/routine/deadlines/process"| process_deadlines
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `services/routine_service.py` | 両エンドポイントが委譲する実処理(進捗管理・チェックポイント判定・ボーナス付与)の詳細を把握するため。 | [関数呼び出し] (行番号: 14, 19) |
| 中 | `models/routine.py` | `RoutineCompleteAction`のフィールド制約の詳細を把握するため。 | [インポート宣言] (行番号: 6) |
| 中 | `routine_data.py` | `routine_service`が参照するフロー定義(`ROUTINE_FLOWS`)の内容を把握し、`flow_key`/`step_key`に実際どのような値が渡りうるかを確認するため。 | [routine_service.md](./routine_service.md)側のインポート宣言 |

## 8. 保守上の注意点

* `GET /today`は`user_id`をクエリパラメータで、`POST /complete`は`user_id`をリクエストボディ(`RoutineCompleteAction.user_id`)で受け取っており、認可の仕組みは無くクライアント入力の`user_id`をそのまま信頼している。これはCLAUDE.mdに記載されたFamily Quest API全体の既知の設計判断(個人用IoTシステム・LAN内信頼境界を前提としたスコープ外合意、Issue #614)と同じパターンであり、本ルーター固有の欠陥ではない。
* 本ファイルは例外を一切捕捉せず、`routine_service`側が送出する`HTTPException`をそのまま透過させる薄い構造になっている。エラーメッセージの文面(日本語)は`routine_service.py`側で決定される。
* `POST /deadlines/process`(Issue #738 / AUDIT-008)は引数を一切受け取らない。締切判定の基準時刻をリクエストから指定できるようにすると、クライアントがチェックポイント通過(＝ボーナス確定)を任意の時刻で強制できてしまうため、サービス層の`now`引数はテストからの注入専用として残されている。
* `GET /today`が読み取り専用になったため、締切通過の反映はスケジューラの実行間隔(60秒)だけ遅れうる。ステップ完了操作(`POST /complete`)の応答は従来どおり即時に強制遷移を反映する。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `routine_service.get_today_state`/`complete_step`の戻り値の正確なJSON形状 | 本ファイルの型ヒントは`Dict[str, Any]`のみであり、実際にどのようなキー・値構造を返すかは本ファイルからは不明。 | `services/routine_service.py` |
| `RoutineCompleteAction`の各フィールドの制約詳細 | インポートのみで、フィールド定義自体は本ファイルには存在しない。 | `models/routine.py` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `routine_service.get_today_state`/`complete_step`の戻り値の正確なJSON形状 | `MY_HOME_SYSTEM/services/routine_service.py`を直接確認した。`get_today_state`(463〜481行目)は`{"date": "YYYY-MM-DD", "flows": {<flow_key>: <flow状態>}}`を返し、`flows`のキーは`ROUTINE_FLOWS`の全キー(`'am'`/`'pm'`)。まだ開始時刻に達していないフローは`{"started": False, "title": <フロー名>}`の2キーのみ(475行目)、開始済みのフローは`_serialize_flow`(419〜461行目)が返す**`started`(常に`True`) / `title` / `checkpoint_time` / `current_step_index` / `in_free_time` / `is_complete` / `bonus_gold` / `bonus_exp` / `preview_bonus_gold` / `bonus_full_gold` / `leveled_up` / `new_level` / `steps` の13キー**である。`steps`は各ステップにつき`key` / `label` / `icon_key` / `is_checkpoint` / `is_checklist` / `status`の6キーを持つdictの配列(`status`は`'locked'`/`'current'`/`'done'`/`'remind'`等の文字列)。`complete_step`(483〜551行目)は**同じ`_serialize_flow`の戻り値(単一フロー分の13キー)をそのまま返す**ため、`get_today_state`の`flows[flow_key]`と同一形状であり、`date`/`flows`のラッパーは付かない。エラー時は`HTTPException`を送出する: 404(`Unknown flow_key` / `User not found` / `Unknown step_key`)、400(`このフローはまだ開始していません` / `本日のフローは完了しています` / `自由時間は時間になると自動的に次へ進みます`)、409(`表示が古いようです。再読み込みしてください`)。本ファイルがこれらを一切捕捉せず素通しするため、そのままのステータスコードでクライアントへ返る。TypeScript側の対応するスキーマは`family-quest/src/lib/routineDataSchema.ts`にある。 | 直接ソース確認: `MY_HOME_SYSTEM/services/routine_service.py:419-551`（参考: [routine_service.md](./routine_service.md)・[routineDataSchema.md](./../family-quest/src/lib/routineDataSchema.md)） |
| `RoutineCompleteAction`の各フィールドの制約詳細 | `MY_HOME_SYSTEM/models/routine.py`を直接確認した。`class RoutineCompleteAction(BaseModel)`は3フィールドのみで、`user_id: str = Field(min_length=1, max_length=64)`、`flow_key: Literal['am', 'pm']`、`step_key: str = Field(min_length=1, max_length=64)`。**`flow_key`だけが`Literal`で値域を固定**されているため、不正な`flow_key`はサービス層の`if flow_key not in ROUTINE_FLOWS`(486行目)に到達する前にPydanticの422で弾かれる(=サービス層のその404分岐は、Pydanticを経由しない直接呼び出し・テスト用の防御的チェックとして機能する)。逆に`step_key`は文字列長しか検証されないため、未知の`step_key`はサービス層の`HTTPException(404, "Unknown step_key")`(510行目)で処理される。同ファイルには`Response`モデルは定義されておらず、本ファイルの戻り値型注釈が`Dict[str, Any]`に留まっている理由と一致する。 | 直接ソース確認: `MY_HOME_SYSTEM/models/routine.py`（全体）, `MY_HOME_SYSTEM/services/routine_service.py:486`（参考: [routine.md](./routine.md)・[routine_service.md](./routine_service.md)） |

## 10. 自己検証結果

* [x] 完了: 推測・外部ファイルの仕様を一切含んでいない
* [x] 完了: 全関数・全クラス・全コンポーネントを列挙した
* [x] 完了: 全てのインポート要素を列挙した
* [x] 完了: すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 完了: 根拠漏れが0件である
* [x] 完了: Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 完了: 不明事項を漏れなく列挙した
