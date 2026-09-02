# 全体コードレビュー報告 (2026-08-22)

対象: `MY_HOME_SYSTEM` (Python/FastAPI バックエンド) / `family-quest` (React/TS フロントエンド) / `DDD` (バッチ群) / `scripts` / `.github` (CI)

手法: リスクベースの段階的レビュー(Map → Prioritize → Trace → Deep Review)。外部入力境界・状態変更・subprocess/Shell 実行・データフローを優先的に深掘りし、`docs/specifications/` の仕様書は問題箇所に対応する部分のみ参照した。`old/` 配下のレガシーコードはレビュー対象外。一部の指摘(H-1 の RuntimeError、M-1b の FK 違反)はローカルでの最小再現コードにより動作確認済み。

---

## 0. 対応状況（2026-09-02追記・Issue #323）

> **状態管理の方針**: 残件の状態管理の正は**GitHub Issue**に一本化する。本レポート（および他のレビューレポート）は「レビュー時点の歴史的記録+未解決項目のIssueへのポインタ」であり、以下の表はIssue #323時点のスナップショット。以後の最新状態は各Issueを参照すること。

High 12件+M-10-1のコード照合結果（2026-09-02、Issue #323の調査に基づく）:

| # | 項目 | 状態 | 備考 |
|---|---|---|---|
| H-1 | `get_db_cursor`のリトライ機構破綻 | ✅ 対応済み | `core/database.py`にロック時リトライ実装済み |
| H-2 | 新規/再構築DBの承認フロー無効化・新規構築パス崩壊 | ✅ 対応済み | `migrations/0001`(roleカラム+`role_adult`補正)等で解消 |
| H-3 | 承認・取消・アイテム経路のread-modify-writeレース | ✅ 対応済み | `services/quest_service.py`に`_completion_locks`/`_user_balance_locks`によるキー別排他を実装済み |
| H-4 | SwitchBot Webhookの実ペイロード形式不一致の疑い | 🔲 **未解決** | 実機ログでの確認が必要 → **Issue #328**（`blocked:実機作業`） |
| H-5 | アイテム「承認待ち」フローの断絶 | ✅ 対応済み | — |
| H-6 | AIツールのSQL許可テーブル検査のカンマ結合回避 | ✅ 対応済み | — |
| H-7 | DB保存失敗の握りつぶし・虚偽の成功応答 | ✅ 対応済み | — |
| H-8 | 兄妹連携クエストのフロント未表示 | ✅ 対応済み | 兄妹連携(coop)クエストとして実装済み |
| H-9 | `start_all.sh`のpkill対象名不一致によるscheduler二重起動 | ✅ 対応済み | — |
| H-10 | 認証境界が事実上存在しない | 🔲 **未解決** | JWT検証再実装 or エッジ委譲の正式設計化を判断中 → **Issue #321**（`decision-needed`） |
| H-11 | CIがMY_HOME_SYSTEMのみ対象・フロントはテスト0件 | ✅ 対応済み | `test.yml`にfrontendジョブ(`npm ci`+`build`+`npm test`)・DDD lint/pytest追加済み。vitestテストも存在 |
| H-12 | DDD `extract_youtube_urls.py`のCWD依存フォールバック | ✅ 対応済み | — |
| M-10-1 | `test.yml`に`permissions:`ブロックが無い | ✅ 対応済み | `permissions: contents: read`を明示済み（`test.yml`冒頭） |

- **「5. 品質向上ロードマップ」のPhase 3（スキーマ管理一本化・設定一本化・NASブロッキング分離等）は未着手** → **Issue #330**（`priority:medium`）で追跡。
- Medium各グループ（M-1〜M-10）の個別項目は多くが個別修正済みだが、本表では網羅照合していない。未解決と判明したものは都度Issue化する方針（上記のとおりIssueが正）。

---

## 1. エグゼクティブサマリー

**総合評価: C(要改善)**

| 重要度 | 件数 |
| --- | --- |
| Critical | 0 |
| High | 12 |
| Medium | 10グループ(個別 約35件) |
| Low | 約15件(価値のあるもののみ記載) |

- **最も危険な問題**: H-2「新規/再構築 DB では子供の承認フローが無効化される」。現在の本番 DB は歴史的パッチの積み重ねで動いているが、DB を再構築・リストアした瞬間に `role` が全ユーザー NULL となり、子供のクエストが承認なしで即時報酬付与になる。`init_unified_db.py` の `daily_logs` 定義破損も同じ根で、新規構築パス全体が現行コードと乖離している。
- **次点**: H-1「`get_db_cursor` のリトライ機構は実際には機能せず、DB ロック時に RuntimeError を送出する」(実測確認済み)。H-7「DB 保存失敗時にユーザーへ『✅ 記録しました』と虚偽応答」と合わせて、データ欠損が無通知で起きる構造がある。
- **最も効果が大きい改善**: (1) スキーマ管理の一本化(`init_unified_db.py`・`sync_master_data` 内の実行時 ALTER・`migrations/` の三重管理を `migrations/` に統合)、(2) `get_db_cursor` の書き直し、(3) CI カバレッジの拡大(フロントエンドはテスト0件・ビルド検証すらCIに無い)。
- **未確認・仕様不明の重要領域**(「問題なし」ではない):
  - SwitchBot Webhook の実ペイロード形式(H-4 は実機ログでの確認が必要)
  - `old/` 配下全体、`views/dashboard/` 各タブの詳細、`weekly_analyze_report.py` 内部、`tools/financial_service.py`・`google_photos_service.py` の詳細
  - テストスイートの実行(静的レビュー+2件の最小再現のみ。pytest は未実行)
  - アイテム「承認待ち」フローの正仕様(即時消費が正か、承認フローが正か — H-5)

評価根拠: Critical はないが、「今は動いているが再構築・並行操作・マスタ変更で壊れる」High が複数あり、CI が検知できない領域(フロントエンド・DDD・カメラサービス本体)が広い。

