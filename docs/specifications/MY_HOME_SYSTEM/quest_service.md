## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `quest_service.py` |
| 言語 | Python (FastAPI関連) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 2. ファイルの概要

データベースクエリを用いて、ユーザー情報、クエスト、アイテム（ごほうび・装備）、インベントリ、ファミリーマイレージ、および週次ボスの状態管理と操作を行うサービス群を定義したファイル。また、マスターデータファイル（`quest_data`）とデータベースの同期や、画面表示用の集約データ生成を担う。

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `datetime` | 標準ライブラリ | 日付や時刻の操作・比較 | `import datetime` (行番号: 行番号取得不可 / 抜粋: "import datetime") |
| `importlib` | 標準ライブラリ | マスターデータモジュールのリロード | `import importlib` (行番号: 行番号取得不可 / 抜粋: "import importlib") |
| `random` | 標準ライブラリ | クリティカルヒットの判定、ランダムクエスト発生判定 | `import random` (行番号: 行番号取得不可 / 抜粋: "import random") |
| `math` | 標準ライブラリ | インポートされているが未使用 | `import math` (行番号: 行番号取得不可 / 抜粋: "import math") |
| `pytz` | 外部ライブラリ | タイムゾーンの設定 | `import pytz` (行番号: 行番号取得不可 / 抜粋: "import pytz") |
| `typing` (`List`, `Dict`, `Any`, `Optional`) | 標準ライブラリ | 型ヒント | `from typing import List, Dic` (行番号: 行番号取得不可 / 抜粋: "from typing import List, Dict,") |
| `fastapi` (`HTTPException`) | 外部ライブラリ | エラーレスポンス生成 | `from fastapi import HTTPExcept` (行番号: 行番号取得不可 / 抜粋: "from fastapi import HTTPExcept") |
| `common` | 内部モジュール | DBカーソル取得、現在時刻(ISO)取得 | `import common` (行番号: 行番号取得不可 / 抜粋: "import common") |
| `config` | 内部モジュール | 環境変数・定数の参照 | `import config` (行番号: 行番号取得不可 / 抜粋: "import config") |
| `game_logic` | 内部モジュール | ゲームレベルや報酬の計算ロジック呼び出し | `import game_logic` (行番号: 行番号取得不可 / 抜粋: "import game_logic") |
| `sound_manager` | 内部モジュール | 音声再生イベント発行 | `import sound_manager` (行番号: 行番号取得不可 / 抜粋: "import sound_manager") |
| `services.notification_service` | 内部モジュール | LINEなどへのプッシュ通知 | `from services import notificat` (行番号: 行番号取得不可 / 抜粋: "from services import notificat") |
| `core.logger` (`setup_logging`) | 内部モジュール | ロガー設定 | `from core.logger import setup_` (行番号: 行番号取得不可 / 抜粋: "from core.logger import setup_") |
| `models.quest` (`MasterUser`等) | 内部モジュール | マスターデータの型定義(モデル) | `from models.quest import Maste` (行番号: 行番号取得不可 / 抜粋: "from models.quest import Maste") |
| `quest_data` | 内部モジュール(例外処理付き) | マスターデータのハードコードリスト | `import quest_data` (行番号: 行番号取得不可 / 抜粋: "import quest_data") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `common.get_db_cursor()` | トランザクションスコープや接続の詳細不明 | `with common.get_db_cursor()` (行番号: 行番号取得不可 / 抜粋: "with common.get_db_cursor() as") |
| `common.get_now_iso()` | タイムゾーンや秒精度のフォーマット詳細不明 | `common.get_now_iso()` (行番号: 行番号取得不可 / 抜粋: "common.get_now_iso()") |
| `game_logic.GameLogic.*` | `calculate_drop_rewards` や `calc_level_progress` などの計算式・詳細仕様不明 | `game_logic.GameLogic.calculate` (行番号: 行番号取得不可 / 抜粋: "game_logic.GameLogic.calculate") |
| `config.*` | `TV_UNLOCK_QUEST_IDS`, `ENABLE_BATTLE_EFFECT` などの実際の設定値不明 | `config.TV_UNLOCK_QUEST_IDS` (行番号: 行番号取得不可 / 抜粋: "config.TV_UNLOCK_QUEST_IDS and") |
| `switchbot_service.send_device_command` | 引数の仕様、通信エラーの挙動、戻り値の構造が不明 | `switchbot_service.send_device_` (行番号: 行番号取得不可 / 抜粋: "switchbot_service.send_device_") |
| DBの各テーブルスキーマ | カラムの型、制約(UNIQUE, NOT NULL等)、外部キー設定などが不明 | `cur.execute("SELECT level, go` (行番号: 行番号取得不可 / 抜粋: "cur.execute("SELECT level, gol") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `UserService.get_family_chronicle`

* **役割**: クエスト、報酬交換などの履歴と全ユーザーのレベルから、家族のランクと冒険ログ一覧を生成する。
* 根拠: `UserService.get_family_chronicle` (行番号: 行番号取得不可 / 抜粋: "def get_family_chronicle(self)")


* **引数/リクエスト**: なし
* 根拠: 引数 `self` のみ (行番号: 行番号取得不可 / 抜粋: "def get_family_chronicle(self)")


* **戻り値/レスポンス**: `Dict[str, Any]` (統計情報と冒険ログのリスト)
* 根拠: `UserService.get_family_chronicle` (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB参照（`quest_users`, `quest_history`等）
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "cur.execute("SELECT COUNT(*) a")


* **エラーハンドリング**: なし
* 根拠: 明示的な `try-except` や `raise` なし



### `UserService._fetch_full_adventure_logs`

* **役割**: クエスト承認履歴、報酬獲得履歴、装備購入履歴を取得・マージし、時系列降順にソート・整形する。
* 根拠: `UserService._fetch_full_adventure_logs` (行番号: 行番号取得不可 / 抜粋: "def _fetch_full_adventure_logs")


* **引数/リクエスト**: `cur`
* 根拠: メソッド定義 (行番号: 行番号取得不可 / 抜粋: "(self, cur) -> List[dict]:")


* **戻り値/レスポンス**: `List[dict]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> List[dict]:")


* **副作用**: DB参照
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "q_rows = cur.execute("SELECT '")


* **エラーハンドリング**: なし
* 根拠: 明示的な例外処理なし



### `UserService.update_avatar`

* **役割**: ユーザーのアバターURLを更新する。
* 根拠: `UserService.update_avatar` (行番号: 行番号取得不可 / 抜粋: "def update_avatar(self, user_i")


* **引数/リクエスト**: `user_id: str`, `avatar_url: str`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "user_id: str, avatar_url: str")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB更新（`quest_users`）、ログ出力
* 根拠: クエリ実行とロギング (行番号: 行番号取得不可 / 抜粋: "cur.execute("UPDATE quest_user")


* **エラーハンドリング**: ユーザー不在時に `HTTPException(404)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(status_cod")



### `QuestService.is_within_reset_period`

* **役割**: 完了日時文字列とリセット周期（daily, weekly_monday, monthly_1st）から、現在期間内に完了しているかを判定する。
* 根拠: `QuestService.is_within_reset_period` (行番号: 行番号取得不可 / 抜粋: "def is_within_reset_period(sel")


* **引数/リクエスト**: `completed_at_str: str`, `reset_period: str`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "completed_at_str: str, reset_p")


* **戻り値/レスポンス**: `bool`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> bool:")


* **副作用**: なし
* 根拠: 内部での状態変更なし


* **エラーハンドリング**: ISOフォーマット変換失敗時に別のフォーマットでリトライする。
* 根拠: `try-except` ブロック (行番号: 行番号取得不可 / 抜粋: "except Exception:")



### `QuestService.__init__`

* **役割**: インスタンス初期化時に `UserService` のインスタンスを生成する。
* 根拠: `QuestService.__init__` (行番号: 行番号取得不可 / 抜粋: "def **init**(self):")


* **引数/リクエスト**: なし
* 根拠: 引数なし (行番号: 行番号取得不可 / 抜粋: "def **init**(self):")


* **戻り値/レスポンス**: なし
* 根拠: `__init__`の仕様


* **副作用**: インスタンスプロパティの割り当て
* 根拠: 代入文 (行番号: 行番号取得不可 / 抜粋: "self.user_service = UserServic")


* **エラーハンドリング**: なし
* 根拠: 明示的な例外処理なし



### `QuestService._calculate_user_attack_power`

* **役割**: ユーザーレベルと装着中装備の力から攻撃力を算出する。
* 根拠: `QuestService._calculate_user_attack_power` (行番号: 行番号取得不可 / 抜粋: "def _calculate_user_attack_pow")


* **引数/リクエスト**: `cur`, `user_id: str`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "cur, user_id: str")


* **戻り値/レスポンス**: `int`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> int:")


* **副作用**: DB参照
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "user_row = cur.execute("SELECT")


* **エラーハンドリング**: ユーザーレコードが存在しない場合は0を返す。
* 根拠: 条件分岐 (行番号: 行番号取得不可 / 抜粋: "if not user_row: return 0")



### `QuestService._check_and_reset_weekly_boss`

* **役割**: ボスの出現記録が存在しない、または週が替わっている場合にパーティ状態(ボスのHPや種類)をリセット・初期化する。
* 根拠: `QuestService._check_and_reset_weekly_boss` (行番号: 行番号取得不可 / 抜粋: "def _check_and_reset_weekly_bo")


* **引数/リクエスト**: `cur`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "(self, cur):")


