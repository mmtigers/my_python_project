## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `routine_data.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | `83b42db` |

## 関連ドキュメント

* [routine_service.md](./routine_service.md) - `FULL_BONUS_EXP`, `FULL_BONUS_GOLD`, `ROUTINE_FLOWS`, `RoutineFlow`, `get_checkpoint_index`を本ファイルからimportして使用する呼び出し元
* [routine_router.md](./routine_router.md) - `routine_service`経由で間接的に本ファイルのデータへ依存するルーター
* [quest_data.md](./quest_data.md) - `FULL_BONUS_GOLD`/`FULL_BONUS_EXP`の値がコメントで参照している`REWARDS`(id=11「Youtube (30:00)」)の定義元
* [migrations/README.md にて言及される`migrations/0010_add_routine_progress.sql`](../../../MY_HOME_SYSTEM/migrations/0010_add_routine_progress.sql) - 本ファイルのコメントが根拠として挙げるマイグレーションSQL(仕様書ドリフト規約によりマイグレーション自体は仕様書対象外だが、コメントの一次ソースとして直接参照)

## 2. ファイルの概要

デイリールーティン(すごろく形式の生活導線UI)の「フロー定義」を保持する定数モジュールである。ファイル冒頭のdocstringが述べる通り、`quest_master`(クエストのマスターデータ)とは異なり、この内容は療育目的で固定された生活動線そのものであり親が編集する対象ではないため、DBテーブルではなくPythonの定数として持つ設計である。朝(`am`)・帰宅後(`pm`)の2つのフロー(`ROUTINE_FLOWS`)を定義し、各フローは開始時刻(`start_trigger_time`)・対象曜日(`day_of_week`)・ステップ列(`steps`)を持つ。ステップのうち1つには`checkpoint_time`(強制切替の締切時刻)を設定でき、そのインデックスを取得するヘルパー関数`get_checkpoint_index`も提供する。フロー完走ボーナスの満額(`FULL_BONUS_GOLD`/`FULL_BONUS_EXP`)もここで定義される。
根拠: [モジュールdocstring] (行番号: 1-8 / 抜粋: "quest_master とは異なり、この内容は療育目的で固定された生活動線そのものであり\n親が編集する対象ではないため、DBテーブルではなくこの定数として持つ\n(migrations/README.md・0010_add_routine_progress.sql参照)。")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `typing.List` | 標準 | 型ヒント(`List[int]`, `List[RoutineStep]`) | 根拠: [インポート宣言] (行番号: 9 / 抜粋: "from typing import List, Optional, TypedDict") |
| `typing.Optional` | 標準 | 型ヒント(`Optional[str]`, `Optional[int]`) | 根拠: [インポート宣言] (行番号: 9 / 抜粋: "from typing import List, Optional, TypedDict") |
| `typing.TypedDict` | 標準 | `RoutineStep`/`RoutineFlow`の型定義基底クラス | 根拠: [インポート宣言] (行番号: 9 / 抜粋: "from typing import List, Optional, TypedDict") |

### ブラックボックスとなる外部要素

該当なし(本ファイルはローカルモジュール・外部パッケージへの依存を持たず、`typing`標準ライブラリのみに依存するため、内部実装が不明な外部要素は存在しない)。
根拠: [インポート一覧の網羅] (行番号: 9 / 抜粋: "from typing import List, Optional, TypedDict")

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `RoutineStep`

* **役割**: すごろくの1マス(1ステップ)を表す型定義。`key`(内部識別子)・`label`(表示名)・`icon_key`(アイコン識別子)・`checkpoint_time`(チェックポイント時刻、'HH:MM'形式または`None`)の4フィールドを持つ`TypedDict`。
* 根拠: [クラス定義] (行番号: 12-16 / 抜粋: "class RoutineStep(TypedDict):\n    key: str\n    label: str\n    icon_key: str\n    checkpoint_time: Optional[str]  # 'HH:MM' 形式。設定されたステップだけが強制切替の対象")


* **引数/リクエスト**: 該当なし(型定義であり呼び出し可能な関数ではない)
* 根拠: [クラス定義] (行番号: 12-16)


