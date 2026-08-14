# ファイル構成レビューレポート — my_python_project

**レビュー日**: 2026-08-14
**レビュー対象**: リポジトリ全体のファイル・ディレクトリ構成（`MY_HOME_SYSTEM/`、`family-quest/`、`DDD/`、`docs/`、リポジトリルート）
**スコープ**: 本レポートはファイル/ディレクトリの配置・命名・階層構造のみを対象とする。コードの正確性・セキュリティは対象外（`CODE_REVIEW_REPORT_ALL.md`、`MY_HOME_SYSTEM/CODE_REVIEW_REPORT.md`を参照）。
**方針**: 本PRでは、コードのみで安全に直せる構成上の問題はその場で修正した（詳細は各指摘の「対応」欄）。git履歴の書き換えやリポジトリ名の変更、外部cron/systemdからの呼び出しパスに影響する変更など、利用者の判断が必要なものは指摘のみに留めた。

---

## 1. 🔴 重要

### [重要] リポジトリルートに`node_modules/`が誤って追跡されていた
**対象**: `node_modules/@types/canvas-confetti/*`, `node_modules/canvas-confetti/*`（計8ファイル）

**問題**: リポジトリルートには`package.json`が存在せず、Node.jsプロジェクトはルートに存在しない。にもかかわらず`canvas-confetti`（`family-quest/package.json`の実依存）由来のファイルがルート直下の`node_modules/`としてgit追跡されていた。`family-quest/`自身の`node_modules/`は`family-quest/.gitignore`で正しく除外されているため、これは初期コミット時に誤ったディレクトリで`npm install`を実行した際の取り違えと見られる。ルートの`.gitignore`に`node_modules`の除外ルールがなかったため、再発防止もされていなかった。

**対応**: `git rm --cached -r node_modules`で追跡解除し、ルート`.gitignore`に`/node_modules/`を追加。

---

## 2. 🟠 主要

### [主要] 個人の給与・賞与データがgit追跡されていた（`.gitignore`の意図と矛盾）
**対象**: `MY_HOME_SYSTEM/data/salary_history.csv`, `MY_HOME_SYSTEM/data/bonus_history.csv`

**問題**: ルート`.gitignore`には`*.csv`の除外ルールが既に存在するが、これら2ファイルはルール追加前（またはそれ以前）のコミットで追跡が開始されており、実在の氏名・従業員ID・給与内訳を含む個人データがそのまま追跡され続けていた。`.gitignore`ルールは既存の追跡ファイルには遡及しないため、意図と実態が乖離していた。

**対応**: `git rm --cached`で追跡解除（ワーキングツリー上のファイルは残るが、以後は既存の`*.csv`ルールにより無視される）。**注意**: git履歴には引き続き残るため、機微性を鑑みて履歴の削除（`git filter-repo`等）が必要か、リポジトリ所有者の判断を推奨する（過去PR #4で指摘された他の個人情報Git混入と同種の問題）。

### [主要] `tools/`ディレクトリがレイヤー構造の慣習を破っている
**対象**: `MY_HOME_SYSTEM/tools/`

**問題**: このコードベースは`routers/`（エンドポイント）→`services/`（`*_service.py`で統一されたビジネスロジック）→`models/`/`handlers/`/`monitors/`/`core/`/`views/`という明確な層構造を持つ。しかし`tools/`には`camera_digest_service.py`という`_service`命名のモジュールが、シェルスクリプトや単発ダウンロードスクリプトと混在していた。調査の結果、`camera_digest_service.py`の呼び出し元は`MY_HOME_SYSTEM/old/send_ai_report.py`（レガシー領域）のみで、アクティブなコードからは一切参照されていないことを確認した。

**対応**: `camera_digest_service.py`を`old/camera_digest_service.py`へ移動し、唯一の呼び出し元である`old/send_ai_report.py`のimportを更新（`import tools.camera_digest_service as camera_digest_service` → `import camera_digest_service`。`old/`内の他モジュール（`weather_service`等）と同じフラットimport方式に統一）。

**未対応（要判断）**: `tools/financial_service.py`（家計シミュレーション用Streamlitページ、`streamlit run tools/financial_service.py`で個別起動される想定）と`tools/google_photos_service.py`はコード内から一切import されておらず、`services/`ないし`views/`への移動が構成上は自然だが、リポジトリ外のcron/systemd等から直接パス指定で起動されている可能性があり、呼び出し元を確認できないため本PRでは移動を見送った。移動する場合は運用側の起動コマンドも合わせて更新が必要。

