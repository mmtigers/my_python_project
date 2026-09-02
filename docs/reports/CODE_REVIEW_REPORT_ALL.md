# 総合コードレビューレポート — my_python_project

**レビュー日**: 2026-08-11
**レビュー対象**: リポジトリ全体（`MY_HOME_SYSTEM/`、`family-quest/`、`DDD/`）
**レビュー方式**: ユーザー提示「AIコードレビュー指示書」に基づく本番運用観点の網羅レビュー
**前提**: `docs/reports/MY_HOME_SYSTEM/CODE_REVIEW_REPORT.md`（2026-08-10/11付、MY_HOME_SYSTEM限定の既存レビュー、指摘16件中13件対応済み。当時はリポジトリ構成整理前のため`MY_HOME_SYSTEM/CODE_REVIEW_REPORT.md`直下に配置されていた）が存在するため、本レポートは (a) その3件の未対応項目の現状再確認、(b) 既存レポートが薄い/対象外だった領域の追加調査、(c) 今回新たに対象となった `family-quest/`・`DDD/` の初回レビュー、を統合したもの。既に確認済みで問題なしの箇所は重複させず「確認済み・問題なし」とだけ記す。

---

## 0. 対応状況（最終更新: 2026-09-02）

指摘34件（Critical 9・High 6・Medium 19）のうち25件は対応完了、8件は未対応・部分対応（残作業あり）、1件は意思決定により対応対象外。

> **状態管理の方針（2026-09-02・Issue #323）**: 残件の状態管理の正は**GitHub Issue**に一本化する。本レポートは「レビュー時点の歴史的記録+未解決項目のIssueへのポインタ」であり、以下の表はIssue #323対応時点のスナップショット。以後の最新状態は各Issueを参照すること。
> また、本レポート中の「docs改善バックログ」（B1/B5/B6/C2/D4等の項番）の参照先は**リポジトリ内のファイルではなく、Claude Artifact「RPi改善バックログ」**である（リポジトリ内を検索してもヒットしない）。今後の残件参照はバックログ項番ではなくGitHub Issue番号を正とする。**7. 最終結論**の「🔴 REJECT・49/100」判定はこの2026-08-11時点の状況を反映したものであり、以下の通り大半が解消済みのため、現状の判定としては読み替えが必要。

> **2026-09-02訂正（Issue #321）**: Critical#2は一度「対応完了」（`access_control_middleware`によるCloudflare Access JWT検証必須化、PR #80）と記録されたが、PR #80は2026-08-28のfamily-quest障害でrevert済みであり、**未対応に戻っている**。Critical#8の外部アクセス防御についても同様にアプリ層のJWT検証は消滅しており、エッジのCloudflare Access（インフラ側・コード外）のみが防御となっている。本表はこの回帰を反映済み。詳細は`CODE_REVIEW_REPORT_2026-08-22.md`のH-10、`MY_HOME_SYSTEM/CODE_REVIEW_REPORT.md`の2.2を参照。

### 🔲 残件（未対応・部分対応・9件、うち1件はスコープ外）

| # | 項目 | 状態 | 詳細 |
|---|---|---|---|
| Critical#1 | APIの認可欠如（`user_id`/`approver_id`のクライアント信頼） | 未対応（意思決定によりスコープ外） | 棚卸し課題4で対応スコープ外と合意済み。docs改善バックログのB1と同一課題 |
| Critical#2 | IPアドレス制限ヘッダー詐称 | **未対応に戻った（PR #80 revert）** | 一度`access_control_middleware`によるCloudflare Access JWT検証必須化（PR #80）で対応完了と記録されたが、2026-08-28のfamily-quest障害でPR #80はrevert済み。現在の`unified_server.py`に存在するのは`ip_restriction_middleware`のみで、非プライベートIPからのリクエストをログに記録するだけで全て通過させる。`Cf-Access-Jwt-Assertion`を参照するコードはコメント1箇所のみ。JWT検証を再実装するか、エッジのCloudflare Access委譲を正式設計として確定させるかはIssue #321で判断待ち。`CODE_REVIEW_REPORT_2026-08-22.md`のH-10と同一課題 |
| Critical#3 | Streamlitダッシュボードの無認証公開 | 部分対応 | `--server.address`を`0.0.0.0`→`127.0.0.1`に変更済みで外部露出は解消。ただし`sudo systemctl restart`ボタン（`views/dashboard/log_tab.py:119`）を含めアプリ内認証は依然なし |
| Critical#8 | カメラ機能の無認証公開 | 部分対応（**PR #80 revertによりアプリ層防御は消滅**） | 外部アクセスは一度`access_control_middleware`でCloudflare Access JWT検証必須化（PR #80）されたが、2026-08-28のrevertで消滅し、現在の外部アクセス防御はエッジのCloudflare Access（インフラ側・コード外）のみ。LAN内アクセスは引き続き無認証だが、棚卸し課題4でLAN内を信頼境界とする方針が合意済み。アプリ層防御の再実装要否はCritical#2と同じくIssue #321で判断待ち |
| High#3 | `UserStatusCard`のHP固定表示 | 対応済み・要追加確認 | HP再計算バグ自体は是正済み（バックエンド値をそのまま使う設計に変更）。ただし現在の`UserStatusCard.tsx`にはHP表示UI自体が存在しない。意図した仕様変更か確認が必要 → Issue #327（`decision-needed`）で追跡 |
| M2 | `config.py`の子供の氏名・年齢ハードコード | 部分対応 | 年齢は`family_members.local.json`（`.gitignore`で除外・git追跡対象外）に切り出し済み。氏名は`config.py`の`FAMILY_SETTINGS`に引き続き残存 |
| M4 | SwitchBot Webhook署名検証がトークン未設定時オプトイン | 変化あり（仕様は同じ・警告追加） | 実装（未設定時に検証スキップ）自体は変更なし。ただしアプリ起動時（`unified_server.py`のlifespan）に未設定を警告するログが追加された。実機でのトークン設定はIssue #318（`blocked:実機作業`）で追跡。docs改善バックログのB5と同一課題 |
| M17 | `split_prompts.py`のファイル名衝突時無警告上書き | 部分対応 | 衝突時に警告ログを出すよう改修済み（「無警告」は解消）。上書き自体を防止・退避する仕組みは未実装 |
| M18 | `extract_youtube_urls.py`の出力上書き | 部分対応 | 同上（警告ログ追加のみ、上書き自体は継続） |