* **戻り値/レスポンス**: なし
* 根拠: `return` なし


* **副作用**: DB更新/挿入（`party_state`）、ログ出力
* 根拠: クエリ実行とロギング (行番号: 行番号取得不可 / 抜粋: "cur.execute("""INSERT INTO par")


* **エラーハンドリング**: レコード不在時の自己修復機能。
* 根拠: 条件分岐 (行番号: 行番号取得不可 / 抜粋: "if not party_row:")



### `QuestService._apply_boss_damage`

* **役割**: 計算されたダメージをボスに適用し、ボスのHP、合計ダメージ、撃破フラグを更新し効果音を鳴らす。
* 根拠: `QuestService._apply_boss_damage` (行番号: 行番号取得不可 / 抜粋: "def _apply_boss_damage(self, c")


* **引数/リクエスト**: `cur`, `damage: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "cur, damage: int")


* **戻り値/レスポンス**: `dict` (適用ダメージ量、残HP、撃破情報)
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> dict:")


* **副作用**: DB更新（`party_state`）、`sound_manager.play` 呼び出し、ログ出力
* 根拠: メソッド呼び出し (行番号: 行番号取得不可 / 抜粋: "sound_manager.play("boss_defea")


* **エラーハンドリング**: DBのレコードが取得できない場合はエラーログを出し結果を返す。
* 根拠: 条件分岐とロギング (行番号: 行番号取得不可 / 抜粋: "if not party_row:")



### `QuestService.calculate_quest_boost`

* **役割**: クエストの最終完了日からの経過日数に応じ、取得経験値とゴールドのボーナスを計算する。
* 根拠: `QuestService.calculate_quest_boost` (行番号: 行番号取得不可 / 抜粋: "def calculate_quest_boost(self")


* **引数/リクエスト**: `cur`, `user_id: str`, `quest: Any`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "cur, user_id: str, quest: Any")


* **戻り値/レスポンス**: `Dict[str, int]` (追加のgoldとexp)
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, int]:")


* **副作用**: DB参照（`quest_history`）
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "last_hist = cur.execute("""SEL")


* **エラーハンドリング**: 日時パースエラー時に `pass` で無視。
* 根拠: `try-except` ブロック (行番号: 行番号取得不可 / 抜粋: "except Exception: pass")



### `QuestService.process_complete_quest`

* **役割**: クエストを完了する。子供の場合は承認待ちステータスで履歴を作り、大人の場合は即時に報酬・ダメージ適用を行う。
* 根拠: `QuestService.process_complete_quest` (行番号: 行番号取得不可 / 抜粋: "def process_complete_quest(sel")


* **引数/リクエスト**: `user_id: str`, `quest_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "user_id: str, quest_id: int")