---

## 2. Critical / High

### [High] H-1. `get_db_cursor` のリトライ機構が設計として破綻しており、DB ロック時に RuntimeError を送出する

- 確度: **確実に問題(ローカル再現で実証済み)**
- カテゴリ: バグ / 信頼性
- 対象: 全 DB 書き込み・読み取り経路(クエスト API、Webhook ログ保存、LINE 記録)
- 対象ファイル: `MY_HOME_SYSTEM/core/database.py`
- 対象箇所: `get_db_cursor` (12〜50行)
- 証拠: `@contextmanager` のジェネレータ内でリトライループが `yield` を囲んでいる。再現実行の結果:
  - `with` 本文内で locked エラー発生 → except が捕捉して再ループ → 2回目の `yield` → **`RuntimeError: generator didn't stop after throw()`**
  - 接続時 locked が5回連続 → ループ終了後 `yield` されないまま終了 → **`RuntimeError: generator didn't yield`**
- 問題: 「リトライ機能付き」と称するが、実際にリトライが成立するのは接続/PRAGMA 実行時の locked のみ。本文実行中の locked(実運用で最も起きるケース)は RuntimeError に化け、リトライも rollback 通知も行われない。`save_log_generic` (69行) の `if cur:` は「カーソルが None で返る」ことを想定しているが、その経路は存在しない。
- 影響: WAL + timeout 30s のため頻度は低いが、複数プロセス(unified_server / scheduler / monitors)が同一 DB に書く構成で競合した瞬間、API は意味不明な 500 を返し、ログ保存系は例外でジョブごと落ちる。障害調査時にも原因が「DB ロック」と分からない。
- 推奨修正(最小): ジェネレータを「接続確立のみリトライ → yield は1回だけ → 本文例外は rollback して re-raise」の素直な構造に書き直す。リトライで包みたい呼び出し側はデコレータ等の外側で行う。
- 修正規模: 小

### [High] H-2. 新規/再構築 DB では承認フローが無効化され、Webhook ログ保存も失敗する(新規構築パスの崩壊)

- 確度: 確実に問題(静的検証。3点の証拠が連鎖)
- カテゴリ: バグ / 仕様 / データ整合性
- 対象: DB 初期構築 → マイグレーション → マスタ同期 → クエスト完了、の全経路
- 対象ファイル: `MY_HOME_SYSTEM/init_unified_db.py`, `MY_HOME_SYSTEM/migrations/0001_add_quest_users_role.sql`, `MY_HOME_SYSTEM/quest_data.py`, `MY_HOME_SYSTEM/services/quest_service.py`
- 証拠:
  1. **role が誰にも設定されない**: `init_unified_db.py:376-387` は `quest_users` を role カラム無しで作成 → 起動時に `0001_add_quest_users_role.sql` が role を追加し既存行へ UPDATE するが、**この時点でユーザー行は0件**。その後 `sync_master_data` が `quest_data.py` の `USERS` を挿入するが、`USERS` に `role` キーは存在せず(grep で0件)、`role_val=None` のまま挿入・`COALESCE(excluded.role, quest_users.role)` も NULL(quest_service.py:726-735)。role を実際に設定するコードは新規構築経路のどこにも存在しない。
  2. **その結果**: `_process_complete_quest_locked` の `user['role'] == ROLE_CHILD` (250行) が全員 False → **子供も大人扱いで即時報酬付与(承認スキップ)**。逆に `process_approve_quest` は `role != ROLE_ADULT` で全員 403。
  3. **daily_logs 定義の破損**: `init_unified_db.py:121-135` の `daily_logs` CREATE 文には `category`/`detail`/`timestamp` が無く、代わりに `day_of_week`/`target_user`/`occurrence_chance` 等 **quest_master 用のカラム群が混入**している(`current_schema.sql:305-311` の実テーブルと完全に乖離)。新規 DB では `webhook_router.py:86-89` の `INSERT INTO daily_logs (category, detail, timestamp)` が必ず失敗する。
- 問題: スキーマの出所が「`init_unified_db.py`」「`sync_master_data` 内の実行時 ALTER」「`migrations/*.sql`」の3系統に分裂しており、相互に同期していない。現本番 DB は歴史的な手パッチで整合しているだけ。
- 影響: DB 破損・機器交換・引っ越し等での再構築時、エラーなく起動して**静かに**承認フローが消える(子供が無制限に報酬を得られる)。ログ保存はエラーになるが `save_log_generic` が握りつぶすため気づけない。
- 再現条件: 空ディレクトリで `init_unified_db.py` → サーバー起動 → `/api/quest/sync_master` → 子供ユーザーで `/api/quest/complete`。
- 推奨修正(最小): `quest_data.py` の `USERS` に `role` を明記し、`sync_master_data` で必ず反映する。`init_unified_db.py` の `daily_logs` 定義を実スキーマに修正。(理想: `init_unified_db.py` を廃止し、空 DB からの構築を `migrations/` のみで完結させ、テストに「空 DB → 全マイグレーション → 承認フロー E2E」を追加)
- 修正規模: 中

### [High] H-3. 承認・取消・アイテム経路が read-modify-write のままで、更新消失レースがある

