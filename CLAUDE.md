# CLAUDE.md

このファイルは、このリポジトリで作業する Claude Code (claude.ai/code) に向けたガイダンスを提供する。

## リポジトリ概要

これは、共有のSQlite DB・NASマウント・REST APIを介して連携する3つの独立したサブシステムからなる個人用モノレポである。ルートレベルのビルドは存在せず、各サブシステムはそれぞれのディレクトリ内で個別に開発・テストされる。

| サブシステム | パス | スタック | 役割 |
| --- | --- | --- | --- |
| バックエンドコア | `MY_HOME_SYSTEM/` | Python 3.11, FastAPI, SQLite | IoT機器の制御、環境ロギング、LINE/Discord通知、Family Quest API |
| フロントエンド | `family-quest/` | React 18, TypeScript, Vite, Tailwind | RPG風の家族タスク/クエスト管理PWA（バックエンドが配信） |
| バッチ処理 | `DDD/` | Python, yt-dlp | 他とは無関係な動画・コンテンツのスクレイピング・ダウンロード自動化 |

`docs/specifications/` には、ソースファイル1つにつき1つのリバースエンジニアリング済みMarkdown仕様書が格納されている（詳細は後述の「仕様書ドリフト規約」を参照）。個別のファイルを読み込む前にまずここで概要を確認するとよい。サブシステム間をまたぐ全体アーキテクチャ・データフローは `docs/specifications/全体設計書.md` を参照。

## コマンド

### MY_HOME_SYSTEM (バックエンド)

`MY_HOME_SYSTEM/` から実行する:

```bash
pip install -r requirements.txt -r requirements-dev.txt

# 依存の追加・変更は requirements.in / requirements-dev.in(直接依存のみ)を編集し、
# pip-compile で lock(requirements.txt / requirements-dev.txt)を再生成する(手で編集しない。Issue #647)
pip install pip-tools
pip-compile --strip-extras --no-emit-index-url --output-file requirements.txt requirements.in
pip-compile --strip-extras --no-emit-index-url --output-file requirements-dev.txt requirements-dev.in

# 全テストスイート実行 (pytest.iniの asyncio_mode=auto によりasyncテストも自動実行される)
python -m pytest tests/ -v

# 単一ファイル / 単一テストのみ実行
python -m pytest tests/test_quest_service.py -v
python -m pytest tests/test_quest_service.py::test_something -v

# カバレッジ付き (CIの閾値と同じ)
python -m pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=84

# Lint (CIでブロックされるのは未定義名・構文エラーのみ。フルレポートは参考情報)
ruff check . --select F821,F822,F823,E9
ruff check .

# セキュリティスキャン
bandit -r . -x ./tests -lll

# サーバーを直接起動 (開発用)
python unified_server.py   # 0.0.0.0:8000 でバインド
```

テスト実行には以下の環境変数が必要 (CIでは設定済み。ローカルでも設定すること):
```bash
export SQLITE_DB_PATH=":memory:"
export NAS_MOUNT_POINT="./tmp_nas"
export NOTIFICATION_TARGET="none"
```

`tests/conftest.py` は `isolated_db` フィクスチャ（テストごとに新規SQLiteファイルを作成し `init_unified_db.init_db()` でスキーマ初期化、`config.SQLITE_DB_PATH` をmonkeypatch）と `api_client` フィクスチャ（`unified_server.app` に対する `TestClient` で、`lifespan` コンテキストを**実行しない**ため、カメラ監視/スケジューラ等のバックグラウンドサブプロセスがテスト中に一切起動しない）を提供する。新規テストはこれらのフィクスチャを優先して使うこと。`lifespan` そのもの（マイグレーション適用・NASプリウォーム・監視子プロセスの起動/死活監視・シャットダウン処理）を検証したいテストには `lifespan_client_factory`（`with lifespan_client_factory(migration_error=...) as (client, spawned):` の形で使い、`spawned` は `_spawn_child_process` を差し替えた偽の子プロセス。シャットダウン後の `terminate` 等を assert できるよう `with` はテスト側が張る）と、その既定引数版の `api_client_with_lifespan` を使う（Issue #756。実プロセスは起動しない）。これらより古いテストファイルは、各自の `setUp`/`setup_method` 内で `config.SQLITE_DB_PATH` の上書きと `init_unified_db.init_db()` をコピペするパターンを使っているが、挙動を変えるリスクを避けるため、既存ファイルを編集する際はついでにリファクタリングせず、そのファイル内の既存パターンにそのまま従うこと。