### ✅ 対応完了（25件）

**Critical**: #4 DBバックアップのGit履歴残存（Issue #296で`git filter-repo`相当の履歴消去まで完全解消済み。2026-09-02のIssue #323棚卸しで確認）／#5 `package.json`/`tsconfig.json`不在（コミット`74e5f83`で追加）／#6 Admin Dashboard無条件到達性（`features/admin/`ごと機能削除）／#7 アバターアップロード実装不備（`apiClient.postForm()`実装）／#9 DDD機微コンテンツのGit無制限追跡（`.gitignore`に`DDD/split_results/`等追加、追跡0件確認）

**High**: #1 `family-quest/`テストコード皆無・ESLint対象外（ESLint対象化に加え、現在はvitestテストが存在しCIの`npm test`で実行中。2026-09-02のIssue #323棚卸しで確認）／#2 tsconfig不在・`as any`型迂回（`tsconfig.json`で`strict: true`、`as any`は0件）／#4 `batch_download_discord.py`多重起動防止欠如（`fcntl.flock`実装）／#5 `extract_youtube_urls.py`レート制限未考慮（ジッター付きsleep＋連続失敗しきい値実装）／#6 `newface_monitor.py`非アトミック書き込み（tmpファイル＋バックアップ＋`Path.replace()`実装）

**Medium**: M1 `financial_service.py`ハードコード（機能ごと削除）／M3 `ai_logic.py`死んだコードの脆弱性（完全削除＋回帰テストで再発防止）／M5 `nas_monitor.py`のrsync timeout未指定（`timeout=120`追加）／M6 CIにlint/型チェック不在（`ruff`・Bandit・pip-audit・`npm run build`をCIに追加）／M7 `ApprovalList.tsx`の`approver_id`ハードコード（動的解決に変更）／M8 エラーメッセージ握りつぶし（`detail`をUIに表示）／M9 承認/却下失敗時の無通知（`setMessageData`で通知）／M10 `history_id`フォールバック不統一（`??`で統一）／M11 クエストロック判定ロジック重複（`getQuestLockState()`に共通化）／M12 `CameraDashboard.tsx`の生`useEffect`+fetch（`WeeklyTrends.tsx`は機能ごと削除済み。`CameraDashboard.tsx`はIssue #326/PR #345でReact Query化完了）／M13 `pendingInventory`クエリ重複登録（1箇所に統一）／M14 `apiClient`迂回の生fetch呼び出し（全箇所`apiClient`経由に統一）／M15 `AvatarUploader.tsx`のアップロードファイル検証欠如（フロント・バックエンドとも検証実装済み。残っていたサイズ上限不一致もIssue #325/PR #343で5MBに統一完了）／M16 DDD `requirements.txt`の依存不足（`yt-dlp`/`curl_cffi`を明記）／M19 `newface_monitor.py`の`cast_id`にURLクエリ混入（クエリ・フラグメント除去処理を追加）

---

## 1. 仕様理解

- **システムの目的**: 家庭内自動化基盤。中核は `MY_HOME_SYSTEM/`（FastAPI製バックエンド、SwitchBot/Nature Remo/防犯カメラ監視、家族向けクエスト＝お手伝いゲーミフィケーション、LINEボット、家計/不動産監視を1サーバーに統合）。`family-quest/` はそのクエスト機能のReact SPAフロントエンド。`DDD/` はバックエンドとは独立した、開発者個人用のDiscord動画一括ダウンロード・YouTube URL抽出・Webサイト監視スクリプト群（家庭内自動化とは別の私的自動化ツール）。
- **主なユーザー**: 家族4名程度（`dad`/`mom`/`daughter`/`son`等のuser_id）。`DDD/`は開発者本人のみが使う想定。
- **入力/出力/データフロー**: 前回レポート記載の通り（SQLite単一DB、Webhook/API/LINE/スクレイピング入力、LINE/Discord通知・SPA向けJSON API・HLS配信が出力）。`family-quest/`はこのAPIをTanStack Query経由で叩くSPA。`DDD/`はローカルファイル(list.txt等)を入力に外部サイトをスクレイピング/ダウンロードし、ローカル/NASに保存、Discord Webhookで通知。
- **認証・認可方式**: `MY_HOME_SYSTEM/`はアプリケーション層に認証機構がなく、クライアント送信の`user_id`をそのまま信頼（後述、未解決）。`family-quest/`側もこれに対応する認証UIは実装されていない。`DDD/`は外部サービスに対する認証は環境変数のAPIトークン/Webhook URLのみで、スクリプト自体へのアクセス制御という概念はない（ローカル実行前提）。
- **想定利用規模**: 家族単位・個人単位の小規模。ただしLINE Webhookやカメラ機能はインターネットに露出している。
- **仕様不明のため判断不能な点**: インフラ構成（Cloudflare Access導入状況、リバースプロキシ設定）、`family-quest/`の本番デプロイパス構成、`DDD/`の実行環境（cron設定の実体）はコードから確認できず、判断不能。