- 確度: 確実に問題(コード形状として。発生には同時操作が必要)
- カテゴリ: バグ / データ整合性
- 対象: `/api/quest/approve`, `/api/quest/quest/cancel`, `/api/quest/inventory/*`
- 対象ファイル: `MY_HOME_SYSTEM/services/quest_service.py`
- 対象箇所: `process_approve_quest` (316〜348行), `_apply_quest_rewards` (426〜436行), `process_cancel_quest`/`_revert_and_delete_history` (460〜500行)
- 証拠: 完了経路には二重加算対策の `_completion_locks` (45〜55行、コメントでレース発生機序を自認)と、購入経路にはアトミック UPDATE (558〜563行、同じくコメントで自認)が導入済み。しかし承認経路は `user` を SELECT (326行) → Python で `final_gold = user['gold'] + earned_gold` を計算 → UPDATE (432〜436行) という同じ形のまま。FastAPI の sync エンドポイントはスレッドプールで並行実行されるため、同一ユーザーへの承認×承認、承認×完了(大人)、承認×取消が並行すると片方の gold/exp/level 更新が消失する。
- 影響: 親が承認一覧を連続タップする通常操作(フロントは `handleApproveAll` で連続実行: `App.tsx:329` 付近)で、子供の報酬が1回分失われ得る。発覚しにくく、信頼を損なうタイプの不整合。
- 推奨修正(最小): 承認/取消も `_get_completion_lock` と同系のユーザー単位ロックで直列化する(プロセス内で書き込みは完結しているため十分)。または gold/exp を `SET gold = gold + ?` 形式の相対 UPDATE に変更し、level 計算は別途整合させる。
- 修正規模: 小〜中

### [High] H-4. SwitchBot Webhook のペイロード形式不一致の疑い(全イベントが「対象外」として捨てられる可能性)

- 確度: **問題の可能性あり(実機ログでの確認が必要)**
- カテゴリ: バグ / 仕様
- 対象: SwitchBot Webhook 受信 → センサー通知・見守り
- 対象ファイル: `MY_HOME_SYSTEM/models/switchbot.py`, `MY_HOME_SYSTEM/routers/webhook_router.py`
- 対象箇所: `SwitchBotContext` (5〜15行), `switchbot_webhook` (38〜56行)
- 証拠: SwitchBot 公式の Webhook 形式は `{"eventType": "changeReport", "context": {"deviceType": "WoContact", ...}}` で、`deviceType` は **context 内**かつ語彙は `WoContact`/`WoPresence` 系。一方このコードは:
  - `SwitchBotContext` に `deviceType` フィールドが無い(pydantic は未定義フィールドを捨てるため `getattr(ctx, "deviceType", ...)` は常にフォールバック)
  - フォールバック先のトップレベル `body.deviceType` は `Optional[str] = None`
  - 判定語彙 `TARGET_DEVICE_TYPES = ["Contact Sensor", "Motion Sensor"]` はデバイス一覧 API の語彙であり Webhook の語彙ではない
  - `tests/test_webhook_router.py:35` は**トップレベルに deviceType を置く自作形式**でテストしており、実形式との突合になっていない
- 問題: 実ペイロードが公式形式どおりなら `device_type=None` → 54行のガードで全イベントが `unsupported_device` として黙って捨てられ、見守り通知・開閉通知・daily_logs 記録が一切動いていないことになる。
- 影響: 防犯・見守りという安全系機能のサイレント無効化。
- 推奨修正(最小): まず本番ログ(DEBUG の "Ignored webhook from unsupported device type" 出力)で実形式を確認。context 側 `deviceType` をモデルに追加し、`Wo*` 語彙を含むマッピングで判定。実ペイロード JSON を固定化した contract test を追加。
- 修正規模: 小(調査後)

### [High] H-5. アイテム「承認待ち」フローが断絶している(バックエンドは即時消費、フロント/管理APIは pending 前提)

- 確度: 確実に不整合(どちらの仕様が正かは要確認)
- カテゴリ: 仕様 / バグ
- 対象: 報酬アイテムの使用〜親の承認フロー
- 対象ファイル: `MY_HOME_SYSTEM/services/quest_service.py`, `family-quest/src/features/shop/components/InventoryList.tsx`, `family-quest/src/features/quest/components/ApprovalList.tsx`, `family-quest/src/hooks/useGameData.ts`
- 証拠: `use_item` (599〜635行) は即 `status='consumed'` に更新する。`'pending'` を書き込むプロダクションコードは**リポジトリ内に存在しない**(テストが手動 SET するのみ)。一方:
  - `consume_item`(承認)・`cancel_usage`(取り下げ)・`get_pending_items`(管理一覧)は全て `pending` 前提で残置
  - フロントは `InventoryList.tsx:108` で `status === 'pending'` の UI 分岐、`useGameData.ts:104-109` で `/inventory/admin/pending` を **10秒ポーリング**(unified_server のログ抑制リストにも同パスが登録されており、稼働していることを示す)
  - `config.py:172` の `ENABLE_APPROVAL_FLOW` フラグは定義のみで参照0件(フロー消失の痕跡と整合)
- 問題: 「使用申請 → 親が承認」フローの UI・API・ポーリングが全て生きたまま、到達不能になっている。親側は使用申請に気づく手段が LINE push 1通のみ。
- 影響: 現状はデッドコード+無意味なポーリング負荷。将来 `pending` を復活させる変更をどちらか片側だけに入れると、即座に壊れる。
- 推奨修正: 仕様を確定させる。承認フローが正なら `use_item` を `status='pending'` に変更(1行)。即時消費が正なら `consume_item`/`cancel_usage`/`get_pending_items`/フロントの pending UI・ポーリングを削除。
- 修正規模: 小(仕様確定後)

### [High] H-6. AI ツールの SQL 許可テーブル検査がカンマ結合で回避可能

- 確度: 確実に問題
- カテゴリ: セキュリティ
- 対象: LINE Bot → AI → DB 検索ツール
- 対象ファイル: `MY_HOME_SYSTEM/services/ai_service.py`
- 対象箇所: `_extract_referenced_tables` (141〜171行)
- 証拠: 抽出正規表現が `(?:FROM|JOIN)\s+(識別子)` のみのため、`SELECT * FROM child_logs, quest_users` のような暗黙 CROSS JOIN では2つ目以降のテーブルが検査対象にならず、`ALLOWED_SEARCH_TABLES`(4テーブル)外の任意テーブルが読める。
- 問題: 接続は `mode=ro`(`core/database.py:55`)のため書き込みは不可だが、LLM へのプロンプトインジェクション(LINE メッセージ経由)で `quest_users` 等の非公開テーブル内容を返信に流出させられる。
- 影響: 家族の個人データ(LINE user_id、健康記録等)の意図しない開示。
- 推奨修正(最小): カンマ結合・サブクエリを考慮したパースにするか、より堅牢に「許可テーブルのみを ATTACH した専用 read-only DB ファイル/ビュー」に対して実行する方式へ変更。
- 修正規模: 小〜中

