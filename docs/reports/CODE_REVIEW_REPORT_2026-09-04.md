# 全体コードレビュー報告 (2026-09-04)

対象: `MY_HOME_SYSTEM`(FastAPI バックエンド) / `family-quest`(React PWA) / `DDD`(バッチ群) / `.github`(CI) / `deploy`

前提と手法:
- 既存レポート(`CODE_REVIEW_REPORT_ALL.md`、`CODE_REVIEW_REPORT_2026-08-22.md`)と open Issue(#318/#319/#321/#327/#328/#338/#339/#340)で既に追跡中の項目は**再掲しない**。以下はすべて今回新たに見つかった指摘。
- 意思決定済みの設計(クライアント申告 `user_id`、Cloudflare Access エッジ委譲、IP 制限ミドルウェアがログのみ、家族名の `config.py` ハードコード、Streamlit ダッシュボードのアプリ内認証なし)は対象外。
- 全ソースを通読したうえで、High 以上の指摘は該当コードパスを個別に再確認済み。動的検証: `MY_HOME_SYSTEM` pytest 732件 pass(カバレッジ 87%、omit 除外前は 63%)、`DDD` pytest 128件 pass、`family-quest` `tsc -b`/ESLint/vitest 22件 pass、ruff ゲート(F821/F822/F823/E9)両ディレクトリ pass、bandit High 0件(DDD の SHA1 1件は非ブロッキング設定通り)、`pip-audit` の「検知0件時にファイル未生成」挙動を実機再現。
- 本レポートは「レビュー時点の記録」であり、残件の状態管理は従来どおり GitHub Issue を正とする(Issue #323 方針)。全指摘は **Issue #355(トラッキング親Issue)** 配下のサブIssue #356〜#414 に展開済み(High/Medium は1指摘=1 Issue、Low と保守性項目はサブシステムごとに1 Issue、Discord送信系4件と `role_*` 不整合2件は統合)。

---

## 1. エグゼクティブサマリー

| 重要度 | MY_HOME_SYSTEM | family-quest | DDD | CI/基盤 | 計 |
|---|---:|---:|---:|---:|---:|
| High | 7 | 2 | 2 | 3 | **14** |
| Medium | 18 | 7 | 6 | 9 | **40** |
| Low | 約25 | 11 | 13 | 10 | 約60 |

静的チェック・テストはすべてグリーンで、サービス層/ルーター層の品質は高い。問題は (a) **ゲームロジックの経済系の穴**(ゴールド増殖・二重使用)、(b) **AI ツールの SQL 許可リストのバイパス**、(c) **運用系(ディスク・プロセス・NAS 障害時)の自己破壊的な挙動**、(d) **CI の検知力が見かけより大幅に低い**(カバレッジ omit・DDD テストのスキップ・pip-audit 誤起票)の4領域に集中している。

### 最優先で対応すべき 10 件

| # | 指摘 | 場所 | 影響 |
|---|---|---|---|
| 1 | 承認済み履歴のキャンセル時 `max(0, gold - earned)` で飽和 → 報酬購入後にキャンセル+再完了で無限ゴールド | `services/quest_service.py:770-778` | ゲーム経済の破綻 |
| 2 | AI `search_db` のテーブル許可リストが `"quest_users"`/`[t]`/`` `t` ``/`FROM"t"` で素通り(実証済み) | `services/ai_service.py:182-218` | 非公開テーブルの読み出し |
| 3 | 録画 VOD の HLS セグメントがローカル SD カードに無制限蓄積、削除経路なし | `services/camera_service.py:24-25,268-351`、`monitors/nas_monitor.py` | ルート FS 逼迫 → SQLite 書込失敗 |
| 4 | newface_monitor が NAS 障害時、呼び出しごとに `sudo mount`+Discord 通知(毎時 240 回超)+全キャスト再通知 | `DDD/newface_monitor.py:1218-1234,1524-1619,2213` | 通知ストーム・NAS 復旧妨害 |
| 5 | pip-audit 週次監査は検知 0 件のときレポートファイルが生成されず、毎週「チェック失敗」Issue を起票 | `.github/workflows/pip-audit-weekly-audit.yml:44-63` | 初回実行から誤 Issue |
| 6 | LINE 経由の Family Quest コマンドが本番で成立しない(LINE の `U…` ID と `quest_users.user_id` の対応表が無い) | `handlers/line_handler.py:102-155`、`services/line_service.py:104-189` | 機能が実質デッド |
| 7 | シャットダウン時に ffmpeg・monitors 子プロセスが孤児化し、再起動後に HLS 二重書込・古い設定での DB 書込 | `unified_server.py:139-160`、`scheduler_boot.py`、`start_all.sh:31-36` | 再生破損・DB ロック |
| 8 | `DiscordErrorHandler` が 2000 字制限を無視 → 長いエラーほど無言で消える(scheduler の stderr 全文が該当) | `core/logger.py:37-61` | 重要障害の通知欠落 |
| 9 | CI の `--cov-fail-under=45` は実測 87%(omit 込み)/63%(真値)に対し無意味。テスト済みモジュールを omit で隠している | `.coveragerc`、`test.yml:118` | 退行検知力ゼロ |
| 10 | 横画面 4 人パネルで無限クエストのクールダウンが他メンバーにも掛かる(`completedSignal` に user_id なし) | `App.tsx:185`、`QuestList.tsx:66` | UX バグ(子どもが完了できない) |

---

## 2. MY_HOME_SYSTEM: Family Quest(quest_service / quest_router / models)

### High

**Q-H1. 承認済み履歴のキャンセルでゴールドが 0 に飽和し、無限ゴールドループが成立する**
`services/quest_service.py:770-778` (`_revert_and_delete_history`)、`:738-760`
キャンセルは approved 行も対象(フロント `QuestList.tsx:89` の `canCancel` で長押しキャンセルが正式機能)。ゴールド戻しが `max(0, gold - gold_earned)` で、行は DELETE されるため直後の再完了は spam/周期チェック(`:398-423`)を素通りする。
再現: gold 0 の dad が quest 完了 → +100G → 100G の報酬購入 → gold 0 → 履歴をキャンセル → `max(0, 0-100)=0`(減らない) → 同じ quest を再完了 → +100G。1 サイクルごとに報酬が無料。子どもでも承認さえ通れば同じ。
修正: 残高不足なら 400 で拒否、または負残高を許容(クランプ廃止)。少なくとも `gold < gold_earned` のときは拒否。

### Medium

**Q-M1. `use_item` に排他も条件付き UPDATE もなく二重使用が可能**
`services/quest_service.py:944-983`。`SELECT status` → Python 判定 → `UPDATE … WHERE id=?`(status 条件なし)。既存の 3 種のロックのいずれにも参加していない。連打で `quest_history` に「アイテム使用」行 2 件、LINE 通知 2 通、`total_quests` +2。
修正: `UPDATE user_inventory SET status='consumed' WHERE id=? AND status='owned'` として `rowcount==0` なら 400。

**Q-M2. `role` が NULL/未知のユーザーは「大人扱い」で即時報酬になる(承認・購入側の判定と逆)**
`services/quest_service.py:430-452` は `if role == ROLE_CHILD` の else を大人パスにしている。承認は `role == ROLE_ADULT` 必須(`:576`)、購入も同様(`:889`)。role NULL の行が存在すると承認ゲートなしでゴールドを得る。
修正: 「`ROLE_ADULT` のときだけ即時、それ以外は pending」に反転。

**Q-M3. `target_user='role_*'` はフロント/表示系が対応済みなのに完了 API は無条件 403**
`services/quest_service.py:389-393` vs `:1217-1226`、`QuestList.tsx:308-309`、`FamilyDashboard.tsx:72-73`。現状データに `role_*` は無いが、追加した瞬間「UI に出るが押すと 403」になる。
修正: 完了側で `target_user.startswith('role_') and user['role'] == target_user` を許可するか、`role_*` サポート自体を削除。

**Q-M4. `_delete_orphaned_avatar` が他ユーザー参照中の画像を削除しうる**
`services/quest_service.py:210-246`、`routers/quest_router.py:77-79`。`avatar_url` は任意文字列で、旧アバター削除時に他行の参照を確認しない。mom が dad の `/uploads/<uuid>.png` を指定してから絵文字に戻すと dad の画像が物理削除される。
修正: 削除前に `SELECT 1 FROM quest_users WHERE avatar=? AND user_id!=?`。`avatar_url` を絵文字または `^/uploads/[0-9a-f-]{36}\.(jpg|jpeg|png|gif|webp)$` に制限。

**Q-M5. `manual_backup` が `async def` 内でブロッキング I/O を直接実行しイベントループを止める**
`routers/system_router.py:9-14`、`services/backup_service.py:40-60`。sqlite backup + NAS への `shutil.copy2` が同期実行され、`/webhook/switchbot`・`/callback/line` を含む全リクエストが停止する。`detail=msg` に NAS パス等の生例外文字列も入る。
修正: ハンドラを `def` にする(threadpool 実行)か `run_in_executor`。detail は固定文言に。

### Low

- **Q-L1** `:302-306` 連続達成ボーナスが approved のみを見るため、承認待ちの日を「サボり」と誤判定してボーナスが付く。spam チェック(`:398-402`)と同じ `status != 'rejected'` に。
- **Q-L2** `:1145-1157` `target='all'` の daily クエストで表示ボーナスは常に 0 だが完了時はボーナスが付く(1100/1105 が該当)。
- **Q-L3** `:674-678` vs `:770-778` approved 履歴のキャンセルで `medal_count` が戻らない(履歴にメダル数を保存していない)。
- **Q-L4** `models/quest.py:49-70,118-120` int フィールドに上限がなく `2**64` で `OverflowError` → 500(検証済み)。`Field(ge=1, le=2**63-1)`、文字列に `max_length`。
- **Q-L5** `:173`、`:971-974` `total_quests` に承認待ち行とアイテム使用行(`quest_id=0, status='approved'`)が含まれる。
- **Q-L6** `routers/quest_router.py:93` `file.filename` が None で 500。書込途中の例外で書きかけファイルが残る。
- **Q-L7** `:977-981`、`:622`、`:440/:490/:684-689` トランザクション内で LINE 送信・TV 電源・音再生を実行。`use_item` は LINE API 往復中 SQLite の書込ロックを保持。
- **Q-L8** `reset_game.py:143-148` `quest_users` のみリセットし履歴を残すため「本日完了済み」が発動。`:20-41` `LOG_DIR="logs"` が CWD 相対で import 時に `logging.basicConfig` を実行。`:21-27` 漢字実名ハードコード。稼働中サーバーのロックを経由しない。
- **Q-L9** `core/database.py:97-116` `save_logs_batch_generic` に `save_log_generic` と同じ識別子ホワイトリストが無い。
- **Q-L10** `:88-158,351` ロック辞書が未知の `user_id` で無制限に増える(存在チェック前にロック生成)。
- **Q-L11** `:286-336` `daily` かつ `reset_period='weekly'` の組合せでは毎週 +60% ボーナス(潜在)。

### 品質

- **N+1**: `get_all_view_data` がクエストごとに `calculate_quest_boost` を呼び `GET /data` 1 回で約 50 回の履歴クエリ(`:1145-1151`)。`sync_master_data:1083-1094` も同様。
- **デッドコード**: `pre_requisite_quest_id` は保存されるだけで参照なし(サーバー側は前提未達成を検証しない)。`quest_router.py:70-71` `seed_data()`、`models/quest.py` の `UserAction`/`InventoryItem`、`:1169-1176` の到達不能 try/except、`:1215-1226` の `role_*` 分岐。
- `routers/quest_router.py:37-43` `GET /data` の例外を `logger.error(f"{e}")` で潰しスタックトレース消失、`HTTPException` まで 500 化。
- `:594-604` `_process_approve_quest_locked` で `user` が None のとき TypeError → 500(`_approve_linked_history:640` は防御済みで非対称)。
- `models/quest.py:18-43` `MasterQuest/MasterReward` の `type/target/reset_period` が `Literal` でなく、`exp/gold/cost_gold` は負値可、`days='0,,1'` で `GET /data` 全体が 500。

---

## 3. MY_HOME_SYSTEM: LINE / 通知 / AI / ダッシュボード

### High

**L-H1. AI `search_db` のテーブル許可リストが引用符付き識別子で素通りする(実証済み)**
`services/ai_service.py:182-218` (`_extract_referenced_tables`)、`:244-250`。正規表現 `(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*|\()` は `"t"`/`[t]`/`` `t` ``/`"main".t`/`FROM"t"`(空白なし)に一致せず「存在しない」扱いになる。許可テーブルを 1 つ含めれば実行される。
再現: `SELECT * FROM food_records WHERE 0 UNION ALL SELECT user_id,name FROM "quest_users"` → ALLOWED。Issue #224 で塞いだ経路と同種。
修正: 正規表現での網羅は困難。`common.execute_read_query` 側で `conn.set_authorizer()` を使い `SQLITE_READ` を許可テーブル以外は DENY(加えて `SQLITE_FUNCTION` で `load_extension` 等を拒否)にするのが構造的。暫定策なら `"`・`` ` ``・`[` を含む SQL を即拒否。回帰テストに上記 5 ケースを追加。

**L-H2. LINE 経由の Family Quest コマンド(ステータス/クエスト/承認/却下)は本番で成立しない**
`handlers/line_handler.py:102-115,146-155`、`services/line_service.py:109,143,178-189`、`services/quest_service.py:545-547`。LINE の `event.source.user_id`(`U`+32hex)をそのまま `quest_users.user_id`(`dad/mom/son/daughter`)と突き合わせているが、マッピングがリポジトリに存在しない。「ステータス」は常に「ユーザーデータが見つかりません」、「承認 N」は常に 403、「クエスト」は `target='all'` 以外が非表示。`approve:`/`reject:` postback を生成する箇所も無く `handle_postback` のその分岐はデッドコード。テストはシード値に `'dad'` を直接渡すため検出されない。
修正: `quest_users` に `line_user_id` 列を追加(新規 `NNNN_*.sql`)し入口で解決、未登録なら案内文。使わないなら分岐と仕様書を削除。

### Medium

**L-M1.** `services/line_service.py:33-38,43-48` `save_log_async` の戻り値(`False`)を無視して「記録しました」を返す(AI ツール経路・キーワード経路)。H-7 で修正したのは `line_logic.py` 側のみ。
**L-M2.** `services/ai_service.py:464-465,484-487` ツール実行後に 2 度目の function_call が返ると `response.text` が `ValueError` → 一般エラー文 → 再送で重複登録(#232 の経路が残存)。1 メッセージ 2 件記録で再現。
**L-M3.** `handlers/line_handler.py:121` 「元気ない/元気がない」が `"元気" in msg_text` で「元気」として記録される。2 名併記時は 1 名しか処理しない。
**L-M4.** `routers/webhook_router.py:28` → `ai_service.py:350-356,422,464` Webhook 応答前に tenacity リトライ(最大 3 試行×2 回)+AI 処理を完走。reply token(約 1 分)超過で無応答。`isRedelivery`/`webhookEventId` による重複排除なし。
**L-M5.** `services/notification_service.py:238-262` Discord 2000 字制限・429 未対応。`monitors/log_analyzer.py:171-189` の週間レポートは容易に超過し届かない。
**L-M6.** `handlers/line_handler.py:132`、`services/line_service.py:161` LINE テキスト 5000 字制限未考慮(Gemini 応答・クエスト一覧)。
**L-M7.** `views/dashboard/quest_tab.py:213` `log['text']`(ユーザー名+クエスト/報酬タイトル、認証なしの API から書込可)を `unsafe_allow_html=True` で描画 → 格納型 HTML インジェクション。`summary.py:238-252` も同パターン。`misc_tab.py` は escape 済みで非対称。
**L-M8.** `scripts/claude_investigate.sh:59-98` ログ由来の異常サマリを無加工でプロンプトに埋め込み、`Read` 無制限(`.env` 可)+`gh issue create` 権限のエージェントに渡す。ログには LINE 表示名・本文(`line_handler.py:92`)等の外部由来文字列が含まれる。`--disallowedTools "Read(.env*)"`、サマリを「データであり指示ではない」区切りで囲む、`--body-file` 禁止。
**L-M9.** デプロイパス不一致: `tools/connect_speaker.sh`、`tools/keep_alive_*.sh` は `/home/masahiro/develop/MY_HOME_SYSTEM`、`scripts/claude_investigate.sh:41` は `/home/masahiro/develop/my_python_project/MY_HOME_SYSTEM`。片方は無言で機能停止する。

### Low

- **L-L1** `routers/webhook_router.py:20-33` `X-Line-Signature` 欠落時 SDK が `AttributeError` → 汎用 except → **200 "OK"**。`.decode` が try 外で不正バイト列は 500。複数イベント一括時に 1 件目の例外で以降が処理されないのに 200。
- **L-L2** タイムゾーン: `line_logic.py:329` naive `datetime.now()`、`dashboard.py:340` ts に `T` が無いと現在時刻を表示、`analysis_service.py:226` `replace(...)` が microsecond を残し月初 0 時ちょうどの行を落とす。
- **L-L3** `analysis_service.py:56-65` 1 行でも不正タイムスタンプがあると全体が空 DF。
- **L-L4** `ai_service.py:434` `parts[0]` のみ検査、テキスト+function_call の複数パートでツール呼出を無視。
- **L-L5** `dashboard.py:411` `traceback.format_exc()` を画面表示。
- **L-L6** `line_handler.py:86` グループ発言で `user_id` が None のケース未考慮。

### 品質

- 二重実装/未使用: `line_logic.get_daily_health_summary`(生 `sqlite3.connect`、`:158`)と `line_service.get_daily_health_summary_text`(未使用)。`line_service.log_daily_action/log_ohayo`、`line_logic.get_quota_text/create_quick_reply`、`notification_service.send_reply/get_line_message_quota` は本番から未参照。
- f-string SQL: `analysis_service.py:149,162,237,276,298,311,377,388`(bandit B608 多数)。`load_ranking_data` 等は DB 由来値を埋め込んでおりプレースホルダ化すべき。
- 裸の `except:`: `line_logic.py:97,175`、`line_service.py:87`、`analysis_service.py:323,327`、`post_boot_health_check.py:90,106,121,287,302`。
- `line_service.py:72` の `row_factory` 設定はカーソル生成後で無効。`line_handler.py:41` `_profile_cache` 無制限成長。

---

## 4. MY_HOME_SYSTEM: サーバー / 設定 / core / monitors / camera

### High

**S-H1. 録画 VOD 用 HLS セグメントがローカルに無制限蓄積し、削除経路が存在しない**
`services/camera_service.py:24-25,268-351`、`monitors/nas_monitor.py:270-293`。`/api/cameras/record/{cam}/{date}/record_*.m3u8` を叩くごとに、その日の全 NVR 録画(10 分×144 本)を `-c:v copy` で `data/hls_streams/vod/<cam>/` に再多重化(1 日数 GB)。`run_retention_cleanup` の対象に `hls_streams` は無く、他にも削除処理なし(grep 確認)。当日分はキャッシュ対象外で要求のたび再生成。Pi のルート FS 逼迫 → SQLite 書込が `disk I/O error`。
修正: `run_retention_cleanup` に `HLS_VOD_DIR`(保持 1〜3 日)を追加。当日分は「最終セグメント生成から N 分以内なら再利用」。

**S-H2. シャットダウン/再起動時のプロセスツリー清掃が不完全**
`unified_server.py:139-160`、`scheduler_boot.py:106-124`(SIGTERM ハンドラなし)、`services/camera_service.py:202,342`、`start_all.sh:31-36`。lifespan 終了時に止めるのは scheduler と camera_monitor のみ。(a) `_active_processes`/`_active_vod_processes` の ffmpeg は terminate されない。(b) scheduler は SIGTERM で即死し、実行中の `nas_monitor.py` 等(最大 3600s)が孤児化。(c) `start_all.sh` の `pkill -f` は 4 パターンのみで ffmpeg や `monitors/*.py` を殺さない。再起動後に旧 ffmpeg が `live/<cam>/stream.m3u8` に書き続ける横で新 ffmpeg が同じパスに書く。
修正: lifespan shutdown で全 Popen を terminate/kill、`scheduler_boot.py` に SIGTERM ハンドラ+子プロセス kill、`start_all.sh` の対象拡張または `setsid`+プロセスグループ kill。

**S-H3. `DiscordErrorHandler` が 2000 字制限を考慮せず、長いエラーほど無言で消える**
`core/logger.py:37-41,56-61`。content = ヘッダ+`log_msg`+`format_stack()` 末尾 1000 字(exc_info が無くても常に付く)。`log_msg` が約 900 字超で 400 → `except Exception: pass`。`scheduler_boot.py:82` の `logger.error(f"Stderr: …")` は監視スクリプトの stderr 全文を流すため「重要な失敗ほど届かない」。ERROR ごとにデーモンスレッドを無制限生成し、cron 短命プロセスでは終了間際の ERROR が送信完了前に殺される(DDD 側にも影響)。
修正: `content[:1900]`、`exc_info` 無しなら stack 省略、単一ワーカースレッド+`queue`、`atexit` で join。

### Medium

**S-M1.** `monitors/log_analyzer.py:105-121`、`health_watch.py:114-123` タイムスタンプの無い行(トレースバック継続行)が `since` フィルタを素通りし、1 回トレースバックが出るとその日 logrotate まで毎時 `app_logs` 異常が立ち続ける。直前のタイムスタンプを継続行に引き継ぐ。
**S-M2.** `monitors/camera_monitor.py:149-158,427-428` `check_camera_time` がホスト TZ=JST 前提(UTC +9h の naive 値と `datetime.now()` を比較)。TZ=UTC の環境では全カメラが永久に接続不能。aware datetime で比較。
**S-M3.** `unified_server.py:112-119` 起動時マイグレーション失敗を `logger.error` のみで握りつぶし子プロセスを起動・サービス継続。接続に `timeout` 未指定(5 秒)。失敗時は例外再送出。
**S-M4.** `config.py:57-70` `verify_and_initialize_storage` の書込テストが固定ファイル名 `logs/.write_test`。scheduler 起動直後に 6 プロセスが同時実行して衝突、5 回失敗で `LOG_DIR` が `temp_fallback/logs` に落ちるが、`core/logger.py:80` は `BASE_DIR/logs` 固定のため health_watch 等が読む場所と食い違う。`nas_monitor.py:86-93` は一意名で回避済み。
**S-M5.** `core/alexa_verifier.py:56,82-106` 証明書キャッシュが URL 文字列キーで無制限(クエリ付き URL を変えるたびに `requests.get`+登録)。PEM パース/拡張取得の例外が `AlexaVerificationError` でなく router で 500。
**S-M6.** `routers/camera_router.py:74-97`、`services/camera_service.py:217,261,269-270` `target_date` 未検証で glob メタ文字を通す。`target_date="*"` で全期間(30 日×144 本)を 1 本にする数時間の ffmpeg を外部から起動可能(S-H1 のディスク消費も最大化)。`re.fullmatch(r"\d{8}", …)` を要求。
**S-M7.** `services/sensor_service.py:57-83,102-118` タイマー満了後の `to_thread(send_push)` 中に次の検知が来ると、旧タスクの finally が**新タスクの参照**を `MOTION_TASKS` から削除 → 「動きがありました」二重通知+15 分後の誤「止まりました」。`if MOTION_TASKS.get(mac) is asyncio.current_task()` でガード。
**S-M8.** `monitors/nas_monitor.py:394-399`、`scheduler_boot.py:113-121` 保持期間クリーンアップが `hour==8` 依存。scheduler の実行間隔が毎回 3600〜3610s とずれるため、8 時台の実行が無い日はスキップされる。`last_cleanup_date` で判定。

### Low

- **S-L1** `monitors/timelapse_generator.py:232` `getattr(config,'DISCORD_WEBHOOK_REPORT', getattr(…))` は config が常に定義するため第 2 引数が効かない。`or` に。
- **S-L2** `services/switchbot_service.py:107` `fetch_device_name_cache` の呼出元が無く `DEVICE_NAME_CACHE` は常に空 → devices.json に無いセンサーは `Unknown_<mac>`。
- **S-L3** `monitors/camera_monitor.py:619` `ThreadPoolExecutor(max_workers=len(config.CAMERAS))` が devices.json 不在で `max_workers=0` → ValueError で即死。
- **S-L4** `unified_server.py:123` camera_monitor の `Popen` だけ try/except なし。
- **S-L5** `scheduler_boot.py:62-74` `capture_output=True` で最大 1 時間分の stderr をメモリ保持。`env["PYTHONPATH"]=PROJECT_ROOT` が `start_all.sh` の値を上書き。
- **S-L6** `config.py:337,459,513,515,577-579` `int(os.getenv(…))` を try なしで実行。`.env` の誤記 1 つで全プロセスが起動不能。
- **S-L7** `config.py:450-452`+`unified_server.py:172-178` `ALLOW_ALL_ORIGINS=true` で `["*"]`+`allow_credentials=True` の併用。
- **S-L8** `init_unified_db.py:85`、`backup_service.py:41-42` `with sqlite3.connect()` は close しない。`contextlib.closing` を。
- **S-L9** `core/nas_utils.py:40-45` `sudo mount` に `timeout`/`stdin=DEVNULL` が無く永久ブロックしうる(DDD から毎時呼ばれる)。
- **S-L10** `monitors/camera_monitor.py:193-195` 動体検知のたびに NVR 30 日分を CIFS 越しに `glob(recursive=True)`+`getmtime`。当日に絞る。
- **S-L11** `monitors/server_watchdog.py:62` `pgrep -f unified_server.py` は `vim unified_server.py` にもマッチ。

### スキーマ整合性

- `migrations/0000_baseline_schema.sql:207-219` `weather_history` が 0007 の追加列を既に含み、ヘッダ(「0001 適用前の構成」)と矛盾。空 DB 初期化のたびに `duplicate column` WARNING 3 行。
- `current_schema.sql` は 0000 の `CREATE INDEX` 3 つを含まず、逆に baseline に無いテーブル(`haircut_history`, `app_rankings`, `quest_tasks`, `quest_status`, `youtube_subscriptions`)と列を含む。`app_rankings` は `analysis_service.py:375-387` が参照するため空 DB では機能が黙って無効。Issue #330「migrations が唯一の定義元」と並存する第 2 のスキーマ記述であり、位置づけを明記するか CI で自動生成+diff に。
- `core/migrations.py:63` `_split_statements` は単純 `;` 分割。コメント内に `;` を書いた瞬間に起動失敗。`sqlite3.complete_statement` ベースに。

### 品質

- 死にコード: `core/network.py` 全体(`get_retry_session` は POST を 5xx でリトライする非冪等設定)、`switchbot_service.fetch_device_name_cache`。
- ログ出力先の二重定義: `config.LOG_DIR` と `core/logger.py:80` の `BASE_DIR/logs` 固定(S-M4 の根因)。
- `getattr(config, NAME, default)` パターンの誤用が散在(`webhook_router.py:58-61` のコメントが同じ罠を指摘済み)。
- `scheduler_boot.py:33-34` TASKS コメント「30 分」と実値 300s の乖離。
- `post_boot_health_check.py` は `NATURE_REMO_ACCESS_TOKEN` 未設定時に `Bearer None` を送る。

---

## 5. family-quest(フロントエンド)

`any`/`@ts-ignore`/`dangerouslySetInnerHTML` は 0 件、ユーザー由来文字列は JSX テキスト描画のみで XSS 経路なし。

### High

**F-H1. PWA Service Worker の更新戦略が常時起動キオスク(Echo Show)と噛み合わず、デプロイ後に旧バンドル残留/白画面化**
`vite.config.ts:11-37` は `registerType:'autoUpdate'` のみで `virtual:pwa-register` の明示利用なし。`App.tsx:43-44`・`main.tsx:12` の `lazy()` チャンクに対する ErrorBoundary が存在しない。常時表示中の端末は再読込しないので旧 JS のまま動作(2026-09-01 と同型の「旧バンドル×新 API スキーマ」窓が再現)し、新 SW が `skipWaiting` で旧チャンクを precache から削除すると設定ボタン押下時に `import()` が 404 → React 18 はルートごとアンマウントし白画面。
修正: `main.tsx` で `registerSW({immediate:true, onRegisteredSW: 定期 update()})`、`controllerchange` で自動リロード、`lazy` を包む ErrorBoundary でチャンク失敗時に `location.reload()`。

**F-H2. 横画面 4 人パネルで無限クエストのクールダウンが他メンバーにも掛かる**
`App.tsx:185,246-249` の `completedSignal` は `{id, nonce}` のみで user_id を持たず、`FamilyDashboard.tsx:111` が全パネルへ渡し `QuestList.tsx:66` は `id` しか見ない。兄が「食器の片付け(infinite, target all)」を完了すると妹・両親のパネルも 60 秒 "Wait..."。サーバー側クールダウン(`quest_service.py:410`)は (user, quest) 単位。
修正: signal に `userId` を含め `QuestItem` で比較。

### Medium

**F-M1.** `useLongPress.ts:63-69`、`QuestList.tsx:185,190` 長押し取消発火後の `click` を抑止しないため、取消 API+再取得が指を離す前に終わると「直前に取り消したクエストの完了確認モーダル」が開く。
**F-M2.** `gameDataSchema.ts:22-27` `avatar/job_class/role` が `.optional()` のみで `null` を拒否するが、`quest_users` の該当列は NULL 可(`migrations/0001`、`quest_service.py:1028`)。`useGameData.ts:98-113,336-345` は `isError` を捨て `INITIAL_USERS`(「接続エラー」)にフォールバックするため、`role` 無しのメンバーを追加すると全端末が「サーバーに繋がりません」。`.nullable().optional()`、`error` の表示、実レスポンス fixture との契約テスト。
**F-M3.** `App.tsx:331-339` 完了の in-flight 中にカードが再タップ可能で、古い確認モーダルが残り「本日は完了済み」エラーになる。処理中 quest_id 集合でガード。
**F-M4.** `HlsPlayer.tsx:72-75` `MEDIA_ERROR` 以外の fatal で即 `destroy()`。`camera_router.py:66-72` は ffmpeg 起動待ちで 503 を返しうるため、ライブ 4 分割を常時表示している端末でタイルが永久に死ぬ。指数バックオフで `startLoad()`。
**F-M5.** `target_user:'role_*'` の扱いがクライアントとサーバーで矛盾(Q-M3 と同件)。
**F-M6.** `App.tsx:154,209` `currentUserIdx` が範囲外でも補正されず、永続化もされない(PWA 起動ごとにパパに戻る)。`localStorage` に `user_id` を保存し `findIndex` で解決。
**F-M7.** `Modal.tsx:24-30,44-47` 処理中でも背景タップ/ESC で閉じられる(キャンセルボタンだけ `disabled`)。

### Low

- **F-L1** `QuestList.tsx:297-298,315-319` 曜日判定を端末ローカル時刻で二重に行う。サーバーは JST で `day_of_week` フィルタ済みで、端末 TZ≠JST の時間帯に当日クエストが消える。
- **F-L2** `FamilyLog.tsx:56` `key={log.timestamp}` は同秒イベントで重複。`use_item` の記録行(quest_id=0)も混入。
- **F-L3** `apiClient.ts:84-93` 空ボディ/204 → `Unexpected end of JSON input`、ネットワーク断 → `Failed to fetch` が生のままモーダル表示。422 の `detail` 配列は `API Error: 422`。
- **F-L4** `HlsPlayer.tsx:29,89-98` アンマウント時に `onVideoRef(null)` を呼ばず `RecordView.tsx:18-30` に切離済み要素が残る。再生ボタンに busy 状態なし。
- **F-L5** アクセシビリティ: `onClick` 付き div がキーボード不可(`UserStatusCard.tsx:18-21`、`RewardList.tsx:59-61`、`InventoryList.tsx:107-113`、`LiveView.tsx:32-36`、`Card.tsx:46`)。`Modal.tsx` に `role="dialog"`/フォーカストラップなし。
- **F-L6** 横画面では `viewer_user_id` が常に `users[0]` のため `siblings` クエストの欠席ボーナスが Echo Show 上で常に 0。
- **F-L7** `SettingsContext.tsx:17-22` `localStorage` の形状未検証(`iconFirstUserIds` が配列以外だと `App.tsx:601` で例外)。
- **F-L8** `App.tsx:386-474` 一括承認中の個別「承認」タップが `approvingHistoryIdsRef` と連携せず 400。
- **F-L9** `QuestList.tsx:194` 外部 URL(transparenttextures.com)を常時ロード。`Header.tsx:56` の `"Press Start 2P"` は未読込で `cursive` フォールバック。
- **F-L10** `masterData.js` のフォールバッククエスト(999/998)はタップ可能で 404 モーダル。
- **F-L11** `pre_requisite_quest_id` ロックはクライアント判定のみ(Q 側の品質項目と同件)。

### API 契約の不整合・幽霊フィールド

| 箇所 | 内容 |
|---|---|
| `gameDataSchema.ts:22-27` | `avatar/job_class/role` が null 不許容(F-M2) |
| `types/index.ts:15,39,68` | `User.icon`/`Quest.difficulty`/`QuestHistory.date` はバックエンド未送出。`user.icon` は `Header.tsx:115`・`FamilyLog.tsx:39`・`UserStatusCard.tsx:28`・`AvatarUploader.tsx:107` で今も参照 |
| `types/index.ts:67`、`gameDataSchema.ts:67` | `status:'completed'` はサーバーが生成しない値 |
| `types/index.ts:97` vs `models/quest.py:109` | `InventoryItem.desc: string` だがサーバーは `Optional[str]` |
| `useGameData.ts:68-71` | `PurchaseResponse.success` は幽霊(サーバーは `status`) |
| `types/index.ts:36,63` | `quest_id?`/`id?` が optional で `history_id: undefined` → 422 の経路を型が許容 |
| `useGameData.ts:22-28,342-343` | `familyStats`/`adventureLogs` は未使用 |

### テストカバレッジ(vitest)

既存 4 ファイル 22 件(`HlsPlayer` の Safari 経路、`getQuestLockState`、`useOnlineStatus`、`utils`)。ゼロ: `useGameData`(全ミューテーションの invalidate・Zod 検証・`onLevelUp`)、`apiClient`、`App.tsx`(確認フロー・連打ガード・`completedSignal`)、`QuestList`(target/曜日フィルタ・クールダウン)、`useLongPress`、`ApprovalList`、`InventoryList`、`RewardList`、`SettingsContext`、`AvatarUploader`、`gameDataSchema`(実レスポンスとの契約)。最優先は F-M2 の契約テストと F-H2/F-M1 の再現テスト。

### 品質

- `package-lock.json`: ビルドは vite 5.4.21、vitest 4 は同梱の vite 8.2.2(rolldown/oxc)で動作。テストが本番と別バンドラを通っている。メジャーを揃える。
- `target_user` 判定が `QuestList.tsx:302-313`・`FamilyDashboard.tsx:67-77` に重複、`extractErrorDetail` が 3 箇所に重複。
- `FamilyDashboard.tsx:16` `FAMILY_ORDER=['dad','mom','son','daughter']` のハードコード(サーバーが既に `quest_data.USERS` 順で返す)。
- 死蔵: `src/App.css`、`src/assets/react.svg`、`public/silent.mp3`。`masterData.js` は `allowJs` で無型。
- `types/index.ts:6` `ID = number|string` の緩さ。サーバーは常に int。

---

## 6. DDD(バッチ)

### High

**D-H1. NAS 障害時に全キャスト再通知ストーム+`sudo mount`/Discord 通知が数百回発火する**
`DDD/newface_monitor.py:1218-1234,1524-1619,1702,2213`。`DataManager` の全メソッドが呼出ごとに `MonitorConfig.get_data_dir()` → `core.nas_utils.get_managed_target_directory()` を実行(79 サイトで 1 実行あたり 240 回超)。NAS 未マウント時は呼出ごとに `sudo mount`(timeout なし)+`logger.error`(DiscordErrorHandler 経由)+`send_push(channel="error")`。さらに `wait_for_storage_warmup(data_dir)` は `get_data_dir()` がローカルフォールバックを返して mkdir 済みのため必ず通過し、ローカルに `known_casts_*.json` が無いので**全サイト×全在籍キャストを Discord へ通知**(`:2106,2129`、`MASS_DETECTION_WARNING_THRESHOLD` は known が空だと発動しない)。`extract_youtube_urls.py:441-444` は同問題を #243/#123 で修正済み(1 回だけ評価+フォールバック中は中断)だが newface には未適用。
修正: `_run_monitor_locked` 冒頭で `data_dir` を 1 回だけ解決して `DataManager` に渡し、フォールバック中は run 全体を中断。

**D-H2. 一時的な I/O エラーで正常な known_casts ファイルを「破損」として隔離**
`DDD/newface_monitor.py:1512,1540-1552`。`_LOAD_ERRORS=(OSError, ValueError, TypeError, KeyError)` を捕捉後、種別を問わず `.corrupted-*` に rename。CIFS 瞬断(`wait_for_storage_warmup` の docstring 自体が想定)で `open()` が失敗しただけで中身が正しいファイルが退避され、`.bak` が無ければ空集合 → 全キャスト再通知、以降 union 保存で隔離前データは戻らない。
修正: 隔離は `JSONDecodeError`/`UnicodeDecodeError`/`TypeError`/`KeyError` に限定し、`OSError` は当該サイトをスキップ。

### Medium

**D-M1.** abce340「消失サイト連続失敗アラート」のエッジケース: (1) `_check_site:2087-2091` は `RequestException` のみ失敗扱いのため、200 を返す消失サイト(別ドメインへ 302 → 200)は永久に検知されず毎時 WARNING のみ。(2) Webhook 未設定/失効時は `alerted` が立たず(`:2054-2058`)毎時 ERROR 発報が続く。(3) Pi 側回線断で 79 サイトが同時に閾値到達すると 79 件一斉送信。(4) `record_site_failure`→`mark_site_failure_alerted` が別々の load/save(`:1758-1775`)。(5) `load_site_failures:1723` はエントリが dict かを検証せず、不正値で毎時 CRITICAL。
**D-M2.** `core/logger.py:46-48` デーモンスレッド送信のため cron 短命プロセスで終了間際の ERROR が失われる(S-H3 と同根)。
**D-M3.** `batch_download_discord.py:205-212,266-273` `_is_bot_detection_error` の `"sign in to confirm"` が年齢制限メッセージ("Sign in to confirm your age")にも一致 → 1 本で 12 時間クールダウン。
**D-M4.** `:774-786,833-834,992` セグメント取得にリトライがなく、1 セグメントの一時失敗で数 GB を `rmtree` して破棄、3 回で実行中断。再開機構なし。
**D-M5.** `services/notification_service.py:40-59` 経由の Discord 送信に 2000 字切詰めがなく(スタンドアロン版 `:101` は `[:2000]`)、`詳細: {e}` の長い例外文で 400 → サーキットブレーカーが誤って開き以後の CRITICAL も無言スキップ。
**D-M6.** `:871-875,992` クラッシュ時の `tmp_fragments/*.fragments.tmp` が SD カードに永久残留(同一動画の再試行時のみ削除)。`_run_locked` 冒頭で一掃。

### Low

- **D-L1** `:550` `outtmpl` にディレクトリ名を埋め込み、source_name に `%` が含まれるとテンプレートエラー。`paths.home` を使う。
- **D-L2** `:579,585` `extract_info` 後に `download()` でメタデータを 2 回取得(ボット検知対策を自ら弱める)。
- **D-L3** `:1001-1007` SIGINT/SIGTERM をフラグ化するだけで進行中の数 GB ダウンロードは止まらない。
- **D-L4** `:1161-1164` ロック競合時 `sys.exit(1)` で `run_task.sh` が ERROR を記録(newface は正常 return)。
- **D-L5** `:676-696` packer 解除で radix を無視し base36 固定。
- **D-L6** `newface_monitor.py:1364,1347` embed title(256)/field(1024)の上限チェックなし。
- **D-L7** `:1600-1604` `.bak` 更新が非アトミック。**D-L8** `:1593` 読戻し検証失敗時 `.tmp` が残る。**D-L9** `:2129-2130` ブレーカーで送れなかったキャストも日次サマリに計上。
- **D-L10** `extract_youtube_urls.py:364-367` `FileManager.save` が非アトミック。
- **D-L11** `:418-424,464` `youtube_subscriptions` への INSERT を行うコードがリポジトリに無く crontab にも未登録で `--cron` は事実上デッド。DB は `/mnt/nas/home_system/youtube_extractor/home_system.db` で本体と同名の別ファイル。
- **D-L12** `newface_monitor.py:122` `AGE_PATTERN` が `(85)` 単独を年齢と誤検知。**D-L13** `split_prompts.py:105` 既定入力が個人固有ファイル名。

### 品質

- Discord 送信実装が 4 系統(batch `DiscordNotifier`+standalone、MY_HOME_SYSTEM `_send_discord_webhook`、newface `DiscordNotifier`、`core.logger.DiscordErrorHandler`)で 2000 字・429・ブレーカーの扱いが全て異なる(D-M2/D-M5/S-H3 の原因)。共通 `post_discord_text()` へ。
- `MonitorConfig.SITES` が約 970 行の Python リテラル(`:206-1174`、79 サイト)。JSON/YAML へ外出し。
- 巨大関数: `_parse_html`(`:1850-2022`、約 170 行・try ネスト 6 段)、`_download_with_ytdlp`(`:857-992`)、`_run_locked`(`:1174-1286`)。
- プロジェクトルート解決が 3 スクリプトで異なり、batch は `run_task.sh` の PYTHONPATH に暗黙依存(`:66-83`)。
- `DDD/requirements.txt` が pip-audit の対象外で全て `>=` 未固定。

---

## 7. CI / テスト / ドキュメント / デプロイ

### High

**C-H1. pip-audit 週次監査は検知 0 件のとき毎週「チェック失敗」Issue を立て続ける(実機再現済み)**
`.github/workflows/pip-audit-weekly-audit.yml:44,50-63`。`pip-audit -f markdown -o file` は脆弱性 0 件のとき**ファイルを生成しない**(stdout に "No known vulnerabilities found"、exit 0)。`readFileSync` が例外 → "チェック自体が失敗しました" → `isClean=false` → Issue 作成。Issue #324 で「現時点の検知は 0 件」なので初回から誤 Issue。
修正: 実行後に `[ -f report ] || echo "No known vulnerabilities found" > report`、または exit code(0=clean/1=vuln/他=エラー)を `$GITHUB_OUTPUT` に記録して判定。`test.yml:187` の同型ステップにも注記。

**C-H2. `--cov-fail-under=45` は実質無意味、`.coveragerc` の omit が「テスト済みモジュール」を大量に隠している**
`test.yml:118`、`MY_HOME_SYSTEM/.coveragerc:3-14`。CI 公式ビュー 87%(3,639 stmts)に対し閾値 45%。omit を tests のみにした真値は 63%(7,118 stmts)、omit 分 3,479 stmts の実カバレッジは 38%。しかも `monitors/nas_monitor.py`(64%)、`health_watch.py`(64%)、`switchbot_power_monitor.py`、`daily_timelapse_job.py`、`smart/timelapse_generator.py`、`camera_monitor.py`、`network_logger.py`、`nature_remo_monitor.py`、`views/dashboard/misc_tab.py`、`services/train_service.py`、`sync_strict.py`(83%)、`reset_game.py`、`switchbot_webhook_fix.py`、`post_boot_health_check.py` は**専用テストファイルが存在するのに omit されている**。`old/*` は存在しないディレクトリ(`test_coveragerc.py` はワイルドカード付きエントリを検査対象外にしているためすり抜け)。
修正: omit を `tests/*`、`__init__.py`、本当に実行不能なもの(`dashboard.py` 等)に絞り、閾値を真値基準で 60 → 段階的引上げ。

**C-H3. DDD テストのパスフィルタは DDD が import している MY_HOME_SYSTEM 側の変更を見ていない**
`test.yml:74-96`。`newface_monitor.py:33-47`/`extract_youtube_urls.py:29-35` は `core.logger`/`core.nas_utils`/`core.utils`/`config` を直接 import するが、フィルタは `git diff -- DDD/` のみ → `core/*`・`config.py` の変更 PR で DDD テストが黙ってスキップ(直近 #351 が該当)。`| grep -q .` は `pipefail` 無しで `git diff` 失敗も "changed=false" に倒れる。
修正: pathspec に `MY_HOME_SYSTEM/core/ MY_HOME_SYSTEM/config.py MY_HOME_SYSTEM/common.py` を追加、`set -o pipefail`。

### Medium

**C-M1.** `.github/workflows/claude-review.yml:20-23,36` `anthropics/claude-code-action@v1` のみ可変タグ(他は SHA ピン留め済み)、かつ `id-token: write`/`pull-requests: write`+OAuth トークン付きで PR diff(プロンプトインジェクション可能)を処理し `gh pr comment` を許可。SHA ピン留め、権限最小化。
**C-M2.** Dependabot/Renovate なし。`pip-audit-weekly-audit.yml:40` `pip install pip-audit` 未固定、`DDD/requirements.txt` 全て `>=`(監査対象外)、`npm audit` 相当なし。`dependabot.yml` で actions/pip×2/npm を登録、DDD を `==` 固定し週次監査対象に追加。
**C-M3.** `test.yml:203-224` フロント CI に `npm run lint` が無い(現状 0 エラーなのでコストなしでゲート化可)。
**C-M4.** `.github/scripts/test_check_spec_drift.py` はどの CI でも実行されず、`:213-242` で `ai_logic.md`/`bounty_router.md` が**孤立していること**を assert している。週次監査で孤立 14 件(廃止機能 11 件: BossCard/GuildBoard/EquipmentShop/FamilyMileageCard/WeeklyTrends/FamilyParty/ShopContainer/AdminDashboard/BattleEffect/LevelUpModal/gameHelpers、他 `scripts_claude_log_watchdog.md`/`bounty_router.md`/`ai_logic.md`)。片付けると回帰テストが壊れる逆インセンティブ。テストを `tmp_path` 疑似リポジトリ方式にし、lint ジョブで `pytest .github/scripts/` を実行、孤立 14 件は削除または廃止一覧に統合。
**C-M5.** ツールチェーンのバージョンドリフト: CI Node 20 / `@types/node ^26`(Node 20 に無い API を検知できない)/ `engines`・`.nvmrc` なし。vite 5.4 でビルド、vitest 4 は vite 8.2(rolldown/oxc)で実行。Python は CI 3.11 固定だが `.python-version` なし。
**C-M6.** `tests/test_quest_service.py:22-33,44-55` `setUp` で `config.SQLITE_DB_PATH="test_home_system.db"`(CWD 相対の実ファイル)を代入し `tearDown` で復元しない。毎テスト `setup_logging` でハンドラ累積。他の直接代入箇所(`test_core_database.py:36`、`test_quest_authorization.py:27,52`、`test_webhook_router.py:42-44`)は復元済み。
**C-M7.** `config.py` を経由しない環境変数読取が 3 箇所: `services/camera_service.py:214,253`・`monitors/camera_monitor.py:183`(`NVR_RECORD_DIR`)、`switchbot_webhook_fix.py:123`(`WEBHOOK_BASE_URL`)。`test_env_example_consistency.py` は `config.py` のみ走査のため `.env.example` に無いことを検知できない(実際に無い)。
**C-M8.** `.gitignore`: `MY_HOME_SYSTEM/uploads/` 未除外(`.webp`/`.gif` は NOT ignored → 家族のアバター写真がコミット候補)。`*.csv`/`*.ts`/`*.png`/`*.txt`/`*.json` の全域除外+打消し 6 箇所は #187/#330 と同型の事故が構造的に再発する。`.env.*`(`.env.local` が NOT ignored)、`.DS_Store` 欠落。
**C-M9.** `check_spec_drift.py`: `:221` rename(`R`)で旧パスを捨て孤立化を検知しない、`:265-267` `git log` に `--follow` が無く rename 直後は恒久的ドリフト誤検知、`:276,:296` `rglob` で gitignore 済みファイルも「未文書化」報告(`git ls-files` に)、`spec-drift-pr-check.yml:41-42` `github.sha` と base.sha の比較は base 進行時に誤検知(`merge-base` に)。

### Low

- **C-L1** 全ワークフローに `timeout-minutes` なし(既定 360 分)。
- **C-L2** 実時間 sleep 依存テストが 49 秒中約 28 秒: `test_smart_timelapse_generator.py:289`(10s、`smart_timelapse_generator.py:157` の `time.sleep(5)` 未 patch)、`test_switchbot_power_monitor.py`(4s×2)、`test_nas_monitor.py:296` 他、`test_logger.py:39`。
- **C-L3** 壁時計依存: `test_quest_service_edge_cases.py:319-328`、`test_analysis_service.py:189,230,324`(月初 1 秒窓、naive `now()`)。freezegun は dev 依存に既にある。
- **C-L4** assert の無い「例外が出ないこと」テスト 11 件。特に `test_line_handler_dispatch.py:168/245` は握りつぶしの検証(`logger.exception` 呼出等)が無い。
- **C-L5** `test_empty_db_e2e.py` と `test_h2_fresh_db_e2e.py` のフローがほぼ同一。
- **C-L6** `requirements.txt:44,71,86-87` `iniconfig`/`pluggy`/`pytest`/`pytest-asyncio` が本番依存に混入、`:121` `psutil>=5.9.0` のみ未固定。`japanize-matplotlib`/`onvif_zeep`/`sgmllib3k` は sdist のみで legacy `setup.py` ビルドが必要 — 本環境(Debian パッチ済み setuptools)では `install_layout` AttributeError で `pip install -r` 全体が失敗(venv+最新 setuptools で回避)。実機の OS 更新でも同じ壊れ方をしうる。
- **C-L7** `test_camera_monitor_low_priority.py:54` 実 `/tmp` を glob(並列実行で偽陽性)。
- **C-L8** `deploy/systemd/home_system.service:7-12` `Type=oneshot`+`RemainAfterExit`、`Restart=` コメントアウトでクラッシュ時に再起動しない(監視は `server_watchdog.py` 頼みでそのカバレッジは 0%)。`deploy/cron/crontab:6,9,26` 04:00 に 3 ジョブ同時起動、`:14` `rclone sync >/dev/null 2>&1` でエラー黙殺。
- **C-L9** シークレット実値の検出なし。ユーザー名・LAN IP・`.env.example:90` の小児科予約 URL は public 化時に要処理。
- **C-L10** `README.md:24-27` の CI 説明が `claude-review.yml`/`pip-audit-weekly-audit.yml` を含まない。

### カバレッジ: 真値ワースト 15(tests/ と `__init__.py` のみ除外)

| ファイル | Stmts | Miss | Cover |
|---|---:|---:|---:|
| dashboard.py | 78 | 78 | 0% |
| monitors/memory_monitor.py | 97 | 97 | 0% |
| monitors/server_watchdog.py | 107 | 107 | 0% |
| monitors/timelapse_runner.py | 36 | 36 | 0% |
| monitors/tv_lock_monitor.py | 34 | 34 | 0% |
| views/dashboard/log_tab.py | 89 | 89 | 0% |
| views/dashboard/quest_tab.py | 38 | 38 | 0% |
| views/dashboard/sensor_tab.py | 77 | 77 | 0% |
| views/dashboard/summary.py | 151 | 151 | 0% |
| reset_game.py | 112 | 94 | 16% |
| monitors/log_analyzer.py | 107 | 88 | 18% |
| monitors/camera_monitor.py | 378 | 291 | 23% |
| core/sound_manager.py | 61 | 46 | 25% |
| monitors/network_logger.py | 130 | 79 | 39% |
| services/train_service.py | 81 | 47 | 42% |

ディレクトリ別: `views` 10% / `monitors` 41% / ルート直下 67% / `services` 84% / `core` 85% / `handlers` 87% / `routers` 92% / `models` 100%。本番の自己修復を担う `server_watchdog.py`・`tv_lock_monitor.py`・`memory_monitor.py` が 0% で、omit によって CI から不可視になっている点が C-H2 の本質。

---

## 8. 推奨対応順序

1. **即時(1 PR ずつ、小さく)**: Q-H1(キャンセル時の残高不足拒否)、L-H1(`set_authorizer` または引用符拒否+回帰テスト)、C-H1(pip-audit ファイル未生成対策。次回週次実行前に)、S-M6(`target_date` 検証)、Q-M1(`use_item` 条件付き UPDATE)。
2. **運用安定(1〜2 週)**: S-H1(VOD 保持期間)、D-H1/D-H2(newface の NAS フォールバック中断・隔離条件限定。extract 側の既存パターン移植)、S-H3+D-M2+D-M5(Discord 送信の共通化・2000 字切詰・atexit join)、S-H2(プロセスツリー清掃)、S-M1(health_watch の継続行)。
3. **CI の検知力回復**: C-H2(omit 縮小+閾値引上げ)、C-H3(DDD フィルタ拡張)、C-M3(lint ゲート)、C-M4(孤立仕様書 14 件の整理+ドリフトテストの CI 化)、C-M2(Dependabot)。
4. **フロント**: F-H1(SW 更新+ErrorBoundary。2026-09-01 障害の再発防止として最優先)、F-H2/F-M1/F-M3(連打・シグナル系)、F-M2(Zod nullable+契約テスト)。
5. **設計判断が必要なもの(Issue 化推奨)**: L-H2(LINE ID マッピングを実装するか機能削除か)、Q-M2(role 不明時の扱い)、Q-M3/F-M5(`role_*` サポートの存廃)、D-L11(`--cron` の存廃)、`current_schema.sql` の位置づけ、C-L8(systemd の `Restart=`)。
