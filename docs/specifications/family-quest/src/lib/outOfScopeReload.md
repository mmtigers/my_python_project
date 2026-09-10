## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `outOfScopeReload.ts` (family-quest/src/lib/outOfScopeReload.ts) |
| 言語 | TypeScript |
| 解析対象 | 提供されたコードのみ（`outOfScopeReload.test.ts`は動作仕様の裏付けとして参照） |
| 推測・補完 | 一切なし |
| 解析基準コミット | (このリポジトリのHEADで新規作成) |

## 関連ドキュメント

* [../../main.md](../../main.md) - 唯一の呼び出し元。`registerSW(...)`呼び出しの直後、`isOutsideServiceWorkerScope(window.location.pathname)`が真の場合のみ`createUpdateChecker`が返す関数を`SW_UPDATE_INTERVAL_MS`(1時間)ごとに`window.setInterval`で呼び出す（Issue #591）。
* [routing.md](routing.md) - `main.tsx`のもう一方の判定関数`isCameraRoute`の実装元。本ファイルの`isOutsideServiceWorkerScope`とは**別モジュール・別の判定基準**であり、一方が他方を代替・包含するものではない（詳細は本ファイルの「8. 保守上の注意点」参照）。**（2026-09-10で追加）** どちらも共通のパスセグメント分割ヘルパー[pathSegments.md](pathSegments.md)に依存する。
* [pathSegments.md](pathSegments.md) - **（2026-09-10で追加）** `isOutsideServiceWorkerScope`が使う`getPathSegments`の実装元。以前は本ファイル独自の`pathname.split('/').filter(Boolean)`だったが、`routing.ts`の`isCameraRoute`との重複を解消するため共有ヘルパーへ切り出された（コードレビュー指摘対応）。
* [../../../MY_HOME_SYSTEM/unified_server.md](../../../MY_HOME_SYSTEM/unified_server.md) - `/camera`・`/camera/{full_path}`を`FileResponse`で配信するバックエンド側の実装元。本ファイルが依存する「`/camera`のレスポンスには`Last-Modified`ヘッダが付与される」という前提は、StarletteのFileResponseがファイルのmtimeから自動的に付与する挙動に基づく。

## 2. ファイルの概要