### [High] H-7. DB 保存失敗が握りつぶされ、LINE ユーザーへ「✅ 記録しました」と虚偽の成功応答を返す

- 確度: 確実に問題
- カテゴリ: バグ / 信頼性
- 対象: LINE Bot の記録系フロー(おはよう記録・子供の体調・食事記録)
- 対象ファイル: `MY_HOME_SYSTEM/handlers/line_logic.py`
- 対象箇所: `sync_run` (39〜48行) と呼び出し元 (227〜231, 290〜294, 358〜362行)
- 証拠: `sync_run` は `except Exception: logger.error(...)` のみで戻り値も検査されない。`save_log_generic` 自体も失敗時 `False` を返すが誰も見ていない。呼び出し元は保存結果に関係なく成功メッセージを返信する。
- 影響: DB ロック(H-1 で RuntimeError 化する経路を含む)・スキーマ不整合(H-2)・ディスク障害時に、健康記録などのデータ欠損が**ユーザーに成功と見えたまま**発生する。
- 推奨修正(最小): 保存結果の bool を検査し、失敗時は「記録に失敗しました。もう一度お試しください」を返信+エラー通知。
- 修正規模: 小

### [High] H-8. 兄妹連携クエスト(target='siblings')がフロントエンドに一切表示されず、機能全体が死蔵

- 確度: 確実に問題
- カテゴリ: バグ / 仕様
- 対象: 兄妹連携クエスト機能(完了報告〜カスケード承認)
- 対象ファイル: `family-quest/src/features/quest/components/QuestList.tsx`, `family-quest/src/features/family/components/FamilyDashboard.tsx`
- 対象箇所: QuestList.tsx 293〜299行のターゲットフィルタ(FamilyDashboard.tsx 66〜71行も同一ロジック)
- 証拠: フィルタは `'all'` / `'role_*'` / `user_id` 完全一致のみ許容し、`'siblings'` はどれにも一致せず全ユーザーから除外される。バックエンドには完全な実装が存在する(`_process_coop_quest_completion`、承認・却下・取消のカスケード、`migrations/0004`、テスト `test_coop_quest_router.py`)。
- 影響: バックエンド・マイグレーション・テストまで揃えた機能がユーザーから起動不能。`quest_data.py` に siblings クエストを定義しても画面に出ない。
- 推奨修正(最小): フィルタに「`target === 'siblings'` かつ現在ユーザーが child」を許容する分岐を追加。
- 修正規模: 小

### [High] H-9. `start_all.sh` の pkill 対象名が実ファイル名と不一致で、scheduler が二重起動する

- 確度: 確実に問題
- カテゴリ: バグ / 信頼性
- 対象: サーバー再起動運用
- 対象ファイル: `MY_HOME_SYSTEM/start_all.sh`
- 対象箇所: 29〜31行(`pkill -f scheduler.py`, 存在しない `bluetooth_monitor.py`)、46行(`pkill -9 -f unified_server.py`)
- 証拠: 実体は `scheduler_boot.py` であり、`pkill -f scheduler.py`(正規表現)は `scheduler_boot.py` にマッチしない。scheduler は `unified_server.py:121` から子プロセスとして起動されるため、`pkill -9` で親を落とすと graceful shutdown (134〜139行) が走らず孤児化する。
- 影響: 再起動のたびに scheduler が累積 → 全監視ジョブが二重実行(LINE/Discord 通知の重複、DB 記録の重複、TV ロック操作の二重送信)。
- 推奨修正(最小): pkill パターンを `scheduler_boot.py` に修正し、`-9` の前に TERM → 待機 → KILL の段階化。存在しない `bluetooth_monitor.py` 行は削除。
- 修正規模: 小

### [High] H-10. 認証境界が事実上存在しない(IP 制限ミドルウェアは無効化済み・Cloudflare Access JWT 未検証・API は自己申告 ID)

- 確度: 確実(設計上の自認あり。リスクの記録として)
- カテゴリ: セキュリティ
- 対象: 全 API(クエスト承認・カメラ映像・設定変更を含む)
- 対象ファイル: `MY_HOME_SYSTEM/unified_server.py`, `family-quest/src/App.tsx`
- 対象箇所: `ip_restriction_middleware` (175〜227行), App.tsx 18〜21行のコメント
- 証拠: ミドルウェアは IP 判定後、**非プライベート IP でも最終的に全リクエストを通す**(221〜227行。「Cloudflare Access 導入によりIP遮断を無効化、JWT 検証は将来」とコメントで自認)。API は `user_id`/`approver_id` を body の自己申告で受け付け、`approve` は「申告された ID が adult か」しか見ない。サーバーは `0.0.0.0:8000` で LISTEN。
- 問題: Cloudflare Tunnel 経由の外部アクセスは Access で守られるが、**ポート 8000 に到達できる者(LAN 上の任意端末・ゲスト Wi-Fi・IoT 機器)は誰でも、任意ユーザーとしての操作・親としての承認・カメラ録画の閲覧・カメラ設定変更が可能**。`Cf-Access-Jwt-Assertion` 検証が未実装のため、ヘッダー偽装で「Cloudflare 経由風」の外部アクセスと区別も付かない。
- 影響: 家庭内 LAN の信頼を前提にした設計としては成立するが、その前提(LAN 内は全て信頼できる)は IoT 機器の侵害・ゲスト端末で崩れる。子供の端末から親承認が可能な点はゲーム設計上も自壊的。
- 推奨修正: 最小は「LAN 内アクセスにも簡易トークン(既にある `SWITCHBOT_WEBHOOK_TOKEN` と同様の共有シークレット)を要求」+「Cloudflare 経由は `Cf-Access-Jwt-Assertion` の JWT 検証を実装」。承認系だけでも PIN を挟むとゲーム的にも健全。
- 修正規模: 中