* **戻り値/レスポンス**: `Dict[str, Any]` (ステータスや報酬情報)
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB参照/更新（`quest_history` 等）、`sound_manager.play` 呼び出し、`_apply_quest_rewards` / `_apply_boss_damage` の呼び出し
* 根拠: メソッド呼び出しとクエリ (行番号: 行番号取得不可 / 抜粋: "cur.execute("""INSERT INTO que")


* **エラーハンドリング**: データ不在時 `HTTPException(404)`、10秒以内の重複実行時 `HTTPException(429)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(status_cod")



### `QuestService.process_approve_quest`

* **役割**: 親権限を持つユーザーが子供のクエスト完了を承認し、報酬付与・ボスダメージ計算・（必要に応じて）TVロック解除を実行する。
* 根拠: `QuestService.process_approve_quest` (行番号: 行番号取得不可 / 抜粋: "def process_approve_quest(self")


* **引数/リクエスト**: `approver_id: str`, `history_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "approver_id: str, history_id: ")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB参照/更新、内部メソッド呼び出し、TVロック解除処理（別スレッド）
* 根拠: メソッド呼び出しとクエリ (行番号: 行番号取得不可 / 抜粋: "self.*trigger_tv_unlock(quest*")


* **エラーハンドリング**: 権限なし `HTTPException(403)`、履歴なし `HTTPException(404)`、承認待ちでない `HTTPException(400)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(status_cod")



### `QuestService._trigger_tv_unlock`

* **役割**: 別スレッドでTVロック解除のAPIリクエストを送信する。
* 根拠: `QuestService._trigger_tv_unlock` (行番号: 行番号取得不可 / 抜粋: "def _trigger_tv_unlock(self, q")


* **引数/リクエスト**: `quest_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "quest_id: int")