日付・時刻に依存するテストは実時刻に任せず `freezegun` の `freeze_time` で固定すること（Issue #658）。タイムゾーン依存は `core/utils` の JST 固定ヘルパー（`get_now_jst`/`get_today_date_str` 等）で解消済みで、TZ を UTC/America/New_York/Pacific/Honolulu/Pacific/Kiritimati に変えても全テストが通ることは確認済みだが、「JST の日付が変わる瞬間」に走ると結果が変わる判定（`tv_lock_monitor` の深夜2時、YouTubeごほうび券の施行日 `YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM` 等）は残る。境界そのものの回帰テストは `tests/test_jst_day_boundary.py` にある。

`conftest.py` は `config` がロードされる**前**に、import時点でDiscord/LINEのwebhook・トークン系環境変数をすべて空文字にしている。これは、実際の認証情報が入ったローカル `.env` によってテストが本物の通知を発火させてしまった事故が過去にあったため。`notification_service`/`line_service` の経路を通るテストを書く際は、この仕組みを回避しないこと。

### family-quest (フロントエンド)

`family-quest/` から実行する:

```bash
npm run dev      # HMR付きVite開発サーバー
npm run build    # tsc -b && vite build -> dist/
npm run lint     # ESLint
```

ビルド成果物 `dist/` はバックエンドが直接配信する (`unified_server.py` が `QUEST_DIST_DIR`、デフォルトは `../family-quest/dist`、を `/quest` にマウントする) — **ビルド完了 = デプロイ完了**であり、別途のデプロイ/再起動手順は不要。`./deploy.sh` がビルドを実行し（`dist.next/` にビルドして検証し、成功時だけ `dist/` と rename で入れ替えるアトミック方式。ビルド中・失敗時も旧 `dist/` が配信され続ける — Issue #650。Node のメジャーが `package.json` の `engines` 範囲外なら失敗、`.nvmrc` と異なれば警告する）、成功時にビルド元のgitツリーハッシュを `dist/.built-tree` に記録する。`./deploy.sh --if-stale` はそのハッシュがHEADの `family-quest` ツリーと一致すればビルドをスキップする冪等モードで、リポジトリ管理の `deploy/git-hooks/post-merge` フック（`MY_HOME_SYSTEM/start_all.sh` が起動時に `core.hooksPath` として冪等に登録する。以前は `.git/hooks/` へのローカル設置でclone後に再設置が必要だった）が `git pull` のたびに、また `MY_HOME_SYSTEM/start_all.sh` がサーバー起動前に、これを呼び出す（`git reset --hard` 等のpull以外の経路で更新された場合でも、次のサーバー起動時にビルド漏れが回収される。2026-09-01のAPIスキーマ不整合障害の再発防止）。

### DDD (バッチ処理)

`DDD/` から実行する: `pip install -r requirements.txt` の後、スクリプトを直接呼び出す（例: `python batch_download_discord.py`）。テストは `python -m pytest -v --cov=. --cov-fail-under=74`（`DDD/.coveragerc` がテスト自身と `conftest.py` を集計から外す。Issue #665）。Ruff/Bandit はMY_HOME_SYSTEMと同様の方式（後述のCI参照）で、このディレクトリを対象に実行される。

### CI (`.github/workflows/test.yml`)