### [High] H-11. CI は MY_HOME_SYSTEM のみ対象。フロントエンドはテスト0件でビルド検証すら無い

- 確度: 確実に問題
- カテゴリ: テスト
- 対象: リポジトリ全体の CI 保証範囲
- 対象ファイル: `.github/workflows/test.yml`, `family-quest/package.json`
- 証拠: `test.yml:14-16` の `defaults.run.working-directory: ./MY_HOME_SYSTEM` により ruff / pytest(cov 45%) / bandit / pip-audit の全てが MY_HOME_SYSTEM 限定。`family-quest` には test スクリプト・テストフレームワーク・テストファイルが一切無く、CI に npm install / `tsc -b` すら存在しない。`DDD`・`scripts/`・`.github/scripts/`(CI 基盤自身)も未 lint・未テスト。
- 影響: 本レビューのフロントエンド指摘(H-8 等)や DDD の指摘は現行 CI では構造的に検出不能。TS の型エラーですらマージまで気づけない。
- 推奨修正(最小): CI に `family-quest` の `npm ci && npm run build`(tsc 含む)と、DDD への ruff/bandit 適用を追加。次段で vitest 導入(テスト優先順位の章を参照)。
- 修正規模: 小(CI 追加)〜中(テスト整備)

### [High] H-12. DDD `extract_youtube_urls.py` のフォールバックが CWD 依存で、NAS 保護(Fail-Soft)も素通りする

- 確度: 確実(このリポジトリ配置では)
- カテゴリ: バグ / 信頼性
- 対象: YouTube URL 抽出バッチのデータ保存先解決
- 対象ファイル: `DDD/extract_youtube_urls.py`
- 対象箇所: 31行(`PROJECT_ROOT` 解決)、44行(フォールバック `Path("./data")`)、325行(DB パス)、334〜339行(`_verify_environment`)
- 証拠: `PROJECT_ROOT = CURRENT_DIR.parent` は `core/` の実位置(`MY_HOME_SYSTEM/core`)と矛盾し ImportError → フォールバック `get_managed_target_directory` は引数の絶対パスを無視して **CWD 相対の `./data`** を返す。`newface_monitor.py:55-63` では全く同じバグが「保存先が毎回変わり全件を新規と誤検知する」既知不具合として修正済みだが、こちらは未修正。さらに `_verify_environment` は絶対パス包含チェックのため `./data` をフォールバック状態と検知できない。
- 影響: 実行ディレクトリ次第で DB・データが散逸し、重複処理・データ迷子が起きる。NAS 障害検知の Fail-Soft も機能しない。
- 推奨修正(最小): `newface_monitor.py` と同じ解決方式(`CURRENT_DIR.parent / "MY_HOME_SYSTEM"`)・同じフォールバック修正を移植。
- 修正規模: 小

---

## 3. Medium(修正価値の高いもの。同一根の問題はグループ化)

### M-1. クエストドメインの整合性グループ

1. **削除済みクエストの承認がクラッシュする** — `quest_service.py:343` の `quest['quest_id'] in config.TV_UNLOCK_QUEST_IDS` は、`sync_master_data` がマスタから削除したクエスト(740行の `DELETE ... NOT IN`)の pending 履歴を承認しようとすると `quest=None` の subscript で TypeError → 500(報酬はロールバックされるため承認が恒久的に不能)。`_apply_quest_rewards` 自体は quest=None に耐えるのに、この行だけ無防備。〔確実〕
2. **所有済み報酬をマスタから削除すると sync_master_data 全体が失敗する** — `user_inventory` は `reward_master(reward_id)` への FK を持ち(schema:255)、`get_db_cursor` は `PRAGMA foreign_keys=ON`(database.py:24)。`reward_master` の `DELETE ... NOT IN`(quest_service.py:778)は所有中の報酬を消そうとした時点で IntegrityError → 同期全体が 500。**最小再現で FK 違反を実証済み**。`quest_data.py` は過去に報酬削除を実施済み(「パパのアルコール報酬削除」)で、再発が時間の問題。〔確実〕
3. **daily/weekly のリセット制約がサーバー側で強制されない** — `is_within_reset_period` は表示用 `completedQuests` の算出(get_all_view_data)にのみ使われ、`process_complete_quest` は10秒スパムチェックのみ。UI は完了済みを隠すが、API 直叩き(または表示バグ)で同一 daily クエストを1日に何度でも完了・多重報酬が可能。〔確実(仕様意図は要確認)〕
4. **naive タイムスタンプの解釈が同一ファイル内で矛盾** — `is_within_reset_period` (132〜133行) は tz 無し文字列を **UTC** とみなして+9時間する一方、スパムチェック (233〜235行) は同じ tz 無しを **JST** とみなす。レガシー行の日付判定が9時間ズレ、日跨ぎで完了状態の誤判定を起こす。analysis_service.py:49-53 の `utc=True` も同じ問題(グラフ・電気代集計の9時間ズレ)。〔確実(コード矛盾)/実データ影響は要確認〕
5. **スキーマ変更の三重管理** — H-2 の根本原因。`sync_master_data` 内の実行時 ALTER (711〜724, 769〜773行) と `migrations/`・`init_unified_db.py` が併存。`core/migrations.py` 自身の docstring もレース懸念を自認。今後の変更は migrations 一本に寄せる。〔確実〕

### M-2. マイグレーションランナーが失敗を「適用済み」として追認する