---

## 2. 指摘事項（重要度別）

### 🔴 Critical

#### [Critical] APIに認可機構が存在せず、`user_id`/`approver_id`をクライアント申告のまま信頼している（既存指摘・**未解決を再確認**）

**対象箇所**
- `MY_HOME_SYSTEM/routers/quest_router.py:46-56, 136-166`（`complete_quest`, `approve_quest`, `reject_quest`, `admin_update_boss`）
- `MY_HOME_SYSTEM/routers/bounty_router.py:174-306`
- `MY_HOME_SYSTEM/routers/system_router.py:9-14`（`/api/system/backup`、認可チェック皆無）
- `MY_HOME_SYSTEM/services/quest_service.py:407-408`

**問題**: リクエストボディの`user_id`/`approver_id`を検証なしに「本人」として扱う。`/admin/boss/update`と手動バックアップトリガーには権限チェック自体が存在しない。

**なぜ問題か**: サーバーにHTTPリクエストを送れる立場なら誰でも他人のクエスト承認・却下、`dad`/`mom`詐称、ボス状態改ざん、無認可バックアップトリガーが可能。

**再現条件**: `user_id="dad"`を指定して`POST /api/bounty/{id}/accept`等を呼ぶだけ。

**改善案**: 最小限、LINEログインまたは共有APIキー/PINによる軽量認証を導入し、`user_id`をリクエストボディでなくサーバー側で検証済みのセッション/トークンから解決する。少なくとも承認・削除・バックアップ・管理者系エンドポイントには管理者トークンをヘッダーで要求する。

---

#### [Critical] IPアドレス制限ミドルウェアがクライアント送信ヘッダーを無条件に信頼しており迂回可能（既存指摘・**未解決を再確認**）

**対象箇所**: `MY_HOME_SYSTEM/unified_server.py:173-225`（`ip_restriction_middleware`）

**問題**: `cf-connecting-ip`/`x-forwarded-for`はクライアントが任意設定できるヘッダーだが、これらが本当にCloudflareエッジ由来かを検証していない。`Cf-Access-Jwt-Assertion`の検証も未実装（コードコメント上は「将来追加検討」とあるのみ）。

**なぜ問題か**: オリジンに直接到達できる経路があれば、`X-Forwarded-For`にプライベートIPを詐称するだけで上記Criticalの無防備なAPI群にフルアクセスできる。

**改善案**: オリジンへの接続をCloudflareの公開IPレンジに限定するファイアウォールルール、または`Cf-Access-Jwt-Assertion`の署名検証を実装する。1件目の認可強化と合わせた多層防御が必須。

---

#### [Critical] （新規発見）Streamlitダッシュボードが無認証で`0.0.0.0`にバインドされ、`sudo systemctl restart`ボタンと家族の個人情報（健康記録等）を無制限に公開している

**対象箇所**
- `MY_HOME_SYSTEM/start_all.sh:71`（`streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0`）
- `MY_HOME_SYSTEM/views/dashboard/log_tab.py:114-119`（`sudo systemctl restart home_system`ボタン）、`:125-127`（バックアップトリガーボタン）
- `MY_HOME_SYSTEM/dashboard.py:59-67`（`child_health_records`、`security_logs`、`shopping_records`等を無認証表示）

**問題**: `unified_server.py`のIP制限ミドルウェアはFastAPIアプリのみに適用され、別プロセスであるこのStreamlitダッシュボードには一切及ばない。ログイン・パスワード・トークンなど認証機構は皆無（grep確認済み）。

**なぜ問題か**: ポート8501に到達できる者は誰でも、子供を含む家族の健康記録・防犯ログ・購入履歴を閲覧でき、さらにワンクリックで本番サービスを強制再起動できる。仮に上記2件のCritical（認可・IP制限）を将来修正しても、この経路は無傷のまま残る。

**再現条件**: LAN内、あるいはポート開放設定のミス等でホストの8501番ポートに到達できる状態でブラウザアクセスするだけ。

**改善案**: `--server.address 127.0.0.1`に変更し、外部公開が必要ならCloudflare Access等の認証層の配下に置く。`streamlit-authenticator`等でアプリ内認証を追加する。`sudo systemctl restart`ボタンは削除するか、管理者操作の確実な認可の後ろに置く。

---

#### [Critical] `MY_HOME_SYSTEM/old/`配下の個人情報を含むDBバックアップがGit履歴に残存（既存指摘・**部分解決を再確認**）

**対象箇所**: 削除済みだがGit履歴に残存（コミット`54158cc`〜`8cedfd0`、約19.3MB）

**現状**: `.gitignore`修正（`*.db.*`、`*.bak`追加）と`git rm`によるワーキングツリー/HEADからの削除は完了（コミット`e1ff5e0`、2026-08-10）。ただし**Git履歴（`git log --all`で到達可能）にはblobが残存**しており、`git filter-repo`等による履歴からの完全消去は未実施。

**改善案**: 前回レポート通り、リポジトリの公開範囲を確認した上で、必要であれば`git filter-repo`＋force pushによる履歴消去を検討する（リポジトリ所有者の判断が必要な破壊的操作のため、実施前に必ず確認すること）。

---

#### [Critical] `family-quest/`に`package.json`/`tsconfig.json`が存在せず、ビルド・依存関係管理が不可能

**対象箇所**: `family-quest/`直下（`package.json`はGit履歴上も一度もコミットされていない）

