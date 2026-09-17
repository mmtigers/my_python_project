## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `services/quest/approval_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

**（Issue #662で新規作成）** 本ファイルは、`services/quest/quest_service.py` の `QuestService` が717行・22メソッドまで育っていたため、「大人が子どもの完了報告を承認/却下する」「完了報告を取り消す」という別の関心を分離したものである。完了系（`QuestService`）との共有物は `services/quest/rewards.py` の `apply_quest_rewards` だけで、クラス同士は互いを参照しない。

## 関連ドキュメント

* [quest_quest_service.md](./quest_quest_service.md) - `services/quest/quest_service.py`。クエスト完了系の `QuestService`
* [quest_rewards.md](./quest_rewards.md) - `services/quest/rewards.py`。承認系と完了系が共有する報酬付与処理
* [quest_locks.md](./quest_locks.md) - `services/quest/locks.py`。`_acquire_user_balance_locks` 等のロック実装
* [quest_game_system.md](./quest_game_system.md) - `services/quest/game_system.py`。`approval_service` シングルトンの束縛元

## 2. ファイルの概要

`ApprovalService` クラス1つを定義する。承認(`process_approve_quest`)・却下(`process_reject_quest`)・取消(`process_cancel_quest`)はいずれも、`_get_lock_user_ids_for_history` で対象ユーザーと連結された相方（兄妹連携クエストの場合）を特定し、`_acquire_user_balance_locks` で複数ユーザー分のロックをまとめて取得したうえで `_process_*_locked` に委譲する薄いラッパー構造を共有する。このロックが、承認と却下が同時に走ったときの lost update（報酬が付与されたまま status だけ `rejected` になる不整合）を防いでいる。

## 3. 外部依存関係

`fastapi.HTTPException`、`core.database.get_db_cursor`、`core.utils.get_now_iso`、`config`、`game_logic`、`services.switchbot_service`、`services.quest.locks`（`ROLE_ADULT`/`ROLE_CHILD`/`_acquire_user_balance_locks`/`logger`）、`services.quest.rewards.apply_quest_rewards`。

## 4. 主要要素の定義

### `ApprovalService._get_lock_user_ids_for_history`

* **役割**: `history_id`に対応する`quest_history`行から、ユーザー単位ロックの対象とすべき`user_id`一覧を求める共通ヘルパー。連結履歴(`linked_history_id`)がある場合は相方の`user_id`も含める。`process_approve_quest`/`process_reject_quest`/`process_cancel_quest`がそれぞれ実装していたロジックを一元化したもの。`primary_user_id`を渡さない場合は`quest_history.user_id`を主対象として使い、対象履歴が存在しなければ`HTTPException(404)`を送出する（`process_approve_quest`/`process_reject_quest`の挙動）。`primary_user_id`を渡した場合はそれを主対象としてそのまま使い、対象履歴が存在しなくても404は送出しない（`process_cancel_quest`の挙動）。
* 根拠: `def _get_lock_user_ids_for_history(\n        self, history_id: int, primary_user_id: Optional[str] = None\n    ) -> List[str]:` (行番号: 28〜64)
* 根拠: `if primary_user_id is not None:\n            lock_user_ids = [primary_user_id]\n        else:\n            if not hist_peek:\n                raise HTTPException(status_code=404, detail="History not found")\n            lock_user_ids = [hist_peek['user_id']]` (行番号: 49〜54)
* 根拠: `if hist_peek and hist_peek['linked_history_id'] is not None:\n            with core.database.get_db_cursor() as cur:\n                linked_peek = cur.execute(\n                    "SELECT user_id FROM quest_history WHERE id = ?", (hist_peek['linked_history_id'],)\n                ).fetchone()\n            if linked_peek:\n                lock_user_ids.append(linked_peek['user_id'])` (行番号: 56〜62)
* **引数/リクエスト**: `history_id: int`, `primary_user_id: Optional[str] = None`
* 根拠: (行番号: 350〜352)
* **戻り値/レスポンス**: `List[str]`
* 根拠: (行番号: 352,386)
* **副作用**: DB参照（`quest_history`から`user_id`・`linked_history_id`をSELECT。連結履歴がある場合は相方の`user_id`もSELECT）
* 根拠: (行番号: 367〜369,380〜382)
* **エラーハンドリング**: `primary_user_id`未指定かつ対象履歴が見つからない場合のみ`HTTPException(404, "History not found")`を送出する。
* 根拠: (行番号: 374〜375)