### [主要] `_service`命名のモジュールが`MY_HOME_SYSTEM/`直下に置かれていた
**対象**: `MY_HOME_SYSTEM/train_service.py`, `MY_HOME_SYSTEM/sound_manager.py`

**問題**: `train_service.py`（`views/dashboard/misc_tab.py`, `views/dashboard/summary.py`から使用）と`sound_manager.py`（`services/quest_service.py`, `routers/quest_router.py`から使用）はいずれも実際に使われているコアロジックだが、`services/`や`core/`の同種モジュールと異なりプロジェクトルート直下にフラットimport（`import train_service`）されていた。

**対応**:
- `train_service.py` → `services/train_service.py`に移動。`views/dashboard/misc_tab.py`, `views/dashboard/summary.py`のimportを`from services import train_service`に更新。
- `sound_manager.py` → `core/sound_manager.py`に移動（`services/`と`routers/`の両方から使われる横断的ユーティリティのため`core/`が適切と判断）。`services/quest_service.py`, `routers/quest_router.py`, `tests/test_quest_router_endpoints.py`のimportを`from core import sound_manager`に更新。
- 移動後、`python3 -m pytest`（369件）全件パス、`ruff check --select F821,F822,F823,E9`クリーン、`unified_server.py`のimportが正常に通ることを確認済み。

---

## 3. 🟡 軽微

### [軽微] `.done`マーカーファイルが`.gitignore`ルールに反して追跡されていた
**対象**: `MY_HOME_SYSTEM/data/timelapse_records/20260713_evening.done`

**問題**: ルール`*.done`は既に`.gitignore`に存在するが、2.のCSVと同様、ルール追加前に追跡が始まっていた。中身のないランタイム状態マーカーで、バージョン管理する価値がない。

**対応**: `git rm --cached`で追跡解除。

### [軽微] `MY_HOME_SYSTEM/old/`内に破損したファイル名の0バイトファイルが存在
**対象**: `MY_HOME_SYSTEM/old/completed_at,`（末尾にカンマ、拡張子なし）

**問題**: シェルリダイレクトの誤爆等で生成されたと見られる、意味のない0バイトファイル。

**対応**: 削除。

### [軽微] `.gitignore`に不要な実行権限ビットが付与されていた
**対象**: `/.gitignore`（`-rwxr-xr-x`）

**対応**: `chmod 644`で修正。

### [軽微・未対応] `MY_HOME_SYSTEM/old/`と`monitors/old/`という2つの独立したレガシー置き場が存在
**対象**: `MY_HOME_SYSTEM/old/`（README付き、約20ファイル）、`MY_HOME_SYSTEM/monitors/old/`（README無し、7ファイル）

**問題**: いずれも現行コードから参照されないレガシースクリプト群だが、置き場が2箇所に分かれており規約が統一されていない（`old/README.md`の内容も現行アプリと無関係なインフラメモになっている）。

**未対応理由**: 統合の実施自体はローリスクだが、内容の刷新（README書き直し）を含めると本レビューの範囲（構成整理）を超えるため、次回の対応候補として記録するに留めた。

### [軽微・未対応] `docs/specifications/family-quest/`に孤立した仕様書が残存
**対象**: 削除済みコンポーネント（`AdminDashboard`, `GuildBoard`, `BattleEffect`, `BossCard`, `FamilyMileageCard`, `FamilyParty`, `WeeklyTrends`, `EquipmentShop`, `ShopContainer`）に対応する`.md`が、PR #9のフロントエンド刷新後も削除されずに残っている。逆に新設された`family-quest/src/hooks/useLayoutMode.ts`に対応する仕様書がない。

**未対応理由**: これを検知する`check_spec_drift.py`の週次監査が既に仕組みとして存在し、非ブロッキングでIssue化される設計になっている。本PRのスコープ外として、既存の監査フローに委ねる。

### [軽微・未対応] `MY_HOME_SYSTEM/`・`DDD/`にトップレベルREADMEが無い
`family-quest/README.md`は存在するが、より大規模な2つのPythonサブプロジェクトにはディレクトリ構成やDDDという略称の意味を説明するREADMEがない。追加を推奨するが、内容作成は本レビューのスコープ外とした。

### [軽微・未対応] `docs/merge_mds.py`が`docs/`直下に混在
Markdownを1ファイルに結合する開発者個人用スクリプトと見られ、CIスクリプト（`.github/scripts/`配下）とは性質が異なる。移動先候補は`.github/scripts/`または新設の`scripts/`だが、用途が本人以外に不明なため削除も含めて所有者の判断が必要。