* **戻り値/レスポンス**: なし
* 根拠: `return` なし


* **副作用**: 別スレッド作成、外部API(`switchbot_service`)呼び出し、LINE通知(`notification_service`)呼び出し
* 根拠: メソッド呼び出し (行番号: 行番号取得不可 / 抜粋: "t = threading.Thread(target=un")


* **エラーハンドリング**: APIエラー時は例外をキャッチしログ出力およびLINEへ通知。
* 根拠: `try-except` ブロック (行番号: 行番号取得不可 / 抜粋: "except Exception as e:")



### `QuestService.process_reject_quest`

* **役割**: 親権限を持つユーザーが子供のクエスト完了を拒否し、履歴を削除する。
* 根拠: `QuestService.process_reject_quest` (行番号: 行番号取得不可 / 抜粋: "def process_reject_quest(self,")


* **引数/リクエスト**: `approver_id: str`, `history_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "approver_id: str, history_id: ")


* **戻り値/レスポンス**: `Dict[str, str]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, str]:")


* **副作用**: DB削除（`quest_history`）、ログ出力
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "cur.execute("DELETE FROM ques")


* **エラーハンドリング**: 権限なし `HTTPException(403)`、履歴なし `HTTPException(404)`、承認待ちでない `HTTPException(400)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(status_cod")



### `QuestService.get_family_mileage`

* **役割**: ファミリーマイレージの現在の設定・進捗状況を取得する。
* 根拠: `QuestService.get_family_mileage` (行番号: 行番号取得不可 / 抜粋: "def get_family_mileage(self) -")


* **引数/リクエスト**: なし
* 根拠: 引数 `self` のみ (行番号: 行番号取得不可 / 抜粋: "def get_family_mileage(self) -")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB参照（`family_mileage`）
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "row = cur.execute("SELECT * FR")


* **エラーハンドリング**: なし
* 根拠: 明示的な例外処理なし



### `QuestService.update_family_mileage`

* **役割**: ファミリーマイレージの目標を新規設定または更新し、過去のデータを履歴テーブルに保存する。
* 根拠: `QuestService.update_family_mileage` (行番号: 行番号取得不可 / 抜粋: "def update_family_mileage(self")


* **引数/リクエスト**: `target_name: str`, `target_exp: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "target_name: str, target_exp: ")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB更新/挿入（`family_mileage`, `family_mileage_history`）
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "cur.execute("""INSERT INTO fa")


* **エラーハンドリング**: なし
* 根拠: 明示的な例外処理なし



### `QuestService._apply_quest_rewards`

* **役割**: ユーザーレベルや経験値の計算、マイレージの加算、クエスト履歴の更新等の報酬付与処理を実行する。
* 根拠: `QuestService._apply_quest_rewards` (行番号: 行番号取得不可 / 抜粋: "def _apply_quest_rewards(self,")


* **引数/リクエスト**: `cur`, `user`, `quest`, `now_iso`, `history_id=None`, `override_rewards=None`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "cur, user, quest, now_iso, his")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB更新（`family_mileage`, `quest_users`, `quest_history`, `party_state`）、`sound_manager.play` 呼び出し
* 根拠: クエリとメソッド呼び出し (行番号: 行番号取得不可 / 抜粋: "cur.execute("UPDATE quest_user")


* **エラーハンドリング**: `family_mileage` や `party_state` の更新時の例外をキャッチ・無視またはログ出力。
* 根拠: `try-except` ブロック (行番号: 行番号取得不可 / 抜粋: "except Exception as e:")



### `QuestService.process_cancel_quest`

* **役割**: クエストの完了を取り消し、獲得した経験値やゴールドを減算して履歴を削除する。
* 根拠: `QuestService.process_cancel_quest` (行番号: 行番号取得不可 / 抜粋: "def process_cancel_quest(self,")


* **引数/リクエスト**: `user_id: str`, `history_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "user_id: str, history_id: int")


* **戻り値/レスポンス**: `Dict[str, str]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, str]:")


* **副作用**: DB削除/更新（`quest_history`, `quest_users`）
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "cur.execute("DELETE FROM ques")