### `ApprovalService.process_approve_quest`

* **役割**: `_get_lock_user_ids_for_history`でロック対象ユーザー（`quest_history`の本来の完了者）と連結された相方（存在する場合）を特定し、`_acquire_user_balance_locks`でそれら全員分のユーザー単位ロックをまとめて取得したうえで、実処理を`_process_approve_quest_locked`に委譲する薄いラッパー。連結履歴がある場合に相方のIDも合わせてロックするのは、`_process_approve_quest_locked`が`_approve_linked_history`経由で相方の`quest_users`もカスケード更新するためである。
* 根拠: `def process_approve_quest(self, approver_id: str, history_id: int) -> Dict[str, Any]:` (行番号: 66〜73)
* 根拠: `lock_user_ids = self._get_lock_user_ids_for_history(history_id)\n        with _acquire_user_balance_locks(lock_user_ids):\n            return self._process_approve_quest_locked(approver_id, history_id)` (行番号: 71〜73)
* **引数/リクエスト**: `approver_id: str`, `history_id: int`
* 根拠: (行番号: 388)
* **戻り値/レスポンス**: `Dict[str, Any]`（`_process_approve_quest_locked`の戻り値をそのまま返却）
* 根拠: (行番号: 395)
* **副作用**: `_get_lock_user_ids_for_history`経由の軽量な参照クエリ、複数ユーザー分のロックの取得・解放
* 根拠: (行番号: 393〜394)
* **エラーハンドリング**: 参照クエリで該当履歴が見つからない場合`HTTPException(404)`（`_get_lock_user_ids_for_history`が送出）
* 根拠: (行番号: 393)

### `ApprovalService._process_approve_quest_locked`

* **役割**: `ROLE_ADULT`のユーザーが子供のクエスト完了を承認する実処理。TV解錠(SwitchBot API経由の副作用)は`with core.database.get_db_cursor(commit=True)`ブロックの**外**（コミット完了後）で行われる。ブロック内では該当時に`tv_unlock_quest_id`へ`quest['quest_id']`を記録するだけにし、ブロックを抜けた後`tv_unlock_quest_id is not None`なら`switchbot_service.trigger_tv_unlock`を呼ぶ（**毎朝ミッション統合で変更**: 以前は本クラスのプライベートメソッド`_trigger_tv_unlock`を`self._trigger_tv_unlock(tv_unlock_quest_id)`として呼んでいたが、`services/routine_service.py`側の朝の準備チェックリスト完了処理とも共有するため、`switchbot_service`モジュールの関数として切り出された。呼び出し時に渡す文字列はログ識別用の`context`引数（例: `f"quest_id={tv_unlock_quest_id}"`）に変わっている）。`override_rewards`の`gold`/`exp`は`hist['gold_earned'] or 0`/`hist['exp_earned'] or 0`として`NULL`を0扱いにする。TVロック判定の`quest`は`sync_master_data`のマスタ削除後も`quest_history`の`pending`行が残るケースで`None`になり得るため`if quest and ...`でガードされている。`_approve_linked_history`が返す相方の報酬情報を`result`辞書へ`partnerUserId`/`partnerLeveledUp`/`partnerNewLevel`/`partnerEarnedMedals`として格納する。
* 根拠: `def _process_approve_quest_locked(self, approver_id: str, history_id: int) -> Dict[str, Any]:` (行番号: 75〜133)
* 根拠: `tv_unlock_quest_id: Optional[int] = None\n        with core.database.get_db_cursor(commit=True) as cur:` (行番号: 80〜81)、`if tv_unlock_quest_id is not None:\n            switchbot_service.trigger_tv_unlock(f"quest_id={tv_unlock_quest_id}")\n        return result` (行番号: 131〜133)
* 根拠: `override_rewards = {\n                "gold": hist['gold_earned'] or 0,\n                "exp": hist['exp_earned'] or 0\n            }` (行番号: 101〜104)
* 根拠: `if quest and quest['quest_id'] in config.TV_UNLOCK_QUEST_IDS and config.TV_PLUG_DEVICE_ID:\n                if user['role'] == ROLE_CHILD:\n                    tv_unlock_quest_id = quest['quest_id']` (行番号: 125〜127)
* 根拠: `if hist['linked_history_id'] is not None:\n                partner_result = self._approve_linked_history(cur, hist['linked_history_id'])\n                if partner_result:\n                    result['partnerUserId'] = partner_result['user_id']\n                    result['partnerLeveledUp'] = partner_result['leveledUp']\n                    result['partnerNewLevel'] = partner_result['newLevel']\n                    result['partnerEarnedMedals'] = partner_result['earnedMedals']` (行番号: 114〜120)
* **引数/リクエスト**: `approver_id: str`, `history_id: int`
* 根拠: (行番号: 397)
* **戻り値/レスポンス**: `Dict[str, Any]`（連結された相方履歴が無い場合は`partnerUserId`等のキー自体が含まれない）
* 根拠: (行番号: 428,455)
* **副作用**: DB参照/更新、`_approve_linked_history`の呼び出し（トランザクション内）、`switchbot_service.trigger_tv_unlock`の呼び出し（トランザクションのコミット後）、ログ出力
* 根拠: (行番号: 428,436〜442,447〜449,451,453〜454)
* **エラーハンドリング**: 承認者が`role_adult`でない場合`HTTPException(403)`、履歴なし`HTTPException(404)`、承認待ちでない場合`HTTPException(400)`、履歴のユーザーがマスタから消えている場合`HTTPException(404, "User of this history not found")`
* 根拠: (行番号: 404〜406,408〜412,415〜418)