4つの独立したジョブがある: `lint`（`MY_HOME_SYSTEM`・`DDD`両方にruffを実行、PRをブロックするのは `F821,F822,F823,E9` と、merge-base 比で**増えた**指摘（`.github/scripts/check_lint_ratchet.py`。既存指摘は対象外）。加えて `MY_HOME_SYSTEM` の pyright（`MY_HOME_SYSTEM/pyrightconfig.json` で未定義名・未束縛変数・await漏れ等の明確なバグ診断だけを error にした設定。Issue #532。`typeCheckingMode` は `off` のままだが、Issue #743 で `reportOptionalMemberAccess`/`reportOptionalSubscript`/`reportOptionalIterable`/`reportOptionalOperand` も error に追加した — 「`None` になりうる値をそのまま使う」は過去に何度も500エラーとして踏んできた失敗モードで、サードパーティのスタブ不足起因の診断を含まないため。診断を追加するときは同ファイルで有効化し、**診断の追加と既存の指摘の修正を必ず同一コミットで行う**こと）と、全シェルスクリプトの shellcheck、および `pytest .github/scripts/`（`check_spec_drift.py` 等の回帰テストに加え、仕様書の「根拠」行番号引用が実ソースの定義位置と一致するかを検証する `check_spec_line_refs.py` のゲートを含む。ずれた場合は `python3 .github/scripts/check_spec_line_refs.py --fix` で一括追従できる。ゲートの対象は「行番号がどこを指しているか機械的に確定できる」引用、すなわち (1) `def`/`class` で始まる抜粋の引用（`ast` が要るため Python のみ）と、(2) 抜粋がソース内で**一意**に決まる式・文の引用（AUDIT-019 で追加。文字列の照合だけなので `.py`・`.sh`・TypeScript すべてが対象）。(2) に入るのは「単一行の引用かつ抜粋の先頭行が一意」と「範囲引用かつ抜粋が複数行で両端とも一意、かつ抜粋の行数が引用範囲の幅と一致する」の2つ。抜粋が一意でない/ソースに見つからないもの、範囲引用に単一行の抜粋が付いたもの、範囲の幅が抜粋より広いものは非ゲートで、`--report` で件数を見るのみ(Issue #655。保証範囲は `docs/specifications/README.md` を参照)。抜粋の書き方は「行番号の後ろ」(`(行番号: 28 / 抜粋: "def foo(")`)と「行番号の前」(`` `def foo(...)` (行番号: 28) ``)の2通りとも解析する(Issue #679。以前は前者だけで後者87件が無言で素通りしていた)）もブロッキングで実行する）、`test`（`MY_HOME_SYSTEM` のpytest + カバレッジ、固定閾値 `--cov-fail-under=84` を床として、PRでは master の直近成功 run の実測から 0.5pt を超えて下がると失敗するラチェット `.github/scripts/check_coverage_ratchet.py` も走る。ラチェットは `pull_request` でしか走らず比較元の成果物が取れなければスキップされるため、床はそのフォールバックとして実測に追従させる（Issue #494/#536 と同じく「実測より約3〜4pt下」へ段階的に引き上げる）。続けて `DDD` のpytestも常に実行し、こちらも `DDD/.coveragerc` に基づくカバレッジを計測して床 `--cov-fail-under=74` と同じ 0.5pt ラチェット（`--label ddd`、成果物は `ddd-coverage-report`）でゲートする）、`security`（bandit + pip-audit、ブロックするのはbanditのHigh severity（`MY_HOME_SYSTEM`・`DDD`とも）と、merge-base 比で増えた Medium 以上（severity・confidence とも `-ll -ii`）の指摘のみ）、`frontend`（`family-quest` で `npm ci && npm run lint && npm run build && npm test`、つまりESLint・`tsc -b` によるTSの型チェック・Vitestがゲートになっており、カバレッジはバックエンドと同じ master 比ラチェットに加え、`vitest.config.ts` の `coverage.thresholds` が固定の床として効く（Issue #495 で「まず可視化に徹する」として閾値を置かなかったが、実測が出たのでバックエンド・DDD と同じく床を設けた））。ラチェット系のチェックは `pull_request` イベントでのみ走り、比較元が取得できない場合（成果物の保持期限切れ等）はスキップされる。もう2つのワークフロー（`spec-drift-pr-check.yml`、`spec-drift-weekly-audit.yml`）は `.github/scripts/check_spec_drift.py` を実行するが、検知結果に関わらず**常に非ブロッキング**（exit 0）である。`dashboard-mobile-e2e.yml` は、ダッシュボード関連ファイルを触ったPRでだけ Streamlit を実際に起動し、Chromium の 390x844 ビューポートでレイアウトの不変条件（横方向のはみ出し・固定ヘッダーとの重なり・タブが画面内に収まるか・タップターゲット44px等）を検証する（`MY_HOME_SYSTEM/tests/test_dashboard_mobile_e2e.py`。既定ではスキップされ、`DASHBOARD_E2E=1` のときだけ実行される）。**常に非ブロッキング**（`continue-on-error: true`）で、崩れはスクリーンショットの成果物で確認する。playwright は `requirements-dev.txt` には入れずこのジョブでのみ入れる（実機の `.venv` とブロッキングの test ジョブに、ここでしか使わない依存とブラウザを持ち込まないため）。`dependabot-auto-merge.yml` は Dependabot の minor/patch 更新PRに対して GitHub の auto-merge を有効化する（マージ自体は required status checks が全て成功した時点で GitHub が行う。メジャー更新は対象外。branch protection の required checks と「Allow auto-merge」設定が前提で、PRが `BLOCKED` 以外の状態なら安全側に倒して何もしない。詳細は同ファイル冒頭のコメントと `docs/runbooks/issue_509_repo_settings_and_branch_cleanup.md`）。ドキュメントに転記された `--cov-fail-under` の値は `.github/scripts/test_docs_ci_consistency.py` が test.yml と突き合わせる。`pip-audit-weekly-audit.yml` は `MY_HOME_SYSTEM/requirements.txt`・`requirements-dev.txt`・`DDD/requirements.txt` の3ファイル（`security` ジョブの pip-audit は `MY_HOME_SYSTEM/requirements.txt` のみが対象で、両者は同じ対象ではない。Issue #400 で週次監査側の対象を拡張した）を週次で監査し（MY_HOME_SYSTEMの2ファイルとDDDは別々の `.venv` で動く独立環境で、同じパッケージを異なるバージョン制約で固定しうるため、1回の `pip-audit` 呼び出しにまとめず環境ごとに個別に実行して結果を結合する。まとめると `ResolutionImpossible` で毎週「チェック自体が失敗」のIssueが立つ）、検知が1件以上あれば `pip-audit-audit` ラベルのIssueを自動起票/更新する（Issue #324: PRのCIを常時安定させるため、pip-audit自体はブロッキング化せず定期監査Issue方式を採用）。GitHub Actions の外側では、Claude Code の Routine（定期起動セッション）として「週次リポジトリ品質監査」（前週の master 差分をレビューして Issue 起票）と「週次仕様書ドリフト解消」（Issue #20 の検知を `spec-drift-sync` スキルで追従して Draft PR 作成）が毎週月曜に動く。スケジュールとプロンプトの正は `docs/runbooks/claude_routines.md`。