* **戻り値/レスポンス**: 該当なし
* 根拠: [クラス定義] (行番号: 12-16)


* **副作用**: なし
* 根拠: [クラス定義] (行番号: 12-16)


* **エラーハンドリング**: なし
* 根拠: [クラス定義] (行番号: 12-16)



### `RoutineFlow`

* **役割**: 1つのすごろくフロー(朝または帰宅後)全体を表す型定義。`title`(表示タイトル)・`day_of_week`(対象曜日のリスト、0=月〜6=日)・`start_trigger_time`(このフローが当日開始される時刻、'HH:MM')・`steps`(`RoutineStep`のリスト)の4フィールドを持つ`TypedDict`。
* 根拠: [クラス定義] (行番号: 19-23 / 抜粋: "class RoutineFlow(TypedDict):\n    title: str\n    day_of_week: List[int]\n    start_trigger_time: str  # 'HH:MM'。この時刻を過ぎると当日分の進捗が開始する\n    steps: List[RoutineStep]")


* **引数/リクエスト**: 該当なし
* 根拠: [クラス定義] (行番号: 19-23)


* **戻り値/レスポンス**: 該当なし
* 根拠: [クラス定義] (行番号: 19-23)


* **副作用**: なし
* 根拠: [クラス定義] (行番号: 19-23)


* **エラーハンドリング**: なし
* 根拠: [クラス定義] (行番号: 19-23)



### `WEEKDAYS`

* **役割**: 月〜金(0〜4)を表す曜日リスト定数。`ROUTINE_FLOWS`内の`am`・`pm`両フローの`day_of_week`として共用される。
* 根拠: [定数宣言] (行番号: 26 / 抜粋: "WEEKDAYS = [0, 1, 2, 3, 4]")


* **引数/リクエスト**: 該当なし
* 根拠: [定数宣言] (行番号: 26)


* **戻り値/レスポンス**: 該当なし(値は`List[int]`のリテラル`[0, 1, 2, 3, 4]`)
* 根拠: [定数宣言] (行番号: 26)


* **副作用**: なし
* 根拠: [定数宣言] (行番号: 26)


* **エラーハンドリング**: なし
* 根拠: [定数宣言] (行番号: 26)



### `FULL_BONUS_GOLD` / `FULL_BONUS_EXP`

* **役割**: フロー完走ボーナスの満額を定義する定数。チェックポイント通過時、チェックポイントより前のステップの達成率に応じて按分される(按分処理の実体は`services/routine_service.py`の`_apply_forced_transition`、[routine_service.md](./routine_service.md)参照)。コメントにより、この満額はquest_data.pyのREWARDS(id=11「Youtube (30:00)」)のごほうび券と同額に設定されていることが明示されている。
* 根拠: [定数宣言・コメント] (行番号: 28-31 / 抜粋: "# フロー完走ボーナスの満額 (Youtube 30分チケット(quest_data.py REWARDS id=11)と同額)。\n# チェックポイント通過時、チェックポイントより前のステップの達成率に応じて按分する。\nFULL_BONUS_GOLD = 150\nFULL_BONUS_EXP = 30")


* **引数/リクエスト**: 該当なし
* 根拠: [定数宣言] (行番号: 30-31)


* **戻り値/レスポンス**: 該当なし(値はそれぞれ`int`リテラル`150`・`30`)
* 根拠: [定数宣言] (行番号: 30-31)


* **副作用**: なし
* 根拠: [定数宣言] (行番号: 30-31)


* **エラーハンドリング**: なし
* 根拠: [定数宣言] (行番号: 30-31)



### `ROUTINE_FLOWS`

* **役割**: フローキー(`'am'`/`'pm'`)をキーとする`RoutineFlow`の辞書。`am`は「起きてから出発まで」(開始05:00、6ステップ、`free`ステップに`checkpoint_time='07:50'`を設定)、`pm`は「帰ってから寝るまで」(開始15:00、6ステップ、`free`ステップに`checkpoint_time='20:00'`を設定)を定義する。いずれも`day_of_week`は`WEEKDAYS`(月〜金)。
* 根拠: [定数宣言] (行番号: 33-63 / 抜粋: "ROUTINE_FLOWS: dict[str, RoutineFlow] = {\n    'am': {\n        'title': '起きてから出発まで',\n        'day_of_week': WEEKDAYS,\n        'start_trigger_time': '05:00',\n        'steps': [")