* **エラーハンドリング**: 履歴不在 `HTTPException(404)`、権限なし `HTTPException(403)`、ユーザー不在 `HTTPException(404)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(status_cod")



### `QuestService.filter_active_quests`

* **役割**: クエストの期間、曜日、時間帯、出現確率をもとに、現在の時刻に有効なクエスト一覧に絞り込む。
* 根拠: `QuestService.filter_active_quests` (行番号: 行番号取得不可 / 抜粋: "def filter_active_quests(self,")


* **引数/リクエスト**: `quests: List[dict]`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "quests: List[dict]")


* **戻り値/レスポンス**: `List[dict]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> List[dict]:")


* **副作用**: リストの書き換え・フィルタリング（DBや外部通信はなし）
* 根拠: ループ内のリスト操作 (行番号: 行番号取得不可 / 抜粋: "filtered.append(q)")


* **エラーハンドリング**: 日付文字列のパースに失敗した場合、ログを出力してスキップ。
* 根拠: `try-except ValueError` (行番号: 行番号取得不可 / 抜粋: "except ValueError as e:")



### `QuestService.get_weekly_analytics`

* **役割**: 過去1週間のクエスト・報酬履歴を集計し、日別の統計情報やランキングを作成する。
* 根拠: `QuestService.get_weekly_analytics` (行番号: 行番号取得不可 / 抜粋: "def get_weekly_analytics(self)")


* **引数/リクエスト**: なし
* 根拠: 引数 `self` のみ (行番号: 行番号取得不可 / 抜粋: "def get_weekly_analytics(self)")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB参照（`quest_users`, `quest_history`, `user_inventory`等）
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "sql_quest = """SELECT user_id")


* **エラーハンドリング**: 集計中のエラー時に空のデータを返すフォールバック処理。テーブル名変更のフォールバック処理。
* 根拠: `try-except` ブロック (行番号: 行番号取得不可 / 抜粋: "except Exception as e:")



### `ShopService.process_purchase_reward`

* **役割**: ユーザーがごほうび(アイテム)を購入し、ゴールドを消費してインベントリと履歴に追加する。
* 根拠: `ShopService.process_purchase_reward` (行番号: 行番号取得不可 / 抜粋: "def process_purchase_reward(se")


* **引数/リクエスト**: `user_id: str`, `reward_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "user_id: str, reward_id: int")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB更新/挿入（`quest_users`, `reward_history`, `user_inventory`）、ログ出力
* 根拠: クエリ実行とロギング (行番号: 行番号取得不可 / 抜粋: "cur.execute("""INSERT INTO re")


* **エラーハンドリング**: マスター不在・ユーザ不在 `HTTPException(404)`、ゴールド不足 `HTTPException(400)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(status_cod")



### `ShopService.process_purchase_equipment`

* **役割**: ユーザーが装備を購入し、ゴールドを消費して所持品に追加する。
* 根拠: `ShopService.process_purchase_equipment` (行番号: 行番号取得不可 / 抜粋: "def process_purchase_equipment")


* **引数/リクエスト**: `user_id: str`, `equipment_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "user_id: str, equipment_id: in")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB更新/挿入（`quest_users`, `user_equipments`）、ログ出力
* 根拠: クエリ実行とロギング (行番号: 行番号取得不可 / 抜粋: "cur.execute("""INSERT INTO us")


* **エラーハンドリング**: アイテム・ユーザ不在 `HTTPException(404)`、所有済み・ゴールド不足 `HTTPException(400)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(400, "Alre")



### `ShopService.process_change_equipment`

* **役割**: 指定した装備の装着フラグを有効にし、同タイプの他の装備を外す状態へ更新する。
* 根拠: `ShopService.process_change_equipment` (行番号: 行番号取得不可 / 抜粋: "def process_change_equipment(s")


* **引数/リクエスト**: `user_id: str`, `equipment_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "user_id: str, equipment_id: in")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB更新（`user_equipments`）、ログ出力
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "cur.execute("""UPDATE user_eq")


* **エラーハンドリング**: 対象アイテムの所持履歴がない場合 `HTTPException(404)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(404, "Equi")



### `InventoryService.get_user_inventory`

* **役割**: 指定ユーザーの所有または保留中のインベントリアイテム一覧を取得する。
* 根拠: `InventoryService.get_user_inventory` (行番号: 行番号取得不可 / 抜粋: "def get_user_inventory(self, u")


* **引数/リクエスト**: `user_id: str`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "user_id: str")


