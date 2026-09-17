## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `services/quest/rewards.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

**（Issue #662で新規作成）** `QuestService._apply_quest_rewards` を素の関数として切り出したもの。承認系（`ApprovalService`）と完了系（`QuestService`）の両方から呼ばれるため、どちらか一方のクラスに置くと他方がそのクラスを参照することになる。元のメソッドは `self` を一切使っていなかったため、そのまま関数化できた。

## 関連ドキュメント

* [quest_approval_service.md](./quest_approval_service.md) - `services/quest/approval_service.py`。承認・却下・取消
* [quest_quest_service.md](./quest_quest_service.md) - `services/quest/quest_service.py`。クエスト完了系

## 2. ファイルの概要

`apply_quest_rewards` 関数1つを定義する。

## 3. 外部依存関係

`game_logic`（`GameLogic.calculate_drop_rewards`/`calc_level_progress`）、`core.sound_manager`。

## 4. 主要要素の定義

### `apply_quest_rewards`

* **役割**: `game_logic.GameLogic.calculate_drop_rewards`でゴールド・経験値・メダル・ラッキー判定を計算し、`calc_level_progress`でレベル・経験値・レベルアップ有無を求め、`quest_users`を更新する。`history_id`が指定されていれば既存の`quest_history`行を`'approved'`に更新（`completed_at`は書き換えない）、なければ新規挿入する。レベルアップ・ラッキー(メダル獲得)・通常クリア(新規挿入時のみ)に応じて対応するサウンドを再生する。
* 根拠: `def apply_quest_rewards(cur, user, quest, now_iso, history_id=None, override_rewards=None) -> Dict[str, Any]:` (行番号: 13〜71)
* 根拠: `if history_id:\n            cur.execute("UPDATE quest_history SET status='approved', gold_earned=?, exp_earned=?, medals_earned=? WHERE id=?", ...)\n        else:\n            cur.execute("""\n                INSERT INTO quest_history (...)\n                VALUES (?, ?, ?, ?, ?, ?, ?, 'approved')\n            """, ...)` (行番号: 47〜58)
* 根拠: `if leveled_up:\n            sound_manager.play("level_up")\n        elif is_lucky:\n            sound_manager.play("medal_get")\n        elif not history_id:\n            sound_manager.play("quest_clear")` (行番号: 60〜65)
* **引数/リクエスト**: `cur`, `user`, `quest`, `now_iso`, `history_id=None`, `override_rewards=None`
* 根拠: (行番号: 13)
* **戻り値/レスポンス**: `Dict[str, Any]`（`status`, `leveledUp`, `newLevel`, `earnedGold`, `earnedExp`, `earnedMedals`）
* 根拠: (行番号: 570〜574)
* **副作用**: DB更新（`quest_users`, `quest_history`）、`sound_manager.play`呼び出し
* 根拠: (行番号: 542〜546,550〜561,563〜568)
* **エラーハンドリング**: なし
* 根拠: (行番号: 13〜71)

## 5. 保守上の注意点

* 呼び出し元（`ApprovalService` / `QuestService`）は、いずれも `_apply_quest_rewards` という薄いラッパーメソッド経由でこの関数を呼ぶ。これはテストが差し込む seam を維持するためで、ラッパーを外すと既存の並行性テストが無力化する。