* **引数/リクエスト**: 該当なし
* 根拠: [定数宣言] (行番号: 33-63)


* **戻り値/レスポンス**: 該当なし(値は`dict[str, RoutineFlow]`のリテラル)
* 根拠: [定数宣言] (行番号: 33-63)


* **副作用**: なし
* 根拠: [定数宣言] (行番号: 33-63)


* **エラーハンドリング**: なし
* 根拠: [定数宣言] (行番号: 33-63)



### `get_checkpoint_index`

* **役割**: 渡された`flow`の`steps`を先頭から走査し、`checkpoint_time`が真値(空文字・None以外)であるステップの最初のインデックスを返す。フロー内にチェックポイントを持つステップが存在しない場合は`None`を返す。
* 根拠: [関数定義] (行番号: 66-71 / 抜粋: "def get_checkpoint_index(flow: RoutineFlow) -> Optional[int]:\n    \"\"\"フロー内でチェックポイント(強制切替の境界)を持つステップのインデックスを返す。\"\"\"\n    for idx, step in enumerate(flow['steps']):\n        if step['checkpoint_time']:\n            return idx\n    return None")


* **引数/リクエスト**: `flow: RoutineFlow`
* 根拠: [関数定義] (行番号: 66 / 抜粋: "def get_checkpoint_index(flow: RoutineFlow) -> Optional[int]:")


* **戻り値/レスポンス**: `Optional[int]` — チェックポイントを持つ最初のステップのインデックス、無ければ`None`
* 根拠: [戻り値] (行番号: 69-71 / 抜粋: "return idx" / "return None")


* **副作用**: なし
* 根拠: [関数定義] (行番号: 66-71、副作用となるI/O・状態変更コードは存在しない)


* **エラーハンドリング**: なし(例外送出処理は実装されていない。`flow['steps']`のキーアクセスは`RoutineFlow`が常に`steps`キーを持つ前提で行われる)
* 根拠: [関数定義] (行番号: 66-71、`try`/`except`は存在しない)



## 5. 処理フロー図

以下は本ファイル唯一の関数である `get_checkpoint_index` のフローチャートです。