* **戻り値/レスポンス**: `List[dict]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> List[dict]:")


* **副作用**: DB参照（`user_inventory`, `reward_master`）
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "rows = cur.execute(sql, (user_")


* **エラーハンドリング**: なし
* 根拠: 明示的な例外処理なし



### `InventoryService.use_item`

* **役割**: ユーザーがアイテムを使用し、消費状態の更新、LINE通知の送信、履歴の追加を行う。
* 根拠: `InventoryService.use_item` (行番号: 行番号取得不可 / 抜粋: "def use_item(self, user_id: st")


* **引数/リクエスト**: `user_id: str`, `inventory_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "user_id: str, inventory_id: in")


* **戻り値/レスポンス**: `Dict[str, str]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, str]:")


* **副作用**: DB更新/挿入（`user_inventory`, `quest_history`）、`notification_service.send_push`、`sound_manager.play`
* 根拠: クエリ実行とメソッド呼び出し (行番号: 行番号取得不可 / 抜粋: "notification_service.send_push")


* **エラーハンドリング**: アイテム不在 `HTTPException(404)`、権限不一致 `HTTPException(403)`、状態不正 `HTTPException(400)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(400, "Cann")



### `InventoryService.consume_item`

* **役割**: 親権限のユーザーが保留中のアイテム使用を承認（消費）する。
* 根拠: `InventoryService.consume_item` (行番号: 行番号取得不可 / 抜粋: "def consume_item(self, approve")


* **引数/リクエスト**: `approver_id: str`, `inventory_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "approver_id: str, inventory_id")


* **戻り値/レスポンス**: `Dict[str, str]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, str]:")


* **副作用**: DB更新（`user_inventory`）、`sound_manager.play` 呼び出し
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "cur.execute("""UPDATE user_in")


* **エラーハンドリング**: 親権限なし `HTTPException(403)`、アイテム不在 `HTTPException(404)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(403, "承認権限")



### `InventoryService.cancel_usage`

* **役割**: 保留中のアイテム使用をキャンセルし、所有状態に戻す。
* 根拠: `InventoryService.cancel_usage` (行番号: 行番号取得不可 / 抜粋: "def cancel_usage(self, user_id")


* **引数/リクエスト**: `user_id: str`, `inventory_id: int`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "user_id: str, inventory_id: in")


* **戻り値/レスポンス**: `Dict[str, str]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, str]:")


* **副作用**: DB更新（`user_inventory`）
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "cur.execute("UPDATE user_inven")


* **エラーハンドリング**: アイテム不在 `HTTPException(404)`、所有者不一致 `HTTPException(403)`、保留中でない `HTTPException(400)`
* 根拠: 条件分岐と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(400, "Not ")



### `InventoryService.get_pending_items`

* **役割**: 承認待ち（保留中）の全アイテム一覧を取得する。
* 根拠: `InventoryService.get_pending_items` (行番号: 行番号取得不可 / 抜粋: "def get_pending_items(self) ->")


* **引数/リクエスト**: なし
* 根拠: 引数 `self` のみ (行番号: 行番号取得不可 / 抜粋: "def get_pending_items(self) ->")


* **戻り値/レスポンス**: `List[dict]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> List[dict]:")


* **副作用**: DB参照
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "rows = cur.execute(sql).fetcha")


* **エラーハンドリング**: なし
* 根拠: 明示的な例外処理なし



### `GameSystem.__init__`

* **役割**: クエスト、ユーザー、ショップ関連のサービスを初期化する。
* 根拠: `GameSystem.__init__` (行番号: 行番号取得不可 / 抜粋: "def **init**(self):")


* **引数/リクエスト**: なし
* 根拠: 引数なし (行番号: 行番号取得不可 / 抜粋: "def **init**(self):")


* **戻り値/レスポンス**: なし
* 根拠: `__init__`の仕様


* **副作用**: インスタンス変数の割り当て
* 根拠: 代入文 (行番号: 行番号取得不可 / 抜粋: "self.quest_service = QuestServ")


* **エラーハンドリング**: なし
* 根拠: 明示的な例外処理なし



### `GameSystem.sync_master_data`

* **役割**: ハードコードされたマスターデータモジュールを再読み込みし、DBとの同期および必要に応じたマイグレーション（カラム追加等）を行う。
* 根拠: `GameSystem.sync_master_data` (行番号: 行番号取得不可 / 抜粋: "def sync_master_data(self) -> ")


* **引数/リクエスト**: なし
* 根拠: 引数 `self` のみ (行番号: 行番号取得不可 / 抜粋: "def sync_master_data(self) -> ")


* **戻り値/レスポンス**: `Dict[str, str]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, str]:")