**問題**: `import`文からReact・Vite・TanStack Query・hls.js・framer-motion・canvas-confetti等の使用が確認できるが、依存関係定義ファイルが存在しないため`npm install`すら実行できない。CIも存在しない。

**なぜ問題か**: このディレクトリは現状の形では本番デプロイもローカルでの再現ビルドも不可能。依存バージョンが一切固定されておらず、再現性がゼロ。

**改善案**: 実開発環境で使用している`package.json`（依存バージョン込み）と`tsconfig.json`をコミットする。`.gitignore`が意図せずこれらを除外していないか確認する。

---

#### [Critical] `family-quest/` — ユーザー識別がクライアント側state選択のみで、なりすまし・管理者機能への到達が誰でも可能

**対象箇所**
- `family-quest/src/App.tsx:69`（`useState(0)`でユーザー切替）
- `family-quest/src/components/layout/Header.tsx:29-33, 44-84`（アイコンタップで即切替、タイトルタップでAdmin Dashboard起動、権限チェックなし）
- `family-quest/src/features/admin/components/AdminDashboard.tsx`（権限チェック皆無）

**問題**: 家族アイコンをタップするだけで他人になりすませ、アプリタイトルを1回タップするだけで誰でもAdmin Dashboard（ボスHP改ざん、ファミリーマイレージ目標変更等）に到達できる。

**なぜ問題か**: 上記バックエンドの認可欠如（Critical#1）と組み合わさると、フロント・バックエンド双方に防御がなく、devtoolsを使わずとも通常のUI操作だけで他人のなりすまし・管理者操作が可能。

**改善案**: フロント側の緩和として、Admin Dashboard起動条件に`currentUser.user_id`が`dad`/`mom`であることのチェックを追加する（`App.tsx`の`onAdminOpen`）。ただし根本解決にはサーバー側認証が必須（Critical#1と同一の根本原因）。

---

#### [Critical] `family-quest/` — アバターアップロード機能が`apiClient.post()`のJSON強制シリアライズにより実際には動作しない

**対象箇所**
- `family-quest/src/lib/apiClient.ts:43-51`（`post()`が`Content-Type: application/json`固定、第3引数非対応）
- `family-quest/src/components/ui/AvatarUploader.tsx:40-42`（`(apiClient as any).post(...)`で型チェックを迂回し`FormData`を渡している）

**問題**: `FormData`を`JSON.stringify`すると`"{}"`相当になり、画像バイナリは一切送信されない。`Content-Type`も`multipart/form-data`に上書きされず`application/json`のまま送られる。

**なぜ問題か**: アバターアップロード機能自体が実装として成立していない。ユーザーには「アップロードに失敗しました」という`alert`のみが表示される。

**改善案**: `apiClient`に`postForm(endpoint, formData)`のような専用メソッドを追加し、`Content-Type`ヘッダーを明示せず（ブラウザにboundary付きで自動設定させる）`body: formData`をそのまま渡す実装にする。

```ts
// apiClient.ts に追加
async postForm<T>(endpoint: string, formData: FormData): Promise<T> {
  const res = await fetch(`${BASE_URL}${endpoint}`, { method: 'POST', body: formData });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}
```

---

#### [Critical] `family-quest/` — カメラのライブ映像・録画に認証が一切なく、URLパスを知るだけで誰でも閲覧可能

**対象箇所**: `family-quest/src/main.tsx:17-24`、`features/camera/components/CameraDashboard.tsx`、`LiveView.tsx`、`RecordView.tsx`（全体）

**問題**: `window.location.pathname.includes('/camera')`という条件のみでカメラダッシュボードがマウントされ、認証・認可コードが皆無。

**なぜ問題か**: バックエンド側にも認証がない前提（Critical#1と同根）のため、`/camera`を含むURLが分かれば第三者が自宅のライブ映像・過去録画を無制限に閲覧できる、家庭内プライバシーの重大リスク。

**改善案**: 最低限Basic認証やアクセストークンを導入する。フロント単体では緩和不可能で、バックエンド全体の認証導入が前提となる。

---

#### [High寄りCritical] `DDD/` — 個人的・機微なコンテンツ（画像生成プロンプト1000件超）がGitに無制限にコミットされている

**対象箇所**: `DDD/split_results/*.md`（約1000ファイル）、`DDD/一ノ瀬蓮_プロンプト1000選.md`、`DDD/画像生成プロンプト*.md`

**問題**: ルート`.gitignore`は`*.txt`/`*.jpg`/`*.png`等を除外するが**`*.md`は除外パターンに存在しない**ため、これらのファイルが無条件にトラッキングされている（`git check-ignore`で"NOT IGNORED"を確認済み）。内容はAI画像/動画生成用のロールプレイ・アダルト隣接コンテンツと見られる個人の私的創作物。

**なぜ問題か**: リポジトリが将来パブリック化される、第三者と共有される場合に機微な個人コンテンツが露見する。既にコミット済みのため、`.gitignore`修正だけでは既存分は除去されない。

**改善案**: `DDD/split_results/`と関連`.md`を`.gitignore`に追加し`git rm --cached`で追跡解除する。既にリモートにpush済みで公開・共有され得るなら、`git filter-repo`等による履歴からの完全消去を検討する（要リポジトリ所有者判断）。

Severity判定について: 直接の実害（システム破壊・金銭被害）はないが、機微な個人コンテンツの不可逆な漏洩リスクという点でCritical〜Highの境界にあり、ここではCriticalとして扱う。

---

### 🟠 High

#### [High] `family-quest/` — テストコードが皆無、ESLintが実質的に`.ts`/`.tsx`を対象外にしている