### `ApprovalService._approve_linked_history`

* **役割**: 兄妹連携クエストで連結された相方側の`quest_history`行を承認済みに確定する。対象行が存在しない、または既に`pending`でない、または対象ユーザーが存在しない場合は何もしない冪等な実装。相方の`user_id`と`_apply_quest_rewards`の戻り値をひとつの辞書にまとめて呼び出し元へ返す。`override_rewards`の`gold`/`exp`は`linked_hist['gold_earned'] or 0`/`linked_hist['exp_earned'] or 0`として`NULL`を0扱いにする。
* 根拠: `def _approve_linked_history(self, cur, linked_history_id: int) -> Optional[Dict[str, Any]]:` (行番号: 135〜155)
* 根拠: `if not linked_hist or linked_hist['status'] != 'pending':\n            return None` (行番号: 144〜145)、`if not linked_user:\n            return None` (行番号: 149〜150)
* 根拠: `override_rewards = {"gold": linked_hist['gold_earned'] or 0, "exp": linked_hist['exp_earned'] or 0}` (行番号: 152)
* **引数/リクエスト**: `cur`, `linked_history_id: int`
* 根拠: (行番号: 457)
* **戻り値/レスポンス**: `Optional[Dict[str, Any]]`。正常時は`{"user_id": ..., "status": "success", "leveledUp": ..., "newLevel": ..., "earnedGold": ..., "earnedExp": ..., "earnedMedals": ...}`
* 根拠: (行番号: 475,477)
* **副作用**: DB参照/更新（`_apply_quest_rewards`経由）、ログ出力
* 根拠: (行番号: 475〜476)
* **エラーハンドリング**: 対象履歴が存在しない・`pending`でない、または対象ユーザーが存在しない場合は早期`return None`（例外を送出しない）
* 根拠: (行番号: 466〜467,471〜472)

### `ApprovalService._trigger_tv_unlock`（廃止・毎朝ミッション統合で削除）

* **役割**: 本メソッドは廃止された。以前は別スレッドでTVプラグのON操作をSwitchBot API経由でリクエストする非同期処理（メインスレッド＝APIルーティングをブロックしない）を本クラスのプライベートメソッドとして実装していたが、`services/routine_service.py`（朝の準備チェックリスト全項目達成時）でも同一の処理が必要になったため、`services/switchbot_service.py`の`trigger_tv_unlock(context: str)`という共有関数へロジックがそのまま切り出され、本メソッド自体は削除された。呼び出し元（`_process_approve_quest_locked`）は`self._trigger_tv_unlock(tv_unlock_quest_id)`の代わりに`switchbot_service.trigger_tv_unlock(f"quest_id={tv_unlock_quest_id}")`を呼ぶよう変更されている。実装の詳細（スレッド生成、Fail-Soft通知、エラーハンドリング）は[switchbot_service.md](./switchbot_service.md)の`trigger_tv_unlock`セクションを参照。
* 根拠: 本ファイルの`git diff`で確認（`services/quest/quest_service.py`から`_trigger_tv_unlock`メソッド定義24行が削除され、呼び出し元1行が`switchbot_service.trigger_tv_unlock(...)`へ置換された）、切り出し先 `def trigger_tv_unlock(context: str) -> None:` (`services/switchbot_service.py`側、行番号: 94〜125)

### `ApprovalService.process_reject_quest`