```mermaid
flowchart TD
    Start(["Start: get_checkpoint_index(flow)"]) --> Init["idx = 0 (enumerateにより自動採番)"]
    Init --> LoopCheck{"flow['steps']に未走査のステップが残っているか?"}
    LoopCheck -- Yes --> CheckpointCheck{"step['checkpoint_time']は真値か?"}
    CheckpointCheck -- Yes --> ReturnIdx(["return idx"])
    CheckpointCheck -- No --> NextStep["idxを次のステップへ進める"]
    NextStep --> LoopCheck
    LoopCheck -- No（全ステップ走査済み） --> ReturnNone(["return None"])
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "routine_data.py"
        RoutineStep
        RoutineFlow
        WEEKDAYS
        FULL_BONUS_GOLD
        FULL_BONUS_EXP
        ROUTINE_FLOWS
        get_checkpoint_index
    end

    subgraph "標準ライブラリ"
        typing
    end

    subgraph "依存元(このファイルをimportする側)"
        routine_service["services/routine_service.py"]
    end

    RoutineStep --> typing
    RoutineFlow --> typing
    ROUTINE_FLOWS --> RoutineFlow
    ROUTINE_FLOWS --> WEEKDAYS

    routine_service --> ROUTINE_FLOWS
    routine_service --> RoutineFlow
    routine_service --> FULL_BONUS_GOLD
    routine_service --> FULL_BONUS_EXP
    routine_service --> get_checkpoint_index
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `services/routine_service.py` | 本ファイルの定数群(`ROUTINE_FLOWS`, `FULL_BONUS_GOLD`等)を実際にどう消費するか(進捗管理・按分ボーナス計算のロジック)を把握するため。 | `from routine_data import FULL_BONUS_EXP, FULL_BONUS_GOLD, ROUTINE_FLOWS, RoutineFlow, get_checkpoint_index`([routine_service.md](./routine_service.md)側のインポート宣言) |
| 中 | `quest_data.py` | `FULL_BONUS_GOLD`/`FULL_BONUS_EXP`のコメントが同額の根拠として挙げる`REWARDS`(id=11)の実際の定義内容を確認するため。 | 行番号: 28 のコメント(本ファイル) |
| 低 | `migrations/0010_add_routine_progress.sql` | 本ファイルの冒頭docstringが参照する、進捗を保持するDBテーブル`routine_progress`のスキーマ定義とその設計意図(JSONで持つ理由等)を確認するため。 | [モジュールdocstring] (行番号: 1-8) |

## 8. 保守上の注意点

* `pm`フローの`start_trigger_time`は`'15:00'`とハードコードされているが、これは「仮の既定値」であり実際の下校/帰宅時刻に合わせて要調整であるとコメントで明記されている(ユーザー確認事項)。
  根拠: [コメント] (行番号: 52-53 / 抜粋: "# 仮の既定値。実際の下校/帰宅時刻に合わせて要調整(ユーザー確認事項)。\n        'start_trigger_time': '15:00',")
* `FULL_BONUS_GOLD = 150`はquest_data.pyのREWARDS(id=11「Youtube (30:00)」、`cost_gold`)と同額になるよう意図的に設定されている旨がコメントに明記されている。そのため`quest_data.py`側のこの報酬の価格を変更する場合は、本ファイルの`FULL_BONUS_GOLD`も見直しが必要になる(2つの値の同期はコード上強制されておらず、コメントによる申し合わせのみである)。
  根拠: [コメント] (行番号: 28-30 / 抜粋: "# フロー完走ボーナスの満額 (Youtube 30分チケット(quest_data.py REWARDS id=11)と同額)。")
* `am`フローの開始時刻`'05:00'`についても、それより前にアプリを開くと前日分の状態のままになる旨がコメントされている。
  根拠: [コメント] (行番号: 37-39 / 抜粋: "# 朝5:00を過ぎたら当日分のすごろくを開始する(それより前にアプリを開いても\n        # 前日分の状態のまま)。\n        'start_trigger_time': '05:00',")
* `ROUTINE_FLOWS`はDBテーブルではなくこのファイルの定数として持つことが、モジュールdocstringにより意図的な設計として明記されている(療育目的の固定生活動線であり、親が編集する対象ではないため)。将来的にステップ内容を可変にする(親が編集できるようにする)場合は、この設計方針自体の見直しが必要になる。
  根拠: [モジュールdocstring] (行番号: 1-8)

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `start_trigger_time = '15:00'`(pmフロー)の妥当性 | コメント上「仮の既定値」「ユーザー確認事項」と明記されているのみで、実際の下校/帰宅時刻や確定した値・確定時期は本ファイルからは不明。 | ユーザーへの直接確認、または将来の設定ファイル化を示す別コミット |
| `icon_key`の実際の表示アイコンとの対応関係 | 本ファイルでは文字列キー(例: `'wash'`, `'meal'`)が定義されているのみで、これをどのアイコン画像/絵文字に変換するかはフロントエンド側の実装(`family-quest`)に依存し不明。 | `family-quest/src/features/routine/`配下のコンポーネント(本タスクのスコープ外) |
| ステップ内容を将来的に親が編集できるようにする管理UIの計画有無 | モジュールdocstringは「親が編集する対象ではない」という現状の設計方針を述べるのみで、将来的な可変化の計画有無には触れていない。 | 該当ファイルなし(仕様・ロードマップ文書の追加が必要) |

## 10. 自己検証結果

* [x] 完了: 推測・外部ファイルの仕様を一切含んでいない
* [x] 完了: 全関数・全クラス・全定数を列挙した
* [x] 完了: 全てのインポート要素を列挙した
* [x] 完了: すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 完了: 根拠漏れが0件である
* [x] 完了: Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 完了: 不明事項を漏れなく列挙した