`core/migrations.py:76-82`: `executescript` が OperationalError を出すと「おそらく適用済み」とみなし `INSERT OR IGNORE` でバージョン記録して続行する。DB ロック・ディスクフル・SQL 誤りでも**未適用のまま適用済み扱い**になり、以後永久に再適用されない(サイレントなスキーマドリフト)。また executescript は暗黙 commit を挟むためファイル単位のアトミック性もない。最小修正: 「duplicate column」等の既知パターンのみ追認し、それ以外は起動失敗として扱う。〔確実〕

### M-3. カメラ/HLS サービスグループ(`services/camera_service.py`)

1. RTSP URL(認証情報込み)が ffmpeg の argv と `ffmpeg.log` に平文露出(102〜118行)。`ps` で全ユーザー閲覧可。〔確実〕
2. パスワード空文字時のログマスク `replace('', '***')` が文字列を破壊(99〜100行、Python の仕様)。〔確実〕
3. HLS ログファイルハンドルが close されず再起動毎にリーク(117行)。`_active_vod_processes` も無限蓄積(243行)。〔確実〕
4. VOD 生成の check-then-act 競合(176行の poll と 243行の登録の間)で同一プレイリストへ ffmpeg 二重書込。`set_camera_enabled` の devices.json 書込もロック・temp+rename 無しで破損リスク(254〜270行)。〔可能性あり〕
5. **サービス本体のテストが実質ゼロ**: `test_camera_router*.py` は camera_service を全て monkeypatch しており、`start_hls_stream`/`generate_record_playlist` の実装(上記1〜4の領域)は未実行。〔確実〕

### M-4. タイムラプス・監視ジョブグループ(`monitors/`)

1. `smart_timelapse_generator.py` の作業ディレクトリ(`work/timelapse`)・`motion.csv` が固定名+`setup_directories()` で全消しされ、複数ジョブ並列で相互破壊。ロック無し(205〜225, 319行)。〔可能性あり〕
2. `scheduled_timelapse.py:259-266`: FFmpeg 失敗でも実行済みマーカーを touch → 当日中の再試行なし。〔確実〕
3. ffmpeg 呼び出しの `stderr=PIPE` 満杯デッドロック(`smart_timelapse_generator.py:261` vs 272〜294行)、`timelapse_generator.py:209,254` の check/timeout 無し・エラー完全黙殺(494行の `except Exception: return False`)。〔可能性あり〕
4. 生成した `_part_*.mp4`・summary が `assets/timelapse` に無限蓄積(retention 対象外: `nas_monitor.py:172-179`)。逆に `timelapse_generator.py:327-328` の `glob("*")` 全消しはディレクトリ混入で IsADirectoryError。〔可能性あり〕
5. `switchbot_power_monitor.py:26` の状態キャッシュはプロセス内メモリだが、scheduler は毎回新プロセス起動(scheduler_boot.py:70-71) → ON/OFF 変化検知が構造的に一度も機能しない。〔確実〕
6. `nas_monitor.py:75-77` の無タイムアウト I/O は CIFS ストール(D-state)時に kill 不能で、in-flight スキップ機構(scheduler_boot.py:116-118)により **NAS 監視だけが再起動まで永久スキップ**。状態ファイルが `/tmp` のため再起動で障害中でも「正常」に戻る(31, 41行)。〔可能性あり〕
7. `server_watchdog.py:123` は systemd サービス稼働と AND 条件のため、`start_all.sh` 手動起動構成では常時「異常」誤報。pgrep 部分一致(61〜65行)は逆に偽陰性の余地。運用形態との整合確認が必要。〔仕様確認が必要〕

### M-5. 通知・AI 基盤グループ

1. AI ツール schema の timestamp 形式説明(`ai_service.py:229`「YYYY-MM-DD HH:MM:SS」)が実データ(`get_now_iso()` の ISO8601+09:00)と不一致 → AI 生成の BETWEEN 検索が文字列比較で漏れる。〔確実〕
2. 見守りタイマー `send_inactive_notification`(`sensor_service.py:57-76`)は CancelledError しか捕捉せず、通知失敗で `IS_ACTIVE=False` に到達しないと**再開通知が永久に出ない**。〔可能性あり〕
3. `SimpleRateLimiter` の asyncio.Lock がリクエスト毎の別イベントループ(`line_handler.py:102` の `asyncio.run`)を跨いで共有され、RuntimeError や制限すり抜けの余地(`ai_service.py:61,84`)。threading.Lock にすべき箇所。〔可能性あり〕
4. `analysis_service.py` 全域の f-string SQL(152, 223, 255, 277, 291, 337, 366〜368行)。現呼び出し元は内部値だが、UI 入力に接続された時点でインジェクション成立(ro 接続のため読み取りのみ)。〔可能性あり〕
5. `DiscordErrorHandler.emit`(`core/logger.py:42`)はエラーログのたびに同期 `requests.post`(timeout 5s)を呼び、リクエスト処理スレッドを最大5秒ブロック。〔確実〕

### M-6. フロントエンドの動作バグ グループ(`family-quest/src`)

1. 承認 API の戻り値(`leveledUp`/`earnedMedals`)を捨てており(`useGameData.ts:242-250`, `App.tsx:299-311`)、子供の承認経由レベルアップ/メダル演出が絶対に出ない。〔確実〕
2. 一括承認の再試行 `onRetry: handleApproveAll`(`App.tsx:329-334`)が古い `pendingQuests` クロージャを掴み、承認済みを再承認して 400 → エラーが消えない。〔確実〕
3. アイテム承認・使用・取消の mutation に `onError` が無くサイレント失敗(`ApprovalList.tsx:65-72`, `InventoryList.tsx:37-77`)。クエスト系は全てエラーモーダルがあるのと非対称。〔確実〕
4. `FamilyLog.tsx:64-69` が報酬購入の消費ゴールドを「+N G」(獲得)表示。〔確実〕
5. `QuestList.tsx:333` のソートが存在しない `id` カラム参照で `NaN` を返し並び不定(実カラムは `quest_id`)。`id || quest_id` と `quest_id || id` の混在も残置。〔確実〕