### [軽微・未対応] `family-quest`にテストディレクトリが存在しない
`MY_HOME_SYSTEM/tests/`（30+ファイル、`conftest.py`あり）と比べ、`family-quest/`側はテストの仕組み自体が無い。ファイル構成の指摘というより開発体制上のギャップとして記録。

### [軽微・未対応] `family-quest`・`DDD`向けのCIワークフローが無い
`.github/workflows/test.yml`は`MY_HOME_SYSTEM`専用。`family-quest`の`eslint`/`tsc -b`/`vite build`を検証するCIジョブが無い。

---

## 4. ⚪ 指摘なし（Nit）・現状維持を推奨

- **サブプロジェクト間の命名規則の違い**（`MY_HOME_SYSTEM`=大文字スネーク、`family-quest`=ケバブケース、`DDD`=大文字略称）: 機能的には問題なし。破壊的なリネームは`check_spec_drift.py`の`PY_SOURCE_DIRS`定数やCIのパス参照に波及するため非推奨。新規サブプロジェクトを追加する際はkebab-case/小文字を推奨、という運用ルールの明文化に留めるべき。
- **リポジトリルートに`README.md`が無い**: 追加を推奨するが軽微。
- **`CODE_REVIEW_REPORT_ALL.md`がルート直下**: `docs/reports/`等への移動も考えられるが、影響範囲が小さくNit。
- **`MY_HOME_SYSTEM/requirements.txt`末尾2行が`pip freeze`出力の並び順から外れている**（`aiofiles==24.1.0`, `psutil>=5.9.0`）: 手動追記の跡だが実害なし。

---

## 5. 明示的に「良い構成」と確認できた箇所（変更不要）

- **`docs/specifications/`の構成**: `family-quest`向けは`src/`をそのままミラーするネスト構造、`MY_HOME_SYSTEM`/`DDD`向けはソース1ファイルにつきMarkdown1枚のフラット構造——という使い分けは`check_spec_drift.py`のdocstringに明記された意図的な規約であり、問題ではない。
- **仕様書ドリフト検知CI**（`.github/scripts/check_spec_drift.py` + 2つのworkflow）: ドキュメントツリーをコードと同期させ続ける良い仕組みで、配置も`.github/scripts/`と適切。
- **`MY_HOME_SYSTEM/migrations/`**: 番号付きマイグレーションの命名規則が一貫しており、追加方法を説明する`README.md`も併設。他ディレクトリの手本となる構成。
- **`MY_HOME_SYSTEM/tests/`**: `test_<module>.py`命名で一貫し、`tests/`配下に集約。`conftest.py`・`pytest.ini`・`.coveragerc`も適切に配置。今回の全走査で`tests/`外に迷子のテストファイルは見つからなかった。
- **サブプロジェクトごとに独立した依存関係マニフェスト**（`MY_HOME_SYSTEM/requirements*.txt`、`DDD/requirements.txt`、`family-quest/package.json`）: モノレポとして正しいパターンであり、1つのマニフェストに統合すべきではない。
- **ルート`.gitignore`の`family-quest`向け救済ルール**（`!family-quest/**/*.ts`等）: 過去に発生した「広範な`*.ts`/`*.json`ルールが`package.json`/`tsconfig.json`を誤って除外していた」問題（PR #4で発覚・修正）に対する現在の対処が正しく機能していることを確認済み。同種の地雷が他に無いかも今回の全走査で確認したが、他に見つからなかった。
- **`DDD/split_results/`問題は既に解決済み**: 過去に個人生成コンテンツがgit追跡されていた問題はコミット`5cbbdac`で解消され、`.gitignore`にも追記済み。現在のワーキングツリーには存在しない。

---

## 6. 本PRでの対応まとめ

| 分類 | 対応内容 |
|---|---|
| 追跡解除 | `node_modules/`（ルート）、`salary_history.csv`、`bonus_history.csv`、`20260713_evening.done` |
| 削除 | `MY_HOME_SYSTEM/old/completed_at,`（破損ファイル名の0バイトファイル） |
| 移動 | `train_service.py` → `services/`、`sound_manager.py` → `core/`、`tools/camera_digest_service.py` → `old/` |
| import更新 | 上記移動に伴う参照元6ファイルのimportパスを追従 |
| `.gitignore` | `/node_modules/`ルールを追加 |
| 権限 | `.gitignore`の実行権限ビットを除去 |
| 検証 | `pytest`（369件全パス）、`ruff check --select F821,F822,F823,E9`（クリーン）、`unified_server.py`のimport確認 |

上記以外の指摘（`tools/financial_service.py`等の移動、`old/`統合、README追加、CI拡充等）は、外部運用への影響が未確認、または内容作成を伴い本レビューの範囲を超えるため、指摘のみに留めた。