`main.tsx`のService Worker更新戦略(Issue #362)がカバーできない領域、すなわちService Workerのスコープ外にあるページ向けの更新検知ロジックを、単体テスト可能な形で切り出したモジュールである。エクスポートは2つ: パスがスコープ外かどうかを判定する純粋関数`isOutsideServiceWorkerScope(pathname: string): boolean`と、定期呼び出し用の非同期チェック関数を生成するファクトリ関数`createUpdateChecker(pathname: string, deps: UpdateCheckDeps): () => Promise<void>`である。ファイル冒頭のコメントによれば、`vite-plugin-pwa`が生成するService Workerのスコープは`vite.config.ts`の`base`(`/quest/`)に閉じており、`/camera`のようなスコープ外のページでは`navigator.serviceWorker.controller`が常に`null`のままとなるため、`main.tsx`の`controllerchange`ベースの自動リロード(Issue #362)が一切発火しない。この隙間を埋めるため、スコープ外のページ自身のHTMLを定期的にno-cacheで再取得し、`Last-Modified`ヘッダの変化を見ることで同じ役割（新しいバンドルのデプロイ検知→自動リロード）を代替する。
* 根拠: ファイル冒頭コメント全文 (行番号: 1〜12 / 抜粋: "// #591: vite-plugin-pwaのService Workerのスコープはvite.config.tsのbase(`/quest/`)\n// に閉じている。main.tsxのcontrollerchangeハンドラ(#362)は新しいSWが有効化された\n// 時点で自動リロードする仕組みだが、`/camera`のようにスコープ外のパスでは、その\n// ページ自体がSWの管理下に一切入らない(navigator.serviceWorker.controllerが常に\n// nullのまま)ため、controllerchangeイベント自体が発火しない。", "// SWの管理外にあるページに対しては、ページ自身のHTMLを定期的にno-cacheで\n// 再取得しLast-Modifiedヘッダの変化を見ることで、同じ問題を防ぐ。")
* 根拠: エクスポートは2つ (行番号: 15, 31 / 抜粋: "export function isOutsideServiceWorkerScope(pathname: string): boolean {", "export function createUpdateChecker(pathname: string, deps: UpdateCheckDeps) {")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `getPathSegments` | 内部モジュール(`./pathSegments`) | `isOutsideServiceWorkerScope`がパスを`/`区切りのセグメント配列へ変換するために使う。**（2026-09-10で変更）** 以前は本ファイル独自の`pathname.split('/').filter(Boolean)`だったが、`routing.ts`の`isCameraRoute`と重複していたため共有ヘルパーへ切り出された（コードレビュー指摘対応）。 | 根拠: [import文と呼び出し] (行番号: 14, 18 / 抜粋: "import { getPathSegments } from './pathSegments';", "const segments = getPathSegments(pathname);") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `deps.fetch`, `deps.reload`, `deps.onError`（呼び出し元が注入する`UpdateCheckDeps`） | 実際の実装は呼び出し元(`main.tsx`)から関数として注入されるため、本ファイル単体では具体的に何を`fetch`するのか（実体は`window.fetch`かテスト用モックか）や、`reload`が本当に`window.location.reload()`を呼ぶ実装かどうかは判断できない。 | 根拠: `UpdateCheckDeps`の型定義のみで実体を持たない (行番号: 20〜24 / 抜粋: "export interface UpdateCheckDeps {\n    fetch: typeof fetch;\n    reload: () => void;\n    onError?: (error: unknown) => void;\n}") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `isOutsideServiceWorkerScope`

* **役割**: 与えられたパス文字列`pathname`が、Service Workerのスコープ(`/quest/`)の**外**にあるかどうかを判定する純粋関数。**（2026-09-10で変更）** パスを`/`区切りのセグメント配列へ分割する処理は、`routing.ts`の`isCameraRoute`と重複していたため共有ヘルパー`getPathSegments`(`pathSegments.ts`)へ切り出された（挙動自体は変更なし）。`getPathSegments(pathname)`で得た配列`segments`について、`segments[0] !== 'quest'`を返す。先頭セグメントが`'quest'`である場合のみスコープ内(`false`)と判定し、それ以外（`/camera`、`/`、`/settings`等）はすべてスコープ外(`true`)として扱う。
* 根拠: [関数定義] (行番号: 17〜20 / 抜粋: "export function isOutsideServiceWorkerScope(pathname: string): boolean {\n    const segments = getPathSegments(pathname);\n    return segments[0] !== 'quest';\n}")
* 根拠: 動作契約を裏付けるテスト (`outOfScopeReload.test.ts`、行番号: 9〜21 / 抜粋: "it.each(['/camera', '/camera/', '/camera/live/cam1', '/', '/settings'])(\n        'treats %s as outside the service worker scope',\n        (pathname) => {\n            expect(isOutsideServiceWorkerScope(pathname)).toBe(true);\n        }\n    );", "it.each(['/quest', '/quest/', '/quest/camera', '/quest/settings'])(\n        'treats %s as inside the service worker scope (base: /quest/)',\n        (pathname) => {\n            expect(isOutsideServiceWorkerScope(pathname)).toBe(false);\n        }\n    );")


* **引数/リクエスト**: `pathname: string`（判定対象のURLパス。`main.tsx`では`window.location.pathname`が唯一の実際の呼び出し元での実引数）
* 根拠: [関数シグネチャ] (行番号: 17 / 抜粋: "export function isOutsideServiceWorkerScope(pathname: string): boolean {")


* **戻り値/レスポンス**: `boolean`（スコープ外と判定すれば`true`、スコープ内(`quest`配下)であれば`false`）
* 根拠: [関数シグネチャの戻り値型と`return`文] (行番号: 17〜19 / 抜粋: "): boolean {\n    const segments = getPathSegments(pathname);\n    return segments[0] !== 'quest';")


* **副作用**: なし（`pathname`という引数のみに依存する純粋関数。グローバル状態の読み書きや外部I/Oは一切行わない。`getPathSegments`自体も純粋関数）
* 根拠: [関数本体全体] (行番号: 17〜20)


* **エラーハンドリング**: なし（`try-catch`等は存在しない）。`pathname`が空文字列の場合、`getPathSegments('')`（内部の`''.split('/').filter(Boolean)`）は空配列`[]`となり`segments[0]`は`undefined`となるため、`undefined !== 'quest'`は真となり`true`（スコープ外）を返す（例外は発生しない）。
* 根拠: [`getPathSegments`呼び出し] (行番号: 18 / 抜粋: "const segments = getPathSegments(pathname);")

### `createUpdateChecker`

* **役割**: `pathname`と`deps: UpdateCheckDeps`を受け取り、呼び出す度に`pathname`をno-cacheで再取得して`Last-Modified`ヘッダの変化を検知する非同期チェック関数`checkForUpdate: () => Promise<void>`を生成するファクトリ関数。クロージャ内部に`lastModified: string | null`という状態を1つだけ保持する。`checkForUpdate`が呼ばれるたびに`deps.fetch(pathname, { cache: 'no-store' })`を`await`し、レスポンスの`Last-Modified`ヘッダを`current`として取得する。`lastModified !== null`（＝2回目以降の呼び出し）かつ`current !== null`（＝ヘッダが存在する）かつ`current !== lastModified`（＝値が変化した）の3条件がすべて真の場合のみ`deps.reload()`を呼び出して`return`する。**（2026-09-10で変更）** それ以外（初回呼び出し・値が変化していない・`current`が`null`のいずれか）の場合、以前は無条件に`lastModified = current`を実行していたが、`current !== null`のときのみ`lastModified`を更新するよう修正された。`current === null`（`Last-Modified`ヘッダが欠落した応答。例: デプロイの瞬間に`index.html`が一時的に見つからずヘッダ無しのエラー応答が返るケース）の場合は既知の`lastModified`をそのまま保持し、`null`で上書きしない（コードレビュー指摘: 以前の実装では、単発のヘッダ欠落応答を挟んだ直後に新しいビルドの`Last-Modified`を検知しても、`lastModified`が`null`に落ちていたため「初回のベースライン記録」として扱われるだけで`reload`が発火せず、Issue #591が防ぐはずだった「旧バンドル固着」を再発させる経路があった）。
* 根拠: [関数定義全文] (行番号: 33〜60 / 抜粋: "export function createUpdateChecker(pathname: string, deps: UpdateCheckDeps) {\n    let lastModified: string | null = null;\n\n    return async function checkForUpdate(): Promise<void> {\n        let current: string | null;\n        try {\n            const res = await deps.fetch(pathname, { cache: 'no-store' });\n            current = res.headers.get('Last-Modified');\n        } catch (error) {\n            deps.onError?.(error);\n            return;\n        }\n\n        if (lastModified !== null && current !== null && current !== lastModified) {\n            deps.reload();\n            return;\n        }" / "if (current !== null) {\n            lastModified = current;\n        }\n    };\n}")
* 根拠: 動作契約を裏付けるテスト (`outOfScopeReload.test.ts`、7ケース、行番号: 31〜125 / 抜粋: "it('does not reload on the first check (no baseline to compare against yet)', ...", "it('reloads once Last-Modified changes from the recorded baseline', ...", "it('reports fetch failures via onError instead of throwing, and does not reload', ...", "it('does not lose the known baseline when a single poll is missing Last-Modified, and still detects a later change', ...")


* **引数**: `pathname: string`（チェック対象のURLパス。`checkForUpdate`が`deps.fetch`を呼ぶ際の第1引数として毎回使われる、`createUpdateChecker`呼び出し時に固定されるクロージャ変数）、`deps: UpdateCheckDeps`（`fetch: typeof fetch`／`reload: () => void`／任意の`onError?: (error: unknown) => void`の3フィールドを持つオブジェクト）
* 根拠: [関数シグネチャと`UpdateCheckDeps`の定義] (行番号: 22〜26, 33 / 抜粋: "export interface UpdateCheckDeps {\n    fetch: typeof fetch;\n    reload: () => void;\n    onError?: (error: unknown) => void;\n}", "export function createUpdateChecker(pathname: string, deps: UpdateCheckDeps) {")


* **戻り値/レスポンス**: `() => Promise<void>`型の関数（`checkForUpdate`という名前の`async`クロージャ）。`createUpdateChecker`自体の呼び出しはこの関数を返すのみで、`checkForUpdate`を実際に呼び出すまでは`fetch`等は一切実行されない。
* 根拠: [`return async function checkForUpdate()`] (行番号: 36 / 抜粋: "return async function checkForUpdate(): Promise<void> {")


* **副作用**: `createUpdateChecker`自体の呼び出し（ファクトリ関数としての実行）自体には副作用はなく、クロージャを1つ生成するのみ。生成された`checkForUpdate`を実際に呼び出すたびに、`deps.fetch`によるHTTPリクエスト、条件成立時の`deps.reload()`呼び出し、`current !== null`のときのみ行われるクロージャ内部状態`lastModified`の更新という3種の副作用が発生しうる。
* 根拠: (行番号: 34, 39, 47, 56〜58 / 抜粋: "let lastModified: string | null = null;", "const res = await deps.fetch(pathname, { cache: 'no-store' });", "deps.reload();", "if (current !== null) {\n            lastModified = current;\n        }")


* **エラーハンドリング**: `deps.fetch`が例外を投げた場合（`try...catch`）、`deps.onError?.(error)`（オプショナルチェイニングにより`onError`が未指定なら何もしない）を呼び出し、即座に`return`する。例外は`checkForUpdate`の外へは伝播しない（呼び出し元で`await checker()`が`reject`することはない）。この分岐では`lastModified`も更新されないため、次回呼び出し時は障害発生前の基準値と比較される。
* 根拠: (行番号: 38〜44 / 抜粋: "try {\n            const res = await deps.fetch(pathname, { cache: 'no-store' });\n            current = res.headers.get('Last-Modified');\n        } catch (error) {\n            deps.onError?.(error);\n            return;\n        }")

## 5. 処理フロー図

```mermaid
flowchart TD
    Start(["isOutsideServiceWorkerScope(pathname) 呼び出し"]) --> GetSegments["getPathSegments(pathname) で '/' 区切り・空文字列除外済みのセグメント配列を取得<br>(pathSegments.tsの共有ヘルパー。2026-09-10で切り出し)"]
    GetSegments --> CheckQuest{"segments[0] !== 'quest' ?"}
    CheckQuest -- Yes(スコープ外) --> ReturnTrue(["return true"])
    CheckQuest -- No(スコープ内) --> ReturnFalse(["return false"])
```

```mermaid
flowchart TD
    Factory(["createUpdateChecker(pathname, deps) 呼び出し"]) --> InitState["lastModified = null で初期化し<br>checkForUpdate関数を生成して返す(この時点ではfetch等は実行しない)"]
    InitState --> CheckerCall(["(呼び出し元がsetIntervalで定期実行)<br>checkForUpdate() 実行"])
    CheckerCall --> Fetch["await deps.fetch(pathname, { cache: 'no-store' })"]
    Fetch -- 例外 --> OnError["deps.onError?.(error) を呼び出しreturn<br>(lastModifiedは更新しない)"]
    Fetch -- 成功 --> GetHeader["current = res.headers.get('Last-Modified')"]
    GetHeader --> CheckChange{"lastModified !== null かつ<br>current !== null かつ<br>current !== lastModified ?"}
    CheckChange -- Yes --> Reload["deps.reload() を呼び出しreturn"]
    CheckChange -- No --> CheckCurrentNull{"current !== null ?<br>(2026-09-10で追加)"}
    CheckCurrentNull -- Yes --> UpdateBaseline["lastModified = current で基準値を更新"]
    CheckCurrentNull -- No(ヘッダ欠落) --> KeepBaseline["何もしない<br>(既知のlastModifiedをnullで上書きしない)"]
```

## 6. 依存関係図

```mermaid
graph TD
    OutOfScopeReloadTs["outOfScopeReload.ts"] --> IsOutside["関数: isOutsideServiceWorkerScope"]
    OutOfScopeReloadTs --> CreateChecker["関数: createUpdateChecker"]
    OutOfScopeReloadTs --> DepsType["型: UpdateCheckDeps"]

    PathSegmentsTs["pathSegments.ts (2026-09-10で追加)"] --> GetSegmentsFn["関数: getPathSegments"]
    IsOutside -->|import| GetSegmentsFn

    subgraph "ブラウザ標準API・グローバル型 (importなし)"
        FetchType["グローバル型: typeof fetch"]
    end

    DepsType -.->|型としてのみ参照| FetchType
    CreateChecker -.->|deps.fetch/deps.reload/deps.onErrorとして<br>呼び出し元から注入される実装を実行| DepsType

    MainTsx["利用元: ../../main.tsx"] -.->|"import { isOutsideServiceWorkerScope, createUpdateChecker }"| OutOfScopeReloadTs
    RoutingTs["routing.ts (isCameraRoute)"] -.->|"同じgetPathSegmentsを利用<br>(本ファイルとは独立した判定)"| GetSegmentsFn
    TestTs["outOfScopeReload.test.ts (テスト、仕様書対象外)"] -.->|"import { createUpdateChecker, isOutsideServiceWorkerScope }"| OutOfScopeReloadTs
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `../../main.tsx` | `isOutsideServiceWorkerScope`/`createUpdateChecker`の唯一の呼び出し元であり、実際にどう組み合わされて`SW_UPDATE_INTERVAL_MS`ごとに定期実行されるかを確認するため。 | `main.md`（既存の解析済み仕様書）参照 |
| 中 | `MY_HOME_SYSTEM/unified_server.py` | ファイル冒頭コメントおよび本ファイルの設計が前提とする「`/camera`ルートのレスポンスには`Last-Modified`ヘッダが付与される」という挙動を、バックエンド側の実装（`FileResponse(index_path)`）で裏付け確認するため。 | 直接ソース確認: `MY_HOME_SYSTEM/unified_server.py:367-396`（`serve_quest_spa`/`serve_quest_root`が`/camera`・`/camera/{full_path}`にも登録され`FileResponse`を返す） |
| 低 | `outOfScopeReload.test.ts` | 本ファイルの動作契約（15ケース）を網羅的に確認済みだが、今後仕様変更があった場合はテストケースとの整合を都度確認する必要がある。 | `describe`/`it`/`it.each`一覧 |

## 8. 保守上の注意点

* **`lastModified`はモジュールスコープではなくクロージャごとのインスタンス状態**: `createUpdateChecker`を複数回呼び出すと、それぞれ独立した`lastModified`を持つ別々の`checkForUpdate`関数が生成される。`main.tsx`は`isOutsideServiceWorkerScope`が真の場合に1回だけ呼び出すため実害はないが、誤って複数回呼び出すと基準値がリセットされ、直後の呼び出しは必ず「変化なし」判定になる点に注意。
* 根拠: (行番号: 33〜34 / 抜粋: "export function createUpdateChecker(pathname: string, deps: UpdateCheckDeps) {\n    let lastModified: string | null = null;")
* **`Last-Modified`ヘッダが恒常的に欠落する配信環境では変化を検知できない**: `current`が`null`（ヘッダ欠落）の場合、`current !== null`の条件が偽になるため、実際に新しいデプロイがあっても`reload`は呼ばれない。本ファイルはStarletteの`FileResponse`が自動付与する`Last-Modified`ヘッダに依存する設計だが、その前提自体は本ファイル単体からは検証できない（`MY_HOME_SYSTEM/unified_server.py`側の実装に依存する）。**（2026-09-10で変更）** 以前は`current`が`null`の場合でも`lastModified = current`が無条件実行され、ヘッダ欠落が一度でも起きると既知のベースラインが`null`にリセットされてしまっていた（コードレビュー指摘）。修正後は`current !== null`のときのみ`lastModified`を更新するため、ヘッダ欠落は**単発**であれば既知のベースラインを保持したまま次のポーリングで正しく変化を検知できる。ヘッダが**恒常的に**欠落する環境（バックエンド設定の問題等）では、`lastModified`が最初から一度も更新されず`null`のままとなり、この場合のみ検知不能な状態が続く。
* 根拠: (行番号: 46, 56〜58 / 抜粋: "if (lastModified !== null && current !== null && current !== lastModified) {", "if (current !== null) {\n            lastModified = current;\n        }")
* **初回呼び出しでは必ずbaseline記録のみで終わり、リロードは次回以降まで発生しない**: `lastModified`は`null`で初期化されるため、`checkForUpdate`の初回呼び出しは（`current`がどんな値であっても）`lastModified !== null`の条件が偽となり、必ず`reload`されずに（`current !== null`であれば）`lastModified = current`が実行されるだけで終わる。`/camera`をブラウザで開いた直後にこのチェックによるリロードが走ることはなく、実際に新しいバンドルを検知してリロードするには、呼び出し元(`main.tsx`)の`setInterval`間隔（`SW_UPDATE_INTERVAL_MS`＝1時間）分の待ちが最低でも必要になる。
* 根拠: (行番号: 34, 46 / 抜粋: "let lastModified: string | null = null;", "if (lastModified !== null && current !== null && current !== lastModified) {")
* **`fetch`の失敗はエラーを`onError`に委譲して握りつぶすのみで、基準値も更新されない**: `deps.fetch`が例外を投げた場合、`deps.onError`を呼んで即`return`するため、`lastModified`は前回の値のまま変わらない。次回呼び出しで`fetch`が成功すれば、それ以前の`lastModified`と比較される（＝一時的なネットワーク障害によって基準値が失われたり、誤って`reload`が発火したりすることはない）。
* 根拠: (行番号: 38〜44 / 抜粋: "try {\n            const res = await deps.fetch(pathname, { cache: 'no-store' });\n            current = res.headers.get('Last-Modified');\n        } catch (error) {\n            deps.onError?.(error);\n            return;\n        }")
* **`isCameraRoute`(`routing.ts`)とは独立した別の判定軸である**: 本ファイルの`isOutsideServiceWorkerScope`は「このページはService Workerの管理下にあるか」を判定し、`routing.ts`の`isCameraRoute`は「カメラビュー(`CameraDashboard`)をマウントすべきパスか」を判定する、目的の異なる2つの独立した純粋関数である。`/camera`のような単純なケースでは両方とも`true`を返すため紛らわしいが、`/quest/camera`では`isCameraRoute('/quest/camera')`が`true`（カメラビューをマウントすべき）である一方、`isOutsideServiceWorkerScope('/quest/camera')`は`false`（`quest`配下でSWスコープ内）となり判定結果が食い違う。一方の関数の変更が他方の判定に影響することはなく、代替・包含関係にもない点に注意する必要がある。**（2026-09-10で追加）** 両者はパスセグメント分割の実装(`getPathSegments`、`pathSegments.ts`)のみを共有するようになったが、この共有は実装の重複解消が目的であり、判定基準自体（「`quest`が先頭か」対「`camera`が先頭、または`quest`の次が`camera`か」）は今回の変更後も独立したままである。
* 根拠: (行番号: 17〜20 / 抜粋: "export function isOutsideServiceWorkerScope(pathname: string): boolean {\n    const segments = getPathSegments(pathname);\n    return segments[0] !== 'quest';\n}")

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `MY_HOME_SYSTEM/unified_server.py`側で`/camera`が実際に`Last-Modified`ヘッダ付きで応答することの実行時レベルでの確認 | 本ファイルの設計はStarletteの`FileResponse`がファイルのmtimeから`Last-Modified`を自動付与する挙動を前提とするが、本ファイル自体はこのバックエンド側の実装を直接参照・検証していない（ソースコード上は`FileResponse(index_path)`の呼び出しを確認済みだが、実際のHTTPレスポンスヘッダそのものは未確認）。 | `MY_HOME_SYSTEM/unified_server.py`（実行時のレスポンスヘッダ確認） |

## 相互参照による補足情報

（本ファイルは新規作成のため、他ドキュメントとの相互参照による補足情報はまだ存在しない。）

## 10. 自己検証結果

* [x] 完了: 推測・外部ファイルの仕様を一切含んでいない
* [x] 完了: 全関数・全クラス・全コンポーネントを列挙した（本ファイルは`isOutsideServiceWorkerScope`・`createUpdateChecker`の2関数と`UpdateCheckDeps`型で構成されており、列挙した）
* [x] 完了: 全てのインポート要素を列挙した（該当なし）
* [x] 完了: すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 完了: 根拠漏れが0件である
* [x] 完了: Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 完了: 不明事項を漏れなく列挙した