### M-7. DDD バッチグループ

1. `batch_download_discord.py:262,270`: 履歴 load/add とも `except: pass` — 失敗時は再ダウンロード・再通知の嵐、ログすら無し。〔確実〕
2. 同 150〜157行: "403"/"429"/"503" の部分文字列マッチが動画 ID 等に誤爆し、12時間クールダウンでセッション全停止(831〜842行)。〔可能性あり〕
3. 同 434〜447行: `noplaylist`/上限なし — リスト1行がプレイリスト URL なら無制限 DL、`MAX_TASKS_PER_RUN=30` が無意味化、スキップ判定も破綻。〔可能性あり〕
4. 同 69〜87行: `services/` パス解決がこのリポジトリ配置では失敗し、DISK FULL 等のエラー通知が警告1行でサイレント無効化。〔可能性あり〕
5. `newface_monitor.py:1786-1790`: 既知キャストの union/置換の非対称で、一時消失→復帰キャストが再通知。仕様書も挙動をなぞるのみで意図不明。〔仕様確認が必要〕
6. `newface_monitor.py`: 多重起動ロック無し(batch 側は flock あり)。cron 毎時起動で1回が1時間超になると既知リスト・サマリの更新ロスト。〔可能性あり〕

### M-8. 起動・設定・シェルグループ

1. `config.py` は import 時に NAS パス検証を最大約31秒×3箇所実行(`ensure_safe_path_with_backoff`: 225, 229, 431行)。NAS 停止時は config を import する**全プロセス**(server/monitors/テスト)の起動が約90秒ブロック。フォールバック先ローカルに書かれたデータのうち `nas_utils.sync_fallback_to_nas` の対象外(tmp_video 等)は復旧同期されず split-brain 化。〔確実(ブロック時間)/可能性あり(split-brain)〕
2. CORS 設定の二重定義: `unified_server.py:160-165` はハードコードの `ALLOWED_ORIGINS` を使い、`config.py:416-423` の `CORS_ORIGINS`/`ALLOW_ALL_ORIGINS` は**参照されない死に設定**。env で `ALLOW_ALL_ORIGINS=true` としても無効で、設定変更が効かない罠。〔確実〕
3. `tools/connect_speaker.sh:37-43`: エスケープ済み変数を作った直後に未エスケープの `$message` を JSON へ埋め込み(エスケープ処理が死にコード)。〔確実〕
4. `start_all.sh:27-31` の `pkill -f` 部分一致は `vim unified_server.py` 等も殺す(仕様書自身がリスクを明記)。67, 72行の `&` のみのバックグラウンド化は SSH ログアウトで SIGHUP 死の余地。〔可能性あり〕
5. `tools/keep_alive_anker.sh:11` の `CONNECT_SCRIPT` 絶対パスがリポジトリ実体(`tools/connect_speaker.sh`)と不一致 — 存在しなければ再接続が永久スキップ。〔可能性あり(デプロイ配置次第)〕

### M-9. データ保護・入力境界グループ

1. `quest_data.py` の `USERS[].info` に実名・年齢・「住宅ローン5,400万」等の個人情報がハードコード。`config.py:503-530` が「年齢等は gitignore 対象の family_members.local.json へ」という匿名化方針を敷いたのと矛盾。〔確実(方針不整合)〕
2. `config.py:253` `BACKUP_FILES` に `.env` が含まれ、バックアップ先(NAS の db_backups)へ全シークレットが複製される。NAS 側のアクセス制御が弱いと漏洩点が増える。〔可能性あり〕
3. `/api/quest/upload`(`quest_router.py:89-117`)にファイルサイズ上限なし — 巨大アップロードでディスク圧迫(拡張子+マジックバイト検証は実装済みで良い)。〔確実〕
4. SwitchBot Webhook はトークン未設定時に無検証で受理(`webhook_router.py:44-46`、起動時警告あり)。設定時もクエリパラメータのためアクセスログ等に露出しやすい。偽イベント注入で LINE 通知・DB 汚染が可能。〔確実(挙動)/影響は環境次第〕
5. アバター URL が無検証保存され(`update_avatar`)、フロントの `startsWith('/')` 判定は protocol-relative `//evil.example/x` を通す(`Header.tsx:110` 等)。H-10(無認証)と組み合わせで LAN 内の誰でも設定可能。〔可能性あり〕
6. `sync_strict.py:18-22,71`: マスタに無い行の無確認 DELETE(マスタ空なら全削除)。`quest_data.py` の ID 変更ミス一発で本番マスタが消える(M-1-2 の FK とも連動)。〔可能性あり(運用前提次第)〕

### M-10. CI・監査基盤グループ

1. `test.yml` に `permissions:` ブロックが無く、リポジトリ設定次第で write-all トークンが供給される(他2ワークフローは最小権限明示と不整合)。`permissions: contents: read` を追加。〔可能性あり〕
2. `check_spec_drift.py:109-111`: 孤立ドキュメント判定が `old/`・`tests` 等の除外を適用せず偽陰性。147行は rename の旧パスを喪失し、ソース rename 時の仕様書孤立を検知不能。83〜86行のフラット stem マッピングは同名ファイルで衝突。〔確実(ロジック)〕
3. `tools/haircut_advisor.py:27,42`: DB/.env パスが `tools/` 配下解決になり、リポジトリ構成のままでは常に「DB が見つかりません」で機能死。〔確実〕

---

## 4. Low(価値のあるもののみ)