## アーキテクチャ

### MY_HOME_SYSTEM: リクエストフローとレイヤリング

`unified_server.py` が唯一のFastAPIエントリーポイント。起動時 (`lifespan`) に未適用のSQLマイグレーションを適用し、その後 `monitors/camera_monitor.py` と `scheduler_boot.py` を（asyncioタスクではなく）**別プロセス**として起動し、30秒ごとに死活監視して予期せず終了していれば再起動する（1時間に5回を超えたらクラッシュループとみなして停止しCRITICALを通知。`restart_dead_children`、Issue #646） — これが `tests/conftest.py` の `api_client` フィクスチャが `lifespan` の実行を一切避けている理由である。実機では `deploy/systemd/home_system.service`（`Type=simple` + `Restart=on-failure`）が `unified_server.py` 本体をフォアグラウンドで管理し、`start_all.sh --prepare` は `ExecStartPre` の前処理（旧プロセス掃除・NAS待機・`.venv`/`dist` 鮮度チェック）のみを担う。ダッシュボードは `home_dashboard.service` で別管理。カメラ3台の常時録画は `nvr-{entrance,garden,parking}.service`（同じく `deploy/systemd/`。ffmpeg が NAS へ10分ごとのMP4を直接書き、RTSP認証情報だけは実機の `/etc/nvr/<カメラ>.env`(600) に置きリポジトリには含めない — Issue #773）が担う。ルーターは薄く作られており、`routers/*.py` はリクエストのパース・検証のみを行い、ロジックは `services/*.py` に委譲し、そこから永続化のために `core/database.py` を呼ぶ。新規エンドポイントを追加する際は、ロジックをルーターに直接書かずこのレイヤリングに従うこと。