**対象箇所**: `family-quest/`全体（`*.test.*`/`*.spec.*`/`__tests__`検索で0件）、`family-quest/eslint.config.js:10`（`files: ['**/*.{js,jsx}']`）

**問題**: テストが1件も存在しない。さらにESLint設定の`files`グロブが`.js`/`.jsx`のみを対象にしており、実際のソースのほぼ全て（`.tsx`/`.ts`）がLint対象外になっている。`react-hooks/exhaustive-deps`等の有用なルールを含んでいるにも関わらず機能していない。

**なぜ問題か**: 品質保証がゼロの状態。後述の`useEffect`依存配列の潜在バグ（4.4/4.5）は本来この設定が有効なら検出できたはず。

**改善案**: `files: ['**/*.{js,jsx,ts,tsx}']`に変更し`typescript-eslint`のparserを追加する。主要フック・APIクライアントに最低限のユニットテストを追加する。

---

#### [High] `family-quest/` — `tsconfig.json`不在により型安全性の担保状況が不明、`as any`による型迂回が実在

**対象箇所**: `family-quest/`直下（`tsconfig.json`不在）、`AvatarUploader.tsx:40`（`(apiClient as any).post(...)`）

**問題**: TypeScriptの`strict`モード有無が確認できない。既に`as any`で型チェックを迂回している箇所が実害あるバグ（アバターアップロード）に直結している。

**改善案**: `tsconfig.json`をコミットし`strict: true`を有効化する。`as any`によるキャストを排除し、型不整合をコンパイル時に検出できる状態にする。

---

#### [High] `family-quest/` — `UserStatusCard`のHPが常に満タン固定でゲームのコア機能が未実装のまま

**対象箇所**: `family-quest/src/features/family/components/UserStatusCard.tsx:24-26`

```ts
const maxHp = (user.level * 10) + 50;
const currentHp = maxHp; // とりあえず満タン表示
```

**問題**: コメント通り仮実装のまま放置されており、実際のHP増減が画面に反映されない。

**なぜ問題か**: ボス戦というゲーム性の中核要素が機能しておらず、仕様と実装が一致していない。

**改善案**: バックエンドから実際のHP値を取得して表示するか、未実装であればUIからHPバー自体を一旦外す。

---

#### [High] `DDD/batch_download_discord.py` — 多重起動防止機構がなく、リストファイルの並行read-modify-writeで破損しうる

**対象箇所**: `DDD/batch_download_discord.py:409-470`（`_purge_skipped_tasks`）、`run()`全体

**問題**: PIDファイル/flock等の排他制御が一切ない。ダウンロードは数十分〜数時間かかりうるため、前回実行が終わる前に次回cronが起動すると、`list.txt`への読み取り→フィルタ→書き込みが競合し、パージ結果や更新内容が失われうる。

**改善案**: 起動時に`fcntl.flock`やPIDファイルによる多重起動防止を導入する。

---

#### [High] `DDD/extract_youtube_urls.py` — サブスクリプション巡回でレート制限を一切考慮していない

**対象箇所**: `DDD/extract_youtube_urls.py:372-375`（`process_subscriptions`ループ）

**問題**: チャンネルごとに`/videos`・`/playlists`・各プレイリストへの複数リクエストを、リクエスト間隔なしで連射する。`newface_monitor.py`が実装しているようなジッター付きsleepがない。

**なぜ問題か**: 登録チャンネル数が多い場合、YouTube側のBot検知・レート制限に引っかかりやすく、発生時は`except Exception:`（178-181行）で個別に握りつぶされ、巡回処理全体が実質機能不全になる。

**改善案**: チャンネル間・リクエスト間に指数バックオフ付きの待機を挿入し、連続エラー時はサーキットブレーカー的に中断・通知する。

---

#### [High] `DDD/newface_monitor.py` — `known_casts.json`の書き込みがアトミックでなく、中断時に通知ストームを誘発しうる

**対象箇所**: `DDD/newface_monitor.py:277-287`（`save_known_casts`）

**問題**: `open(..., 'w')`で直接上書きしており、`batch_download_discord.py`側にある一時ファイル+`Path.replace()`のアトミック書き込みパターンが使われていない。cron実行中のプロセスkill・NAS切断等で空/破損したJSONが残ると、次回実行時に既存キャスト全員が「新規」と誤検知され、Discordに大量の重複通知が飛ぶ。

**改善案**:
```python
tmp = data_file.with_suffix('.tmp')
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
tmp.replace(data_file)
```

---

### 🟡 Medium

以下は個別の障害・保守性への影響はあるが緊急性がCritical/Highほどではない項目。まとめて記載する。