- `config.py:172` `ENABLE_APPROVAL_FLOW` は定義のみで参照0件(H-5 と関連する痕跡)。削除か復活かを H-5 と同時に決めるべき。
- `family-quest/src/utils/gameHelpers.js` は完全な死にコードで、かつ `getDayIndex()`(日曜=0)が実装の曜日規約(月曜=0)と矛盾する「将来の罠」。削除推奨。`masterData.js` のフォールバック報酬は `cost_gold` 欠落で「undefinedG」表示になる。
- `train_service.py:29-60`: API 失敗時に「🟢 平常運転」を返す fail-soft — 遅延見逃しに直結するため「情報取得不可」の区別表示が望ましい。
- `core/logger.py`: 複数プロセスが同一 `home_system.log` を `TimedRotatingFileHandler` でローテーションし、ローテ競合でログ喪失の余地。`emit` の `"Discord" not in record.msg` は msg が非文字列だと TypeError(logging 内部で握られるが通知は落ちる)。
- `process_reject_quest` は履歴を DELETE するため status `'rejected'` は生成されず、スパムチェックの `status != 'rejected'` (219行) は死に条件。却下履歴を残す仕様なら UPDATE に。
- `quest_service.py` `filter_active_quests:521` のログが存在しない `q.get('id')` を出力(常に None)。`get_all_view_data:810` は `target_user`(例 'siblings')を user_id としてブースト計算に渡す(結果は常に0で無害だが意味が誤り)。
- `sync_master_data:735` のみ naive `datetime.now()` で updated_at を保存(他は `get_now_iso()`)。
- `camera_monitor.py`: 失敗パスで `/tmp/snapshot_*.jpg` 残骸(203, 222〜236行)、玄関カメラの全ペイロード INFO ログ残留(487〜491行)。
- `keep_alive_anker.sh:9`: `TIMESTAMP` を起動時に1回だけ評価し、ログが常に同時刻。
- `scripts/merge_mds.py`: CWD 依存・除外なし・`rglob` 順序不定で出力が非決定的。
- `nas_utils.py:62-64`: 「上書きを防ぐ」とコメントしつつ `copy2` は無条件上書き(コメントと実装の乖離)。
- GitHub Actions がタグ pin(SHA 未 pin)。フォーク PR では spec-drift のコメント投稿ステップが 403 で赤くなる(同一リポジトリ運用なら顕在化しない)。
- DDD `file_utils.sanitize_filename` は `".."` 等を空文字にし、`.mp4`(隠しファイル)や空 stem のファイル名を生成し得る。パストラバーサル自体は不成立。

---

## 5. 品質向上ロードマップ

### Phase 1: 今すぐ修正(データ整合性・サイレント障害の根)

1. H-1 `get_db_cursor` 書き直し(+H-7 の保存失敗応答修正をセットで)
2. H-2 新規構築パス修正(`quest_data.py` への role 明記、`init_unified_db.py` の daily_logs 修正) + M-2 マイグレーション追認の是正
3. H-6 AI SQL 許可テーブル検査の強化
4. M-1-1 削除済みクエスト承認クラッシュ、M-1-2 報酬削除時の FK 失敗(inventory の扱いを決めて DELETE 順序/ON DELETE を設計)
5. H-9 `start_all.sh` pkill 修正(通知二重化の根)

### Phase 2: 短期改善(High のバグリスク・機能不整合)

1. H-3 承認/取消経路のロックまたは相対 UPDATE 化
2. H-4 SwitchBot 実ペイロード確認 → モデル/判定修正 + contract test
3. H-5 アイテム承認フローの仕様確定と片側への統一
4. H-8 兄妹連携クエストのフロント表示対応
5. H-12 / M-7 DDD のパス解決統一・履歴 I/O の可視化
6. M-3 カメラ認証情報の露出対策と fd/プロセスリーク修正
7. M-6 フロントの承認演出・サイレント失敗・再試行バグ
8. H-11 CI へ `tsc -b`/build と DDD lint を追加

### Phase 3: 中長期改善(アーキテクチャ・拡張性)

1. スキーマ管理を `migrations/` に一本化し、`init_unified_db.py` を廃止(空 DB からの構築テストを CI に追加)
2. H-10 認証境界の実装(Cf-Access-Jwt-Assertion 検証 + LAN 内共有シークレット、承認操作の PIN)
3. 設定の一本化(CORS 二重定義解消、`ENABLE_APPROVAL_FLOW` 等の死に設定整理、config import 時の NAS ブロッキング分離)
4. タイムラプス/監視ジョブの排他制御・作業ディレクトリ分離・ffmpeg 呼び出しの timeout/エラー処理共通化
5. フロントエンドのテスト基盤(vitest)導入

---

## 6. テスト追加優先順位

### Critical(即追加すべき)

- **空 DB からの E2E**: `init_unified_db.py`(または migrations のみ) → 起動 → `sync_master` → 子供の complete → 親の approve が通ることの検証(H-2 の再発防止。現 `test_migrations.py` は自作テーブルで代替しており実経路を通らない)
- **`get_db_cursor` の異常系**: 本文内 locked・接続リトライ枯渇時の挙動(H-1 の回帰防止)
- **SwitchBot Webhook の contract test**: 公式ドキュメント形式の実ペイロード JSON を固定化して受理されることを検証(H-4)

### High

- 同一ユーザーへの並行 approve×approve / approve×complete の残高整合(H-3)
- `camera_service` 本体(モックなし)のユニットテスト: プレイリスト生成・排他・キャッシュ(M-3-5)
- フロントエンド: `getQuestLockState`・QuestList のフィルタ/ソート(siblings 含む)の純関数テスト(H-8, M-6-5)。まず CI に `tsc -b` を入れるだけでも価値がある
- 削除済みクエストの pending 承認、所有中報酬のマスタ削除 → sync(M-1)

### Medium

- `line_logic` の保存失敗時応答(H-7)
- `train_service`(テスト皆無)・DDD の履歴 I/O・スキップ判定
- `is_within_reset_period` の naive/aware・日跨ぎ境界

---

*本レポートはコードレビューのみを目的とし、コードへの修正は行っていません。各指摘の修正はレビュー結果の確認後に別途実施してください。*