新規エンドポイントに関わる独自ミドルウェアが2つある:
- `ip_restriction_middleware` は非プライベートネットワークからのリクエストをログに記録するが（ブロックはしない）、`/webhook/switchbot`・`/callback/line`・`/webhook/alexa` は外部からのトラフィックを受け付ける必要があるため無条件に許可している（`allowed_webhook_paths`。このリストは同時に「エッジのCloudflare Access側でバイパス設定が必要なパス」の一覧でもあり、設定漏れがあるとWebhookがサーバーまで届かない — Issue #517。新しい外部Webhookを追加したら必ずここに追記すること。`tests/test_unified_server_app.py` がマウント済みルートとの一致を検証する）。**これは意図的な設計として正式に確定している**（Issue #321・2026-09-03決定）: アプリ層では`Cf-Access-Jwt-Assertion`のJWT検証を行わず（一度PR #80で実装したが2026-08-28の障害でrevert済みで、再実装しない方針を採用）、外部アクセス制御はエッジのCloudflare Accessに委譲する。この設計は、オリジンへの直接到達がCloudflareのIPレンジ経由に限定されていること（ルーター/FW側の設定）を前提とする。
- CORSの許可オリジンは `config.CORS_ORIGINS` の1箇所だけに存在する（環境変数 `ALLOW_ALL_ORIGINS=true` で `["*"]` に上書き可能）。`unified_server.py` 側に別のハードコードされたオリジンリストを追加しないこと — 過去に2つの別々のリストが存在し、片方しか実際には反映されていないというバグがあった。

Family Quest APIの認可設計にも既知の妥協点が1つある: リクエストボディ中の `user_id`/`approver_id` はクライアント入力のまま信頼しており、サーバー側でセッション等から認可を検証していない。これは個人用IoTシステム・単一プロセス・LAN内を信頼境界とする前提のもとで「意思決定によりスコープ外」と合意済みの設計（`docs/reports/CODE_REVIEW_REPORT_ALL.md` Critical#1、2026-09-02棚卸し課題4）であり、追跡はIssue #614で行う。この判断を覆す場合は、LAN外からの到達経路（Cloudflare Access委譲の前提、上記参照）とセットで再検討すること。

ダッシュボード（Streamlit、8501番をlocalhost束縛）は `routers/dashboard_router.py` が `config.DASHBOARD_BASE_PATH`（既定 `/dashboard`）配下へ中継して配信する。同じベースパス配下に、Streamlit を介さない読み取り専用の軽量ページ `/dashboard/m`（ステータスカード9枚だけを1リクエストで返す。スマートフォン向け）と、ホーム画面に追加するためのマニフェスト・アイコンも置く。これらは中継の総当たりルートより**前**に定義する必要がある（後ろだとStreamlitへ中継されて404になる）。カードの判定ロジックは `services/home_status_service.py` にあり、Streamlit 側（`views/dashboard/summary.py`）と軽量ページの両方がここを呼ぶ — **判定を View 層へ書き戻すと2つの画面で内容が食い違う**ので、`tests/test_mobile_status_page.py` が固定している。

`/quest/{full_path}` と `/camera/{full_path}` のルートは、`family-quest` のSPAビルドを静的ファイルとして配信する。パストラバーサル対策（realpath化したdistディレクトリに対する `os.path.commonpath` チェック）を行い、クライアント側ルーティングのために `index.html` へフォールバックする。

### 依存性注入(DI)について

`MY_HOME_SYSTEM/` は意図的にDIコンテナ・FastAPIの `Depends()` を導入していない（Issue #554で採否を検討し、現状維持を採用）。サービス（`services/*.py`）はモジュールレベルのシングルトンとしてインスタンス化され、ルーターはこれを直接importして使う（例: `routers/quest_router.py` の `from services.quest_service import game_system, quest_service, shop_service, user_service, inventory_service`）。設定も `config` モジュールのグローバル定数を各所が直接参照する（`config.UPLOAD_DIR`、`config.CAMERAS` 等）。個人用IoTシステム・単一プロセス・SQLiteという前提では、DIコンテナを導入する利益より複雑化のコストの方が大きいと判断したための意図的な設計であり、欠陥ではない。テストの分離は `tests/conftest.py` の `isolated_db` フィクスチャや、個々のテストによる `config.SQLITE_DB_PATH` 等の直接monkeypatchで行っており、この方式で十分に成立している。新規のルーター・サービスを追加する際も `Depends()` によるDIは導入せず、既存のモジュールレベルシングルトン+直接importのパターンに従うこと。

### データベース