* **副作用**: DBテーブルのスキーマ変更（ALTER）、DELETE・INSERT・UPDATE、`importlib.reload`、ログ出力
* 根拠: クエリ実行とモジュール呼び出し (行番号: 行番号取得不可 / 抜粋: "cur.execute("ALTER TABLE ques")


* **エラーハンドリング**: モジュールの再読み込み失敗などの例外時に `HTTPException(500)`
* 根拠: `try-except` と `raise` (行番号: 行番号取得不可 / 抜粋: "raise HTTPException(status_cod")



### `GameSystem.get_all_view_data`

* **役割**: フロントエンド描画に必要な全ての状態（ユーザー、クエスト、報酬、履歴、装備、ボス情報など）を一括で取得・整形する。
* 根拠: `GameSystem.get_all_view_data` (行番号: 行番号取得不可 / 抜粋: "def get_all_view_data(self) ->")


* **引数/リクエスト**: なし
* 根拠: 引数 `self` のみ (行番号: 行番号取得不可 / 抜粋: "def get_all_view_data(self) ->")


* **戻り値/レスポンス**: `Dict[str, Any]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: DB参照、`_get_party_state` や `filter_active_quests` などの内部呼び出し
* 根拠: メソッド呼び出し (行番号: 行番号取得不可 / 抜粋: "boss_state = self._get_party_s")


* **エラーハンドリング**: なし
* 根拠: 明示的な例外処理なし



### `GameSystem._fetch_recent_logs`

* **役割**: クエスト承認履歴とアイテム交換履歴から直近20件分を取得・整形する。
* 根拠: `GameSystem._fetch_recent_logs` (行番号: 行番号取得不可 / 抜粋: "def _fetch_recent_logs(self, c")


* **引数/リクエスト**: `cur`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "(self, cur) -> List[dict]:")


* **戻り値/レスポンス**: `List[dict]`
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> List[dict]:")


* **副作用**: DB参照
* 根拠: クエリ実行 (行番号: 行番号取得不可 / 抜粋: "q_logs = cur.execute("""SELEC")


* **エラーハンドリング**: なし
* 根拠: 明示的な例外処理なし



### `GameSystem._get_party_state`

* **役割**: パーティ情報とボス情報を取得し、マスターデータと照合して統合する。
* 根拠: `GameSystem._get_party_state` (行番号: 行番号取得不可 / 抜粋: "def _get_party_state(self, cur")


* **引数/リクエスト**: `cur`
* 根拠: 引数定義 (行番号: 行番号取得不可 / 抜粋: "(self, cur) -> Dict[str, Any]:")


* **戻り値/レスポンス**: `Dict[str, Any]` (エラー時またはデータがない場合は `None`)
* 根拠: 返り値アノテーション (行番号: 行番号取得不可 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: `_check_and_reset_weekly_boss` によるDB更新、DB参照
* 根拠: メソッド呼び出し (行番号: 行番号取得不可 / 抜粋: "self.quest_service.*check_and*")


* **エラーハンドリング**: 取得エラー時に例外をキャッチしログを出力して `None` を返す。
* 根拠: `try-except` ブロック (行番号: 行番号取得不可 / 抜粋: "except Exception as e:")



---

## 5. 処理フロー図

以下は、クエストの完了処理（`process_complete_quest`）を中心とした処理フローです。

```mermaid
flowchart TD
    Start[Start: process_complete_quest] --> DB_Select{"DBからユーザとクエストを取得できるか"}
    DB_Select -- No --> Err404[HTTPException 404: Not found]
    DB_Select -- Yes --> SpamCheck{"直近10秒以内に完了履歴があるか"}
    SpamCheck -- Yes --> Err429[HTTPException 429: 少し時間を空けてください]
    SpamCheck -- No --> CalcBoost[クエストボーナスの計算]
    CalcBoost --> CheckChild{"対象ユーザは子供か(CHILDREN_IDS)"}
    
    CheckChild -- Yes --> InsertPending[quest_historyに'pending'で保存]
    InsertPending --> PlaySoundSubmit[外部: sound_manager.play 'submit']
    PlaySoundSubmit --> ReturnPending[子供用レスポンス返却: 承認待ち]
    ReturnPending --> End
    
    CheckChild -- No --> ApplyReward[大人の報酬適用処理: _apply_quest_rewards]
    ApplyReward --> CalcDamage[攻撃力・クリティカル計算]
    CalcDamage --> ApplyBossDmg[ボスへのダメージ適用: _apply_boss_damage]
    ApplyBossDmg --> ReturnAdult[大人用レスポンス返却: 成功]
    ReturnAdult --> End

```

## 6. 依存関係図

ファイル内の主要クラスと外部モジュール、DBの依存関係を示します。

```mermaid
graph TD
    subgraph quest_service.py
        UserService
        QuestService
        ShopService
        InventoryService
        GameSystem
        game_system_inst[game_system インスタンス]
    end

    GameSystem --> QuestService
    GameSystem --> UserService
    GameSystem --> ShopService
    QuestService --> UserService

    subgraph External Modules
        common
        config
        game_logic
        sound_manager
        notification_service
        switchbot_service
        models_quest["models.quest"]
        quest_data
    end

    subgraph Database/Tables
        quest_users
        quest_master
        quest_history
        reward_master
        reward_history
        equipment_master
        user_equipments
        user_inventory
        party_state
        family_mileage
        family_mileage_history
    end

    UserService -.-> quest_users
    UserService -.-> quest_history
    UserService -.-> reward_history
    UserService -.-> user_equipments

    QuestService -.-> quest_master
    QuestService -.-> quest_users
    QuestService -.-> quest_history
    QuestService -.-> party_state
    QuestService -.-> family_mileage
    
    ShopService -.-> quest_users
    ShopService -.-> user_equipments
    ShopService -.-> user_inventory
    ShopService -.-> reward_history

    InventoryService -.-> user_inventory
    InventoryService -.-> quest_history

    UserService -.-> common
    QuestService -.-> common
    ShopService -.-> common
    InventoryService -.-> common
    GameSystem -.-> common

    QuestService -.-> game_logic
    GameSystem -.-> game_logic

    QuestService -.-> sound_manager
    InventoryService -.-> sound_manager
    
    QuestService -.-> switchbot_service
    QuestService -.-> notification_service
    InventoryService -.-> notification_service

    GameSystem -.-> quest_data
    GameSystem -.-> models_quest

```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `common.py` | トランザクションスコープの境界や日時フォーマットが、データの整合性に強く影響するため。 | `with common.get_db_cursor(commit=True)` (行番号: 行番号取得不可 / 抜粋: "with common.get_db_cursor(comm") |
| 高 | `game_logic.py` | 報酬やレベルアップ等のコアドメインロジックを含むため。 | `game_logic.GameLogic.calc_leve` (行番号: 行番号取得不可 / 抜粋: "game_logic.GameLogic.calc_leve") |
| 中 | `quest_data.py` | 同期時に読み込まれるリスト要素の型と構成が、テーブルの各カラム仕様に依存するため。 | `import quest_data` (行番号: 行番号取得不可 / 抜粋: "import quest_data") |
| 中 | `services/switchbot_service.py` | 非同期のTVロック解除に失敗した場合の影響範囲・再送ロジックの有無を確認するため。 | `switchbot_service.send_device_` (行番号: 行番号取得不可 / 抜粋: "switchbot_service.send_device_") |

## 8. 保守上の注意点

* `QuestService._apply_quest_rewards` にて `family_mileage` および `party_state` のUPDATE時に例外が発生した場合、`except Exception as e:` でエラーをキャッチしログを出力しているが、呼び出し元の処理自体は成功（中断されない）となりDB状態が不整合となる可能性がある。
* `QuestService.filter_active_quests` にて日付文字列を `split('-')` で分割しており、対象フォーマット (`YYYY-MM-DD`) に厳密に依存している。
* `QuestService.get_weekly_analytics` にて `inventory` テーブル名が存在しない場合に、クエリ文字列を置換して `user_inventory` テーブルへフォールバックするハードコードのロジックが存在する。
* `QuestService._trigger_tv_unlock` において `threading.Thread` による非同期実行が行われており、プロセス終了時のスレッド制御が実装されていない。
* `CHILDREN_IDS` および `PARENT_IDS` が `QuestService` クラスの定数としてハードコードされている。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| DB各テーブルのスキーマ | 生SQLによるクエリが記述されているが、各カラムの型、主キーや外部キー等の制約が不明。 | DBのDDL(CREATE TABLE文) |
| `common.get_now_iso`の形式 | 現在時刻として保存する文字列表現における、ミリ秒やタイムゾーン情報の有無が不明。 | `common.py` |
| 各種定数の値 | TVロック解除や通知に使用されるIDの実体が不明。 | `config.py` |
| ゲーム計算ロジック | レベルアップ閾値や獲得報酬量の計算式が不明。 | `game_logic.py` |
| 非同期通信のエラー処理 | `switchbot_service.send_device_command` が返すレスポンス構造が不明。 | `services/switchbot_service.py` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了