| # | 対象 | 問題 | 改善案 |
|---|---|---|---|
| M1 | `MY_HOME_SYSTEM/tools/financial_service.py:14-27` | 実際の住宅ローン金額・金利スケジュールがソースにハードコード（git追跡対象） | 環境変数またはgitignore対象の設定ファイルへ移動 |
| M2 | `MY_HOME_SYSTEM/config.py:434-441` | 実在する子供の氏名・年齢がソースにハードコード | 2.3の「個人情報のgit管理」棚卸しに含めて扱う（config.pyは非gitignore対象のため設計的な検討が必要） |
| M3 | `MY_HOME_SYSTEM/handlers/ai_logic.py` | 死んだコードパスに、修正済みのはずのSQLテーブル無制限アクセス脆弱性（2.6相当）とバインドパラメータのバグが再度存在。現在は`line_logic.handle_message`が呼ばれておらず到達不能だが、将来の統合作業で誤って再有効化されるリスク | `ai_logic.py`と未使用の`line_logic.handle_message`を削除するか、明確にdeprecated化する |
| M4 | `MY_HOME_SYSTEM/routers/webhook_router.py` | SwitchBot Webhookの署名検証は`SWITCHBOT_WEBHOOK_TOKEN`が未設定の場合スキップされる（オプトイン方式） | 運用手順書に必須設定として明記、または未設定時に起動時警告を出す |
| M5 | `MY_HOME_SYSTEM/monitors/nas_monitor.py:99` | rsync呼び出しに`timeout`未指定 | `subprocess.run(..., timeout=...)`を追加 |
| M6 | `.github/workflows/test.yml` | lint/型チェックのステップがCIに存在しない | `ruff`/`mypy`等をCIに追加検討 |
| M7 | `family-quest/src/features/quest/components/ApprovalList.tsx:37` | `approver_id`が`'dad'`にハードコード（開発者コメントで「暫定」と自認） | `currentUser`をpropsで渡し実際のapprover_idを送信する |
| M8 | `family-quest/src/hooks/useGameData.ts`各mutation | サーバーからの詳細エラーメッセージ(`detail`)が握りつぶされ、UIには汎用「エラーが発生しました」のみ表示 | `apiClient`が投げる`Error.message`をmutationの`onError`で拾いモーダルに渡す |
| M9 | `family-quest/src/App.tsx:230-243` | 承認/却下失敗時にユーザーへの通知が一切ない（無言の失敗） | 失敗時に`setMessageData`を呼ぶよう追加 |
| M10 | `family-quest/src/hooks/useGameData.ts:170,184,198` | `history_id`のフォールバック(`history.id \|\| history.history_id`)が一部のmutationにのみ適用され不統一 | 全箇所で統一する |
| M11 | `family-quest/src/features/quest/hooks/useQuestStatus.ts`と`QuestList.tsx:238-245`、`App.tsx:126-142` | クエストのロック判定・ステータス判定ロジックが複数箇所に重複実装 | 共通関数/フックに集約する |
| M12 | `family-quest/src/features/family/components/WeeklyTrends.tsx`、`features/camera/components/CameraDashboard.tsx` | TanStack Queryを使わず生の`useEffect`+`fetch`でデータ取得 | `useQuery`に統一する |
| M13 | `family-quest/src/features/quest/hooks/useQuestStatus.ts`(useGameData内)と`ApprovalList.tsx:29-33` | `pendingInventory`クエリが2箇所で別々の`refetchInterval`とともに重複登録 | `ApprovalList`は`useGameData`が返す値をpropsで受け取る形に統一 |
| M14 | `family-quest/src/features/camera/components/CameraDashboard.tsx:14`、`RecordView.tsx:32` | `apiClient`を迂回した生`fetch('/api/...')`呼び出し | `apiClient.get()`経由に統一 |
| M15 | `family-quest/src/components/ui/AvatarUploader.tsx:19-29` | アップロードファイルのサイズ・MIME検証がフロント側にない | アップロード前にサイズ/typeチェックを追加 |
| M16 | `DDD/requirements.txt` | 実際に使用している`yt_dlp`/`tqdm`が記載されておらず`pip install -r`だけでは動作しない | 依存関係を追記、破壊的変更の多い`yt_dlp`はバージョン固定を推奨 |
| M17 | `DDD/split_prompts.py:18-35` | 出力ファイル名（番号+タイトル）が衝突した場合に無警告で上書き、データ欠落の恐れ | 書き込み前に既存ファイルチェックまたは一意なファイル名を採用 |
| M18 | `DDD/extract_youtube_urls.py:285-291` | 出力ファイルの`"w"`モード上書きにより過去の抽出結果が無警告で消える場合がある | 既存ファイルチェック＋警告、またはハッシュ/タイムスタンプでファイル名を一意化 |
| M19 | `DDD/newface_monitor.py:371-380` | `cast_id`生成時にURLクエリ文字列を除去していない。サイト側にトラッキングパラメータが付与されると毎回別人扱いされ通知が誤発生する潜在バグ | `href.split('?')[0].rstrip('/')`に修正 |

### 🔵 Low

以下は品質改善事項。まとめて記載する（詳細な再現条件・修正例は各エージェント調査結果に記録済みで、必要に応じて展開可能）。

- `MY_HOME_SYSTEM`: CIにlint/型チェック不在（再掲、Info寄り）、`google_photos_service.py`の未使用`import pickle`。
- `family-quest`: `alert()`/`confirm()`によるUI不統一（`AdminDashboard.tsx`ほか）、`any`型の多用（`FamilyLog.tsx`, `FamilyParty.tsx`, `EquipmentShop.tsx`等）、未使用デッドコード`ShopContainer.tsx`（型不整合あり）、`FamilyMileageCard.tsx`のconfetti再発火可能性、`RewardList.tsx`/`ApprovalList.tsx`のkey propフォールバック弱さ、Safari HLSパスでの`addEventListener`未解放、HLSエラー時に画面上のフィードバックがない、`AdminDashboard`フォームのlabel未紐付け（アクセシビリティ）、リポジトリに開発機へのWindowsショートカット(`.lnk`、社内IP記載)が混入。
- `DDD`: 例外ログで`exc_info`未使用の箇所が多数（一貫性）、`sanitize_filename`のロジック重複（DRY違反）、`check_disk_space`失敗時に「安全側」ではなく続行してしまうフォールバック、Discord Webhook失敗時のサーキットブレーカー欠如、ファイル名長制限の欠如、開発者個人パスのハードコード(`/home/masahiro/...`)。

### ⚪ Info（確認済み・問題なし）