SQLiteのみを使用し、単一ファイル `config.SQLITE_DB_PATH`（デフォルトは `config.py` と同じ場所の `home_system.db`。環境変数 `SQLITE_DB_PATH` で上書き可能で、CIではこれを `:memory:` に設定している。ただし `isolated_db` フィクスチャや既存テストの大半はこの環境変数ではなく `config.SQLITE_DB_PATH` をテストごとの一時ファイルへ直接monkeypatch/再代入している）。読み書きの標準的な方法は `core/database.py` の `get_db_cursor()` コンテキストマネージャで、`sqlite3.OperationalError`（"database is locked"）時のリトライ、WALモード・外部キー制約の設定、例外時のロールバックを行う。新規コードでは生の `sqlite3.connect()` を直接開くのではなく、これ（または単純なINSERT用の `save_log_generic`/`save_log_async`）を使うこと。

**並行制御は「単一プロセス前提」で成立している**（Issue #755 で明文化、#760 で強制）。`quest_users`（`gold`/`exp`/`level`/`medal_count`）の read-modify-write の正しさは、`services/quest/locks.py` の `_get_user_balance_lock`（`threading.Lock`）による**`unified_server` プロセス内の直列化だけ**に依存しており、`BEGIN IMMEDIATE` 等のDBレベルの排他はリポジトリ内の実行コードに存在しない（#544 で入り #547 で撤去。`core/database.py` の接続も `isolation_level` 未指定＝暗黙の deferred `BEGIN`）。したがって次の3つを守ること — いずれも破ると**エラーを出さないまま**ゴールドが消える/増える（ロストアップデート）ため、テストでも運用でも気づけない。

- `uvicorn.run()` に `workers` を渡さない・`gunicorn -w N` に置き換えない（`deploy/systemd/home_system.service` の `ExecStart` も単一プロセス）。
- `unified_server` 以外のプロセス（cronスクリプト、スケジューラのタスク等）から `quest_users` を直接書き換えない。別プロセスからの更新は `reset_game.py` と同じくHTTP API を経由する。
- 例外的に安全なのは `services/quest/shop_service.py` の `UPDATE quest_users SET gold = gold - ? WHERE user_id = ? AND gold >= ?` のような**DBレベルで原子的な条件付きUPDATE**だけ。新しい残高更新を足すならこの形にする。

この前提は `tests/test_single_process_invariant.py`・`.github/scripts/test_systemd_units.py`・`tests/test_balance_lock_cross_path_concurrency.py` の `TestConcurrencyControlDocumentationMatchesCode` が固定している。前提を変えるなら、先に残高更新をDBレベルで原子的な形へ移し、これらのテストとコメントを同時に更新すること。

横断的に使う低レベル処理は `core/` の共通モジュールに集約してある（Issue #661）。新しい経路を足すときは同種の実装を書き起こさずこれらを使うこと: Discord Webhook への送信は `core/discord.py`（分割・リトライ・URLマスク。`core.logger` を import しない制約あり）、監視スクリプトの状態ファイルは `core/state_file.py`（flock + tmp + `os.replace`）、読み取り専用のDB接続は `core/database.py` の `get_ro_connection()`（timeout 30秒）、ONVIF の WSDL 探索は `core/onvif_utils.py`。

スキーマ変更は **`migrations/NNNN_description.sql`**（ゼロ埋め連番、`core/migrations.py` の `apply_pending_migrations()` がファイル名の昇順で適用し、適用済みバージョンは `schema_migrations` テーブルで管理）を経由して行う。`ALTER TABLE ... ADD COLUMN` を先頭に書き、データ移行系の文をその後に続けること — マイグレーションは、既に列が存在するDBに対して再実行されても安全でなければならない（ランナーは "duplicate column"/"already exists" エラーを「既に適用済み」として扱い処理を継続するが、それ以外の `OperationalError` は致命的な失敗として扱う）。**`migrations/` がスキーマの唯一の定義元である**（Issue #330）: 全テーブルのベースラインは `migrations/0000_baseline_schema.sql`（全文 `CREATE TABLE IF NOT EXISTS`）にあり、`init_unified_db.init_db()` はマイグレーション適用と検証だけを行う薄いラッパー、`services/quest_service.py` に残っていたレガシーな実行時「SELECTを試して失敗したらALTER」チェックも退役済みで、このパターンを復活させないこと。新しいテーブルの追加もベースラインの書き換えではなく新しい `NNNN_*.sql` で行う。`MY_HOME_SYSTEM/current_schema.sql` は `python init_unified_db.py --dump-schema` で `migrations/` から生成される参照用ダンプであり、手で編集せず、マイグレーションを追加・変更したPRでは再生成してコミットすること（`tests/test_current_schema_sql.py` が再生成結果との完全一致を検証する）。詳細は `MY_HOME_SYSTEM/migrations/README.md` を参照。