* **役割**: `_get_lock_user_ids_for_history`で対象ユーザーと連結された相方（存在する場合）を特定し、`_acquire_user_balance_locks`でそれら全員分のユーザー単位ロックをまとめて取得したうえで、実処理を`_process_reject_quest_locked`に委譲する薄いラッパー。`process_approve_quest`/`process_cancel_quest`と同じロックに参加させることで、承認と却下がほぼ同時に実行された場合の不整合を防ぐ。
* 根拠: `def process_reject_quest(self, approver_id: str, history_id: int, reason: Optional[str] = None) -> Dict[str, str]:` (行番号: 157〜168)
* 根拠: `lock_user_ids = self._get_lock_user_ids_for_history(history_id)\n        with _acquire_user_balance_locks(lock_user_ids):\n            return self._process_reject_quest_locked(approver_id, history_id, reason)` (行番号: 166〜168)
* **引数/リクエスト**: `approver_id: str`, `history_id: int`, `reason: Optional[str] = None`
* 根拠: (行番号: 479)
* **戻り値/レスポンス**: `Dict[str, str]`（`_process_reject_quest_locked`の戻り値をそのまま返却）
* 根拠: (行番号: 490)
* **副作用**: `_get_lock_user_ids_for_history`経由の軽量な参照クエリ、複数ユーザー分のロックの取得・解放
* 根拠: (行番号: 488〜489)
* **エラーハンドリング**: 参照クエリで該当履歴が見つからない場合`HTTPException(404)`
* 根拠: (行番号: 193)

### `ApprovalService._process_reject_quest_locked`

* **役割**: `ROLE_ADULT`のユーザーが子供のクエスト完了を却下する実処理。`quest_history`該当行の`status`列を`'rejected'`へ`UPDATE`することで却下履歴を残す（`DELETE`はしない）。主対象・連結相方いずれのUPDATEも`AND status = 'pending'`を条件に含める。連結された相方の履歴が`pending`であれば、同一トランザクション内で相方側の`status`も同様に`'rejected'`へカスケード更新する。
* 根拠: `def _process_reject_quest_locked(self, approver_id: str, history_id: int, reason: Optional[str] = None) -> Dict[str, str]:` (行番号: 170〜198)
* 根拠: `cur.execute("UPDATE quest_history SET status = 'rejected' WHERE id = ? AND status = 'pending'", (history_id,))` (行番号: 190)
* 根拠: `if hist['linked_history_id'] is not None:\n                cur.execute("UPDATE quest_history SET status = 'rejected' WHERE id = ? AND status = 'pending'", (hist['linked_history_id'],))\n                logger.info(f"Coop Partner Rejected: HistoryID={hist['linked_history_id']}")` (行番号: 193〜195)
* **引数/リクエスト**: `approver_id: str`, `history_id: int`, `reason: Optional[str] = None`
* 根拠: (行番号: 492)
* **戻り値/レスポンス**: `Dict[str, str]`（`{"status": "rejected"}`）
* 根拠: (行番号: 520)
* **副作用**: DB更新（`quest_history.status`を`'rejected'`へUPDATE。連結された相方の`pending`行も含む）、ログ出力
* 根拠: (行番号: 512,515〜517,519)
* **エラーハンドリング**: 承認者が`role_adult`でない場合`HTTPException(403)`、履歴なし`HTTPException(404)`、承認待ちでない場合`HTTPException(400)`
* 根拠: (行番号: 494〜496,498〜500,501〜502)

### `ApprovalService.process_cancel_quest`

* **役割**: `_get_lock_user_ids_for_history`（`primary_user_id=user_id`を渡す）で対象ユーザーと連結された相方（存在する場合）を特定し、`_acquire_user_balance_locks`でそれら全員分のユーザー単位ロックをまとめて取得したうえで、実処理を`_process_cancel_quest_locked`に委譲する薄いラッパー。
* 根拠: `def process_cancel_quest(self, user_id: str, history_id: int) -> Dict[str, str]:` (行番号: 212〜219)
* 根拠: `lock_user_ids = self._get_lock_user_ids_for_history(history_id, primary_user_id=user_id)\n        with _acquire_user_balance_locks(lock_user_ids):\n            return self._process_cancel_quest_locked(user_id, history_id)` (行番号: 217〜219)
* **引数/リクエスト**: `user_id: str`, `history_id: int`
* 根拠: (行番号: 576)
* **戻り値/レスポンス**: `Dict[str, str]`（`_process_cancel_quest_locked`の戻り値をそのまま返却）
* 根拠: (行番号: 583)
* **副作用**: `_get_lock_user_ids_for_history`経由の軽量な参照クエリ、複数ユーザー分のロックの取得・解放
* 根拠: (行番号: 581〜582)
* **エラーハンドリング**: なし（内部の例外はそのまま伝播。`history_id`が不正な場合や`user_id`不一致は`_process_cancel_quest_locked`側で検出される）
* 根拠: (行番号: 576〜583)