- `MY_HOME_SYSTEM`: FK制約は`PRAGMA foreign_keys=ON`済み（3.1解決確認）、カメラ経路のパストラバーサル対策は`realpath`+`commonpath`チェックで実装済み（2.5解決確認）、`.env`/秘密情報のgit追跡なし、`eval`/`exec`/`pickle.loads`/`shell=True`はリポジトリ全体で不使用、`core/migrations.py`のバージョン管理マイグレーションは健全な設計、`config.py`は`os.getenv`ベースでデフォルト秘密値のハードコードなし、CIは`pytest`ベースに統一済み（5.1解決確認）。
- `family-quest`: XSS相当のパターン（`dangerouslySetInnerHTML`等）は検出されず、ハードコードされたAPIキー/シークレットなし、`hls.js`インスタンスのクリーンアップ（メインパス）は適切、`useQuestStatus.ts`のメモ化依存配列は正確、通貨計算に浮動小数点由来の不具合なし。
- `DDD`: ハードコードされた鍵/トークン/Webhook URLなし（すべて環境変数経由）、`shell=True`のsubprocess呼び出し不使用、`newface_monitor.py`はセッションクローズ・個別要素パースのフェイルソフト設計など4ファイル中最も堅牢。

---

## 3. 優先順位別まとめ

### 今すぐ修正すべきもの（🔴 Critical）
1. `MY_HOME_SYSTEM`: APIの認可欠如（認証方式の意思決定が必要）
2. `MY_HOME_SYSTEM`: IPアドレス制限ヘッダーの詐称可能性（インフラ側対応が必要）
3. `MY_HOME_SYSTEM`: Streamlitダッシュボードの無認証公開＋強制再起動ボタン（**新規、対応未着手**）
4. `MY_HOME_SYSTEM`: DBバックアップのGit履歴残存（履歴からの完全消去は所有者判断待ち）
5. `family-quest`: `package.json`/`tsconfig.json`不在によるビルド不可能状態
6. `family-quest`: ユーザーなりすまし・Admin Dashboardの無条件到達性
7. `family-quest`: アバターアップロード機能の実装不備（機能として動作しない）
8. `family-quest`: カメラ機能の無認証公開
9. `DDD`: 機微な個人コンテンツ(`split_results/`等)のGit無制限追跡

### 次のリリースまでに修正すべきもの（🟠 High）
10. `family-quest`: テスト皆無・ESLintが`.ts`/`.tsx`を対象外
11. `family-quest`: `tsconfig.json`不在・型迂回(`as any`)の実在
12. `family-quest`: `UserStatusCard`のHP固定表示（機能未実装）
13. `DDD`: `batch_download_discord.py`の多重起動防止欠如
14. `DDD`: `extract_youtube_urls.py`のレート制限未考慮
15. `DDD`: `newface_monitor.py`の非アトミック書き込みによる通知ストームリスク

### 将来的に改善すべきもの（🟡 Medium〜🔵 Low）
上記「2. 指摘事項」のMedium/Low表に列挙した全項目（M1〜M19、およびLowの各項目）。

---

## 4. 総合評価

3つの性質の異なるコードベース（本番運用中の家庭内サーバー、そのSPA、開発者個人用スクリプト）を横断するため、リスクで重み付けした総合スコアを示す。

| 項目 | 点数 | コメント |
|---|---:|---|
| 正確性 | 11/20 | `family-quest`のHP固定表示・承認者IDハードコード等、仕様と実装の不一致が複数の層で見つかった。`MY_HOME_SYSTEM`側は前回レビューで指摘された異常系の不具合は概ね解消済み。 |
| セキュリティ | 3/20 | `MY_HOME_SYSTEM`のAPI認可欠如・IP詐称に加え、新規発見のStreamlitダッシュボード無認証公開、`family-quest`のなりすまし放題・カメラ無認証公開が積み重なり、システム全体としては「誰でも管理者になれる・誰でも家族の映像が見られる」状態に近い。家庭内利用であっても本番運用の基準では最低評価域。 |
| パフォーマンス | 10/15 | `MY_HOME_SYSTEM`のインデックス不足は既に対応済み。`DDD`のレート制限未考慮、`family-quest`のクエリ重複登録など、規模拡大時に劣化しうる箇所が残る。 |
| 設計 | 10/15 | レイヤー分離（`MY_HOME_SYSTEM`）、feature-basedフォルダ構成（`family-quest`）、Strategyパターン（`DDD`）など、いずれも土台は明快。一方でロジック重複（`family-quest`のクエスト判定）、死んだコードの放置（`ai_logic.py`、`ShopContainer.tsx`）が保守性を下げている。 |
| 保守性 | 5/10 | `family-quest`の`package.json`不在は保守性以前に開発継続性そのものに関わる問題。`DDD`のDRY違反、`MY_HOME_SYSTEM`の実行時マイグレーション残存は軽微。 |
| テスト | 3/10 | `family-quest`はテストが1件も存在しない。`MY_HOME_SYSTEM`は前回レビューでテスト整備が進み6ケース以上が機能。`DDD`はテスト文化自体がない（個人スクリプトのため許容範囲）。 |
| 可観測性・運用性 | 7/10 | `MY_HOME_SYSTEM`のログ設計・通知連携は引き続き良好。`family-quest`はエラー時のユーザー通知が握りつぶされがちで運用時の切り分けが難しい。`DDD`は一部でDiscord通知連携あるが不統一。 |
| **合計** | **49/100** | |

---

## 5. 最大の問題 TOP 5