### 設定 (Configuration)

`config.py` は、`.env`（`python-dotenv` 経由）からロードされるモジュールレベル定数の大きな1ファイルに加え、gitignore対象で**アプリの起動に必須ではない**2つのローカルJSONオーバーレイを持つ:
- `devices.json` — カメラ/IoT機器の定義。Pydanticモデル（`CameraConfig`、`DeviceConfig`）で検証される。
- `family_members.local.json` — 各メンバーの表示用データ（年齢等）。プレースホルダーである `FAMILY_SETTINGS["styles"]` 辞書にマージされる。メンバーの**名前**自体は（ローカルオーバーレイに移されず）`config.py` にハードコードされたままになっている。これはLINE Botのメッセージマッチング（`handlers/line_handler.py`）がこれらの実名に対して部分文字列マッチングを行っているためで、ここで名前を変更・削除するとそのロジックが壊れる。

新しい外部連携を追加する際は、既存の番号付きセクション構成（モジュールdocstringの目次を参照）に従って認証情報/URLを `config.py` に追加し、`.env.example` にもプレースホルダーのエントリを追加すること。

### Family Quest (フロントエンド) の構造

`src/App.tsx` はトップレベルのUI状態（`viewMode`、`activeTab`、`currentUserIdx`）を保持するルートコンポーネントであり、ルーターを使わず各タブごとに機能画面を直接マウントする。`src/features/{quest,family,shop,camera}/` は機能ごとのコンポーネント/フックをグループ化している。`src/hooks/useGameData.ts`（React Query）はバックエンドの `/api/quest/*` エンドポイントと通信するデータフェッチ層であり、フロントエンドが新しいAPIフィールドを必要とする際はまずここを確認すること。**OpenAPIからTSへの生成パイプラインは意図的に導入していない**（Issue #752/#470 で採否を検討し、現状維持を採用。`openapi-typescript` 等を入れると `npm ci` の依存が増え、Issue #757 で軽くしたばかりの実機ビルド時間に効くため）。代わりに契約の正は `MY_HOME_SYSTEM/models/quest.py` の `GameDataResponse`（`GET /api/quest/data` の `response_model`）にあり、その同期は `MY_HOME_SYSTEM/tests/test_quest_api_type_contract.py` が**Pydantic ↔ SQLiteのテーブル列 ↔ `src/lib/gameDataSchema.ts` の手書きZod**を突き合わせて検証する（バックエンドだけが持つフィールドは「フロントで使うならZodに追記」「使わないなら同テストに明記」のどちらかを強制され、どちらもせずには済ませられない）。したがってバックエンドのレスポンス形状を変えるときは、`models/quest.py` の View モデルと `gameDataSchema.ts` の両方を同じPRで更新すること — 片方だけではCIが落ちる。`src/context/` は横断的なUI状態（設定、トースト通知）を保持する。確認/アラートダイアログはアプリ独自の `ConfirmModal`/`MessageModal` コンポーネントを使うこと — `window.confirm()`/`alert()` はこれらに置き換えて完全に削除済みである。

注意: 古いドキュメントに記載されていた一部機能（ボス戦、装備、ギルド、マイレージ、週間ランキング）は、2026年8月のリファクタリングで意図的に削除されている（`docs/specifications/全体設計書.md` の改訂メモおよびコミット `d1599d6`/`ffdc8c2`/`1818d5a` を参照）。これらの機能を復活させたり、まだ存在するかのように参照しないこと。

### DDD バッチ処理