### `ApprovalService._process_cancel_quest_locked`

* **役割**: 対象履歴が本人のものであることを確認したうえで、`_revert_and_delete_history`で報酬をロールバックしつつ`quest_history`行を削除する。連結された相方の履歴があれば、同一トランザクション内で相方側も`_revert_and_delete_history`でカスケード取り消しする。
* 根拠: `def _process_cancel_quest_locked(self, user_id: str, history_id: int) -> Dict[str, str]:` (行番号: 221〜246)
* 根拠: `if hist['user_id'] != user_id:\n                raise HTTPException(status_code=403, detail="User mismatch")` (行番号: 226〜227)
* 根拠: `linked_id = hist['linked_history_id']\n            if linked_id is not None:\n                linked_hist = cur.execute(...).fetchone()\n                if linked_hist:\n                    linked_user = cur.execute(...).fetchone()\n                    if linked_user:\n                        self._revert_and_delete_history(cur, linked_hist, linked_user)` (行番号: 236〜242)
* **引数/リクエスト**: `user_id: str`, `history_id: int`
* 根拠: (行番号: 585)
* **戻り値/レスポンス**: `Dict[str, str]`（`{"status": "cancelled"}`）
* 根拠: (行番号: 610)
* **副作用**: DB参照/更新/削除（`_revert_and_delete_history`経由）、ログ出力
* 根拠: (行番号: 597,606〜609)
* **エラーハンドリング**: 履歴なし`HTTPException(404)`、`user_id`不一致`HTTPException(403)`、ユーザー不在`HTTPException(404)`
* 根拠: (行番号: 588〜589,590〜591,593〜595)

### `ApprovalService._revert_and_delete_history`

* **役割**: `quest_history`1行を取り消す。`approved`であれば付与済みの経験値・ゴールドをロールバックしてから削除する。`pending`/`rejected`は報酬がまだ付与されていないため、残高には触れず単純に削除する。付与済みゴールドを既に消費している(現在の残高 < 付与額)場合は取り消し自体を拒否し、キャンセルが常に「付与の完全な巻き戻し」になることを保証する。
* 根拠: `def _revert_and_delete_history(self, cur, hist, user) -> None:` (行番号: 248〜282)
* 根拠: `if hist['status'] != 'approved':\n            cur.execute("DELETE FROM quest_history WHERE id = ?", (hist['id'],))\n            return` (行番号: 256〜258)
* 根拠: `gold_earned = hist['gold_earned'] or 0\n        current_gold = user['gold'] or 0\n        if current_gold < gold_earned:\n            raise HTTPException(\n                status_code=400,\n                detail="獲得したゴールドを既に使用しているため、このクエストは取り消せません",\n            )` (行番号: 265〜271)
* **引数/リクエスト**: `cur`, `hist`, `user`
* 根拠: (行番号: 612)
* **戻り値/レスポンス**: なし（`-> None`）
* 根拠: (行番号: 612)
* **副作用**: DB更新/削除（`quest_users`のlevel/exp/gold/medal_countをUPDATE、`quest_history`行をDELETE）
* 根拠: (行番号: 644〜646)
* **エラーハンドリング**: 付与済みゴールドを既に消費している場合`HTTPException(400)`
* 根拠: (行番号: 631〜635)

## 5. 保守上の注意点

* **`_apply_quest_rewards` は薄いラッパーとして残してある。** 実体は `services/quest/rewards.py` の `apply_quest_rewards` だが、テストが `monkeypatch.setattr(service, "_apply_quest_rewards", ...)` で遅延や失敗を差し込む seam をこのメソッドが提供している（`tests/test_quest_service_edge_cases.py` の承認/却下の競合テストがこの経路に依存する）。実体を直接呼ぶように変えると、そのテストは**パッチが空振りしたまま通ってしまう**ので注意すること。
* ロックの取得順序は `services/quest/locks.py` の規約に従う。残高ロックは `routine_service` とも共有しているため、ここでの取得順序を変えるとデッドロックを招きうる。