1. **`MY_HOME_SYSTEM`のAPI認可欠如と、それに呼応する`family-quest`側のなりすまし放題UI** — バックエンドとフロントエンドの双方に防御がなく、通常のUI操作だけで他人になりすまし・管理者操作・カメラ映像閲覧が可能。家族の個人情報とゲーム内資産の整合性が実質無防備。
2. **新規発見: Streamlitダッシュボード(`0.0.0.0:8501`)が無認証で家族の健康記録を公開し、`sudo systemctl restart`ボタンを誰でも押せる状態** — 既存レビューが見落としていた、既存の2件のCriticalと同等以上のインパクトを持つ独立した攻撃対象面。
3. **`family-quest`に`package.json`/`tsconfig.json`が存在せず、ビルド・再現・型チェックが一切できない** — セキュリティ以前に、このディレクトリは現状の形では動かせない・検証できない状態にある。
4. **個人情報・機微コンテンツのGit管理不備が複数箇所で反復している** — `MY_HOME_SYSTEM/old/`のDBバックアップ（部分解決）、`config.py`の子供の実名、`financial_service.py`の実際のローン情報、`DDD/split_results/`の機微な創作コンテンツ、いずれも「`.gitignore`のパターン漏れ」または「非gitignore対象ファイルへの直書き」という同一のクラスの問題が繰り返されている。
5. **`family-quest`のアバターアップロード・カメラ機能など、一部のユーザー向け機能が実装として成立していない** — `apiClient.post()`の型迂回によりアップロードが機能せず、`UserStatusCard`のHPは常に満タン固定。仕様と実装の乖離がユーザー体験に直接影響している。

---

## 6. 良い点

- **`MY_HOME_SYSTEM`のログ・リトライ・フォールバック設計**: `SilencePolicyFilter`によるログ抑制、NASマウント遅延へのExponential Backoff、`switchbot_service.py`/`ai_service.py`の外部API障害への配慮は、前回レビュー時から継続して高品質。
- **`MY_HOME_SYSTEM`の是正対応の速さと正確さ**: 前回レビューで指摘された13件（FK有効化、パストラバーサル対策、Webhook署名検証、テーブルアクセス制御、CIのpytest化等）が実際にコードレベルで正しく修正されていることを本レビューで独立して確認した。特にカメラのパストラバーサル対策は`realpath`+`commonpath`という適切な実装になっている。
- **`family-quest`のAPI層・フック・コンポーネントの分離**: `apiClient.ts`にHTTP通信を集約し、TanStack Queryのキャッシュ無効化（`invalidateQueries`）が主要なmutation後に一貫して呼ばれている設計は堅実。feature-basedのフォルダ構成も妥当。
- **`family-quest`のHLSプレイヤーのリソース管理**: `hls.js`インスタンスの`destroy()`によるクリーンアップ（メインパス）は適切に実装されており、メモリリークの主要因は回避されている。
- **`DDD/newface_monitor.py`の堅牢性**: `try/finally`による確実なセッションクローズ、個別要素パースエラーの分離、ストレージウォームアップの指数バックオフなど、個人スクリプトとしては非常に丁寧な作り。
- **`DDD/batch_download_discord.py`のアトミックなファイル書き込み**: 一時ファイル+`Path.replace()`パターンをリストファイル更新に採用しており、同種の処理を行う`newface_monitor.py`より堅牢（後者への横展開を推奨）。

---

## 7. 最終結論

### 🔴 REJECT（現状の全体としては）／ 領域別の内訳:
- `MY_HOME_SYSTEM`: 🟠 REQUEST CHANGES（前回レビューからの継続。Critical 4件のうち3件が未解決、1件新規発見）
- `family-quest`: 🔴 REJECT（ビルド不可能な状態に加え、なりすまし・アバター機能不全・カメラ無認証公開という複数のCriticalが未対応）
- `DDD`: 🟡 APPROVE WITH CHANGES（個人スクリプトとしての完成度は高いが、機微コンテンツのGit管理と一部の堅牢性不足は是正すべき）

**判断理由**: バックエンド（`MY_HOME_SYSTEM`）とフロントエンド（`family-quest`）の双方に認可・認証の欠如という同根の問題があり、家族の個人情報・ゲーム内資産・防犯カメラ映像が実質無防備な状態にある。加えて`family-quest`はビルド設定自体を欠いており、レビュー対象コードが実際に動作・検証可能な状態にすらない。個々の実装（ログ設計、リトライ、レイヤー分離）には評価できる点が多いため大規模な作り直しは不要だが、認可の実装・`package.json`のコミット・Streamlitダッシュボードの遮断という3点は、他の機能開発より優先して着手すべきである。

---

## 8. 補足: レビュー方法論に関する注記

- 本レポートは3体の調査エージェント（`DDD/`担当、`family-quest/`担当、`MY_HOME_SYSTEM/`再検証担当）による全文読解ベースの調査結果を、レビュー実施者が統合・重複排除・重要度再判定した上で作成した。
- `MY_HOME_SYSTEM/`について、前回レポート（`CODE_REVIEW_REPORT.md`）で既に✅対応済みとされ、かつ本レビューでも独立に確認できた項目（FK有効化、パストラバーサル対策、Webhook署名検証、SQLテーブル許可リスト、CI pytest化等）は本レポートでは再掲を最小限にとどめた。詳細根拠は同ファイルおよび本レビューのPart A/B調査ログを参照。
- 仕様書は提示されていないため、コードから読み取れない運用体制・想定脅威モデル（例: 本当にインターネットに公開する意図があるか、家族以外の第三者がネットワークに到達しうるか）については「仕様不明のため判断不能」とし、断定を避けた。特にSeverity判定は「技術的に可能な攻撃」を基準にしており、実際の運用環境（Cloudflare Access等）による緩和効果はコードからは確認できていない点に留意されたい。