NASマウントを共有している（ただし `nas_monitor.py` はNASの死活・容量を監視して通知するだけで、DDDのバッチを止める・絞る機構は実装されていない。DDD側は `batch_download_discord.py` の `check_disk_space` で自前に空き容量を判定する。Issue #656）だけでなく、`sys.path` に `MY_HOME_SYSTEM/` を追加して `core.*` を直接importする実依存もある（Issue #553）。ルート解決は `DDD/file_utils.py` の `resolve_my_home_system_root()` に集約されている。

| ファイル | import 内容 |
| --- | --- |
| `newface_monitor.py` | `core.logger.get_logger`、`core.nas_utils.get_managed_target_directory`、`core.utils.wait_for_storage_warmup` |
| `extract_youtube_urls.py` | `core.logger.get_logger`、`core.nas_utils.get_managed_target_directory` |
| `batch_download_discord.py` | `services.notification_service._send_discord_webhook`（`_standalone_send_discord_webhook` フォールバックあり） |

各スクリプトに `try/except ImportError` のフォールバックはあるが、本番（ラズパイ）経路では MY_HOME_SYSTEM が存在する前提で `core` を使うため、CI・単体テストだけでは気づけない実依存になっている。`core.logger` は import 時に `config` をロードするため、**`MY_HOME_SYSTEM/core/logger.py`・`nas_utils.py`・`utils.py` のシグネチャを変更した場合は、DDD のテスト（`DDD/conftest.py` が同じ `core.*` を import する前提で書かれている）も必ず実行すること**。

`batch_download_discord.py` は `DownloadStrategy` を軸としたストラテジーパターン（`UniversalYtDlpStrategy` vs `ScrapingStrategy`）を採用し、同時実行を防ぐために `fcntl.flock` によるロックファイルを使用している — この領域に新しい長時間稼働のcron的スクリプトを追加する際は、同じロック方式に従うこと。

## 仕様書ドリフト規約

`docs/specifications/` はソースツリーをミラーしており、`MY_HOME_SYSTEM/*.{py,sh}`、`DDD/*.{py,sh}`、`family-quest/src/**/*.{ts,tsx,js,jsx}` 配下のソースファイル1つにつきMarkdownファイルが1つ対応する（テスト、マイグレーション、`__init__.py`、`.d.ts` ファイルは対象外）。`.sh`（例: `start_all.sh` → `start_all.md`）も`.py`と同様にチェック対象である点に注意。`.github/scripts/check_spec_drift.py` がこれをすべてのPRで（対応する仕様書が更新されていない新規/変更ソースファイルを検知）、また週次の全体監査でチェックする — **どちらのチェックも仕組み上非ブロッキング**（gitコマンド自体が失敗するような異常時を除く。詳細は `check_spec_drift.py` 冒頭のdocstringおよび両ワークフローの `continue-on-error` 設定を参照）だが、対応する仕様書を持つソースファイルを追加・意味のある変更をした場合は、ドリフトとして検知されないよう同じPR内でその仕様書も更新すること。まだ仕様書がない新規ファイルについては新規作成が厳密に必須というわけではないが、このリポジトリで確立された習慣ではある。

## その他の規約

- Python・シェルスクリプト全体のコメント・docstringは日本語で書かれている。既存ファイルを編集する際はそれに合わせること。新規で無関係なファイルであれば英語でも問題ない。
- 長時間稼働するホスト用スクリプト（`start_all.sh`、`run_task.sh`）は固定のデプロイ先パス（`/home/masahiro/develop/...`）と `.venv` の存在を前提にしている — これらはRaspberry Pi向けのデプロイスクリプトであり、環境間で可搬ではない。CIやローカル開発でこれらのパスが通用すると仮定しないこと。
- `config.py` の `BACKUP_FILES` と `.coveragerc` の `omit` リストは、それぞれ「本番データ/設定として扱うファイル」と「テストカバレッジ対象から除外するファイル」を定義している — 新しいトップレベルのステートフルなファイルを追加し、それが単体テスト対象外であるべき、またはバックアップに含めるべきものであれば、両方を更新すること。
- PRをrevertしたときは、そのPRが解消していたIssue・レビューレポートの対応状況を必ず「未対応」に戻すこと（Issueの再オープン、`docs/reports/` 配下の対応状況表の訂正）。2026-08-28のPR #80 revertで、Cloudflare Access JWT検証の実装は消えたのに「対応完了」の記録だけが残り、棚卸しから漏れ続ける事故が起きた（Issue #321）ことの再発防止。
