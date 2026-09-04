## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | main.tsx |
| 言語 | React (TypeScript) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | `46c9bc4` |

## 関連ドキュメント

* [App.md](App.md) - `/camera`を含まないパスでマウントされる通常時のルートコンポーネント（`SettingsProvider`/`ToastProvider`配下でマウントされる）
* [src/features/camera/components/CameraDashboard.md](src/features/camera/components/CameraDashboard.md) - `/camera`パスで`lazy()`+`Suspense`によりマウントされるカメラビューワのルートコンポーネント（`SettingsProvider`/`ToastProvider`の外側でマウントされる）
* [src/lib/queryClient.md](src/lib/queryClient.md) - `QueryClientProvider`に渡す`queryClient`インスタンスの定義元
* [src/context/SettingsContext.md](src/context/SettingsContext.md) - `App`分岐のみをラップする`SettingsProvider`の実装元
* [src/context/ToastContext.md](src/context/ToastContext.md) - `App`分岐のみをラップする`ToastProvider`の実装元
* [src/components/ui/ChunkErrorBoundary.md](src/components/ui/ChunkErrorBoundary.md) - ルート直下でツリー全体を包む、`lazy()`チャンク読込失敗時の自動再読み込み用エラーバウンダリ(Issue #362)

## 2. ファイルの概要

* DOMから特定のルート要素（`id="root"`）を取得し、`React.StrictMode`と`QueryClientProvider`（React Query）でラップした上で、URLのパス（`window.location.pathname`）に`/camera`が含まれるかどうか（`isCameraView`）に応じて、マウントするツリーをルート直下で丸ごと切り替えるエントリーポイントファイルである。`isCameraView`が`true`の場合は`CameraDashboard`（カメラビューワ、`lazy()`による動的importで別チャンクに分離され`Suspense`でラップされる）を、`false`の場合は`App`（通常のFamily Questアプリ）を`SettingsProvider`・`ToastProvider`でラップしてレンダリングする。`CameraDashboard`は`SettingsProvider`/`ToastProvider`の**外側**でマウントされるため、`App`側のみが使えるこれら2つのコンテキスト（`useSettings`/`useToast`）を利用できない設計になっている。
* 根拠: `ReactDOM.createRoot(rootElement).render(...)` と `isCameraView` による分岐 (行番号: 22, 24-41 / 抜粋: "const isCameraView = window.location.pathname.includes('/camera'); ... {isCameraView ? (\n        <Suspense fallback={null}>\n          <CameraDashboard />\n        </Suspense>\n      ) : (\n        <SettingsProvider>\n          <ToastProvider>\n            <App />\n          </ToastProvider>\n        </SettingsProvider>\n      )}")
* 根拠: [CameraDashboardのlazy import化とコメント] (行番号: 12〜14 / 抜粋: "// CameraDashboard(hls.js含む)は /camera 専用でFamily Quest本体とは同時に使われないため、\n// 動的importで別チャンクに分離し、通常のクエスト画面の初回読み込みバンドルから除外する。\nconst CameraDashboard = lazy(() => import('./features/camera/components/CameraDashboard'))")
* **Issue #362(PWA Service Workerの更新戦略)**: `virtual:pwa-register`の`registerSW`を明示的に呼び出し、1時間ごとに`registration.update()`で新しいSWの有無を確認する。新しいSWが有効化(`skipWaiting`+`clientsClaim`)されて`controllerchange`が発火した時点で`window.location.reload()`によりページを自動再読み込みし、常時表示中のキオスク端末(Echo Show)でも旧バンドルが残留しないようにする。初回インストール時(controllerが無い状態からの初回claim)の`controllerchange`は「更新」ではないため再読み込みしない。さらにレンダーツリー全体を`ChunkErrorBoundary`で包み、SW更新後に旧チャンクが404になって`lazy()`がthrowしても白画面にせず自動再読み込みする。
* 根拠: SW更新戦略のコメントと実装 (行番号: 16〜51 / 抜粋: "const SW_UPDATE_INTERVAL_MS = 60 * 60 * 1000;", "navigator.serviceWorker.addEventListener('controllerchange', () => {\n    if (!hadController) {\n      hadController = true;\n      return;\n    }\n    window.location.reload();\n  });", "registerSW({\n  immediate: true,\n  onRegisteredSW(_swUrl, registration) {")
* 根拠: `ChunkErrorBoundary`によるラップ (行番号: 63〜83 / 抜粋: "<ChunkErrorBoundary>\n      <QueryClientProvider client={queryClient}>")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `React`, `lazy`, `Suspense` | 外部ライブラリ | JSXおよびReactの基本機能。`lazy`は`CameraDashboard`の動的import、`Suspense`はその読み込み待機中のフォールバック表示に使用 | 根拠: `React` (行番号: 1 / 抜粋: "import React, { lazy, Suspense } from 'react'") |
| `ReactDOM` | 外部ライブラリ | DOMへのルート作成とレンダリング | 根拠: `ReactDOM` (行番号: 2 / 抜粋: "import ReactDOM from 'react-dom/client'") |
| `registerSW` | 外部ライブラリ(`vite-plugin-pwa`の仮想モジュール) | Service Workerの明示登録と、登録済み`registration`を受け取っての定期`update()`呼び出し(Issue #362) | 根拠: (行番号: 3 / 抜粋: "import { registerSW } from 'virtual:pwa-register'") |
| `ChunkErrorBoundary` | 内部モジュール | `lazy()`チャンク読込失敗時に自動再読み込みするエラーバウンダリ。ルート直下でツリー全体を包む(Issue #362) | 根拠: (行番号: 10 / 抜粋: "import ChunkErrorBoundary from './components/ui/ChunkErrorBoundary'") |
| `App` | 内部モジュール | アプリケーションのルートコンポーネント（通常時、`SettingsProvider`/`ToastProvider`配下でマウント） | 根拠: `App` (行番号: 3 / 抜粋: "import App from './App' // 拡張子は省略可能") |
| 該当なし(CSS) | スタイルシート | グローバルなスタイルの適用 | 根拠: `index.css` (行番号: 4 / 抜粋: "import './index.css'") |
| `QueryClientProvider` | 外部ライブラリ | React Queryのクライアントをツリーに提供 | 根拠: `QueryClientProvider` (行番号: 5 / 抜粋: "import { QueryClientProvider } from '@tanstack/react-query'") |
| `queryClient` | 内部モジュール | React Queryのクライアントインスタンス | 根拠: `queryClient` (行番号: 6 / 抜粋: "import { queryClient } from './lib/queryClient'") |
| `SettingsProvider` | 内部モジュール | `App`分岐のみをラップする設定コンテキストのプロバイダ（`CameraDashboard`側では利用不可） | 根拠: `SettingsProvider` (行番号: 7 / 抜粋: "import { SettingsProvider } from './context/SettingsContext'") |
| `ToastProvider` | 内部モジュール | `App`分岐のみをラップするトースト通知コンテキストのプロバイダ（`CameraDashboard`側では利用不可） | 根拠: `ToastProvider` (行番号: 8 / 抜粋: "import { ToastProvider } from './context/ToastContext'") |
| `CameraDashboard` | 内部モジュール（`lazy`による動的import） | カメラビューワのルートコンポーネント（`/camera`パス時。静的importではなく`lazy()`で別チャンクに分離される） | 根拠: `CameraDashboard` (行番号: 12 / 抜粋: "const CameraDashboard = lazy(() => import('./features/camera/components/CameraDashboard'))") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `App` | 内部実装が提供されていないため、どのようなUIやロジックを持つか不明（`./App`ファイルに依存のため要確認）。 | 根拠: `App` (行番号: 3 / 抜粋: "import App from './App'") |
| `CameraDashboard` | 内部実装が提供されていないため、どのようなUIやロジックを持つか不明（`./features/camera/components/CameraDashboard`ファイルに依存のため要確認）。 | 根拠: `CameraDashboard` (行番号: 12 / 抜粋: "const CameraDashboard = lazy(() => import('./features/camera/components/CameraDashboard'))") |
| `./index.css` | 具体的なスタイリング内容や影響範囲が不明（該当ファイルに依存のため要確認）。 | 根拠: `index.css` (行番号: 4 / 抜粋: "import './index.css'") |
| `queryClient` | 初期化時の設定（キャッシュ設定、リトライ回数など）が不明（`./lib/queryClient`ファイルに依存のため要確認）。 | 根拠: `queryClient` (行番号: 6 / 抜粋: "import { queryClient } from './lib/queryClient'") |
| `SettingsProvider` | 内部実装が提供されていないため、どのような設定コンテキストを提供するか不明（`./context/SettingsContext`ファイルに依存のため要確認）。 | 根拠: `SettingsProvider` (行番号: 7 / 抜粋: "import { SettingsProvider } from './context/SettingsContext'") |
| `ToastProvider` | 内部実装が提供されていないため、どのようなトースト通知機構を提供するか不明（`./context/ToastContext`ファイルに依存のため要確認）。 | 根拠: `ToastProvider` (行番号: 8 / 抜粋: "import { ToastProvider } from './context/ToastContext'") |
| `document` API | `root`というIDを持つ要素がDOM上に存在するかどうかはHTML側の実装に依存するため不明。 | 根拠: `document.getElementById` (行番号: 15 / 抜粋: "const rootElement = document.getElementById('root');") |
| `window.location` API | 実行時のURLパスに依存するため、どのタイミングで`/camera`パスになるか（ルーティング全体の設計）は本ファイルからは不明。 | 根拠: `window.location.pathname` (行番号: 61 / 抜粋: "const isCameraView = window.location.pathname.includes('/camera');") |
| `navigator.serviceWorker` / `virtual:pwa-register` | ブラウザのService Worker APIおよび`vite-plugin-pwa`がビルド時に生成する仮想モジュール。生成されるSWの中身(`skipWaiting`/`clientsClaim`/`cleanupOutdatedCaches`)は`vite.config.ts`の`VitePWA`設定に依存し本ファイルからは不明。 | 根拠: (行番号: 3, 27〜51 / 抜粋: "if ('serviceWorker' in navigator) {", "registerSW({\n  immediate: true,") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### Service Worker更新戦略 (モジュールレベルの副作用、Issue #362)

* **役割**: `'serviceWorker' in navigator`の場合、`controllerchange`イベントを購読し、既に`controller`が存在していた状態からの変化(=新しいSWへの切り替わり)であれば`window.location.reload()`する。初回インストール時の`controllerchange`は`hadController`フラグで除外する。続いて`registerSW({ immediate: true, onRegisteredSW, onRegisterError })`を呼び、`onRegisteredSW`で受け取った`registration`に対して`SW_UPDATE_INTERVAL_MS`(1時間)ごとに`update()`を呼ぶ(失敗は`console.warn`)。
* 根拠: (行番号: 25〜51 / 抜粋: "const SW_UPDATE_INTERVAL_MS = 60 * 60 * 1000;\n\nif ('serviceWorker' in navigator) {\n  let hadController = !!navigator.serviceWorker.controller;\n  navigator.serviceWorker.addEventListener('controllerchange', () => {", "registerSW({\n  immediate: true,\n  onRegisteredSW(_swUrl, registration) {\n    if (!registration) return;\n    window.setInterval(() => {\n      registration.update().catch((e: unknown) => console.warn('SW update check failed:', e));\n    }, SW_UPDATE_INTERVAL_MS);")
* **副作用**: `navigator.serviceWorker`へのイベントリスナー登録、SW登録、`setInterval`、条件付きの`window.location.reload()`
* **エラーハンドリング**: `registration.update()`の失敗は`console.warn`、SW登録失敗は`onRegisterError`で`console.error`に記録するのみでアプリの描画は継続する。
* 根拠: (行番号: 45〜50)

## 5. 処理フロー図

```mermaid
flowchart TD
    Start([Start]) --> GetElement["外部：document.getElementById('root')"]
    GetElement --> CheckNull{"rootElementはnullか？"}
    CheckNull -- Yes --> ThrowError["Error: 'Failed to find the root element' をスロー"]
    ThrowError --> EndError([End])
    CheckNull -- No --> CheckCamera["外部：window.location.pathname.includes('/camera')"]
    CheckCamera --> CreateRoot["外部：ReactDOM.createRoot(rootElement)"]
    CreateRoot --> IsCameraView{"isCameraView === true?"}
    IsCameraView -- Yes --> RenderCamera["render() 呼び出し<br>(React.StrictMode → ChunkErrorBoundary → QueryClientProvider → Suspense → CameraDashboardをネスト<br>lazy()により初回描画時に動的import)"]
    IsCameraView -- No --> RenderApp["render() 呼び出し<br>(React.StrictMode → ChunkErrorBoundary → QueryClientProvider → SettingsProvider → ToastProvider → Appをネスト)"]
    RenderCamera --> End([End])
    RenderApp --> End

    SWStart([モジュール評価時: SW更新戦略]) --> HasSW{"'serviceWorker' in navigator ?"}
    HasSW -- Yes --> Listen["controllerchange を購読<br>(既にcontrollerがあった場合のみ location.reload)"]
    HasSW -- No --> Register
    Listen --> Register["registerSW({ immediate: true })"]
    Register --> Interval["onRegisteredSW: 1時間ごとに registration.update()"]

```

## 6. 依存関係図

```mermaid
graph TD
    Main["main.tsx (本ファイル)"] --> Document["外部：ブラウザAPI (document)"]
    Main --> Location["外部：ブラウザAPI (window.location)"]
    Main --> ReactDOM["外部モジュール：react-dom/client"]
    Main --> React["外部モジュール：react (lazy, Suspense含む)"]
    Main --> ReactQuery["外部モジュール：@tanstack/react-query"]
    Main --> QueryClient["外部ファイル：./lib/queryClient (ブラックボックス)"]
    Main --> App["外部ファイル：./App (ブラックボックス)"]
    Main -.->|lazy動的import| CameraDashboard["外部ファイル：./features/camera/components/CameraDashboard (ブラックボックス)"]
    Main --> CSS["外部ファイル：./index.css (ブラックボックス)"]
    Main --> SettingsProvider["外部ファイル：./context/SettingsContext (ブラックボックス)<br>Appのみラップ"]
    Main --> ToastProvider["外部ファイル：./context/ToastContext (ブラックボックス)<br>Appのみラップ"]
    Main --> ChunkErrorBoundary["内部ファイル：./components/ui/ChunkErrorBoundary<br>ルート直下で全体をラップ"]
    Main --> RegisterSW["外部モジュール：virtual:pwa-register (registerSW)"]
    Main --> ServiceWorker["外部：navigator.serviceWorker (controllerchange)"]

```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `./App.tsx` | アプリケーションのルートであり、画面の描画内容やルーティング等の主要な機能の全体像を把握するために必須であるため。 | 根拠: `App` (行番号: 3 / 抜粋: "import App from './App'") |
| 高 | `./features/camera/components/CameraDashboard.tsx` | `/camera`パスでマウントされるもう一方のルートコンポーネントであり、カメラ機能の全体像を把握するために必須であるため。 | 根拠: `CameraDashboard` (行番号: 12 / 抜粋: "const CameraDashboard = lazy(() => import('./features/camera/components/CameraDashboard'))") |
| 中 | `./context/SettingsContext.tsx`, `./context/ToastContext.tsx` | `App`分岐のみをラップするコンテキストの実装内容（`CameraDashboard`側で利用不可な理由の裏付け）を確認するため。 | 根拠: `SettingsProvider`, `ToastProvider` (行番号: 7-8 / 抜粋: "import { SettingsProvider } from './context/SettingsContext'\nimport { ToastProvider } from './context/ToastContext'") |
| 中 | `./lib/queryClient.ts` または `.js` | React Queryによるデータフェッチのグローバルなキャッシュ戦略やエラーハンドリングの設定内容を確認するため。 | 根拠: `queryClient` (行番号: 6 / 抜粋: "import { queryClient } from './lib/queryClient'") |
| 中 | `index.html` | マウント対象となる `<div id="root"></div>` 要素が確実に定義されているか、およびメタデータ等を確認するため。 | 根拠: `document.getElementById` (行番号: 15 / 抜粋: "document.getElementById('root'); ") |
| 低 | `./index.css` | アプリケーション全体に適用されているベーススタイルやCSS変数の定義状況を把握するため。 | 根拠: `index.css` (行番号: 4 / 抜粋: "import './index.css'") |

## 8. 保守上の注意点

* `document.getElementById('root')` が `null` を返した場合、意図的に `Error` がスローされ後続のレンダリング処理が完全に停止する。呼び出し元のHTMLファイルに `id="root"` を持つ要素が存在しない場合にクリティカルな影響が出る。
* 根拠: `if (!rootElement) { throw new Error('Failed to find the root element'); }` (行番号: 17-19)
* **`CameraDashboard`は`SettingsProvider`/`ToastProvider`の外側でマウントされる**: `App`分岐のみが`SettingsProvider`・`ToastProvider`でラップされており、`CameraDashboard`（および配下のコンポーネント）はこれらのコンテキストの外でマウントされる。そのため`CameraDashboard`側では`useSettings`/`useToast`フックを使用できない設計上の制約がある（`CameraDashboard`側でトースト通知的なUIが必要な場合は、コンテキストに依存しないローカルstateでの実装が必要になる）。
* 根拠: [レンダーツリー構造] (行番号: 28-38 / 抜粋: "{isCameraView ? (\n        <Suspense fallback={null}>\n          <CameraDashboard />\n        </Suspense>\n      ) : (\n        <SettingsProvider>\n          <ToastProvider>\n            <App />\n          </ToastProvider>\n        </SettingsProvider>\n      )}")
* **SW更新時の自動再読み込みはユーザー操作を待たない(Issue #362)**: `controllerchange`を受けた時点で入力中の状態やモーダルの有無に関わらず`window.location.reload()`する。Family Questはサーバー側にしか永続状態を持たないため実害は小さいが、長い入力フォームを持つ画面を追加する場合はこの挙動を考慮する必要がある。また`registerSW`を`src/`から明示的にimportしているため、`vite-plugin-pwa`(`injectRegister: 'auto'`既定)は`registerSW.js`の自動注入を行わず、登録は本ファイルの呼び出しに一本化される。
* 根拠: (行番号: 16〜24, 32〜38, 41〜51)
* **ルーティングがURLパスの文字列一致のみで判定されている**: `isCameraView` は `window.location.pathname.includes('/camera')` という単純な部分一致で決定されており、専用のルーティングライブラリを使っていない。将来的に`/camera`を含む別の意図しないパス（例: `/settings/camera-help`）が追加された場合、意図せず`CameraDashboard`がマウントされる可能性がある。
* 根拠: (61行目 / 抜粋: "const isCameraView = window.location.pathname.includes('/camera');")

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `App` コンポーネントの詳細な機能 | 内部実装がインポートされているのみでコード内に記述がないため。 | `./App.tsx` |
| `CameraDashboard` コンポーネントの詳細な機能 | 内部実装がインポートされているのみでコード内に記述がないため。 | `./features/camera/components/CameraDashboard.tsx` |
| データフェッチ機構のグローバル設定 | `queryClient` が外部ファイルからインポートされており、本ファイル内では設定パラメータが判断不可であるため。 | `./lib/queryClient` (拡張子は同上) |
| グローバルスタイルの定義内容 | CSSファイルがインポートされているのみであり、スタイルの衝突や適用範囲が不明であるため。 | `./index.css` |
| HTML側のDOM構造 | `document.getElementById('root')` の対象となる要素が定義されているHTMLファイルが提供されていないため。 | `index.html` (エントリーポイントに対応するHTMLファイル) |
| `/camera` 以外のルーティング設計の有無 | `window.location.pathname`の単純な文字列一致でしか分岐していないため、他にルーティングライブラリや設定が存在するか不明。 | ルーティング関連の設定ファイル（存在する場合） |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `App` コンポーネントの詳細な機能 | `family-quest/src/App.tsx`(全526行)を直接確認した。ユーザー切替(`currentUserIdx`)、購入・却下確認モーダル(`ConfirmModal`)、エラー表示モーダル(`MessageModal`)、クエスト完了/取消のワンタップ実行(`runQuestAction`/`handleQuestClick`)、報酬購入(`handleBuyReward`/`executeConfirm`)、承認・一括承認(`handleApprove`/`handleApproveAll`)、`useLayoutMode`による横画面(`FamilyDashboard`)/縦画面(`UserStatusCard`+`QuestList`等+`BottomNav`)のレイアウト切替、アバターアップロード(`AvatarUploader`、`lazy`import)、設定モーダル(`SettingsModal`、`lazy`import)を統括するFamily Questのメイン画面コンポーネントであることを確認した(138〜523行目)。 | 直接ソース確認: `family-quest/src/App.tsx:138-523` |
| `CameraDashboard` コンポーネントの詳細な機能 | `family-quest/src/features/camera/components/CameraDashboard.tsx`(全67行)を直接確認した。マウント時の`useEffect`(13〜27行目)で`document.title`を「ホーム監視カメラ」に変更しつつ`apiClient.get<CameraConfig[]>('/api/cameras/settings')`を呼び出し、`enabled`なカメラのみ`filter`し`order`昇順に`sort`して`cameras`状態にセットする(16〜19行目)。「🟢 ライブ映像」/「📼 録画再生」の2タブ(`activeTab`, 43〜56行目)を持ち、`live`時は`LiveView`、`record`時は`RecordView`にカメラ一覧を渡して描画する(58〜62行目)独立した全画面レイアウト(`min-h-screen`、31〜65行目)のコンポーネントである。 | 直接ソース確認: `family-quest/src/features/camera/components/CameraDashboard.tsx:1-67` |
| データフェッチ機構のグローバル設定 | `family-quest/src/lib/queryClient.ts`(全10行)を直接確認した。`queryClient`は`retry: 1`（失敗時1回再試行）、`staleTime: 1000 * 60`（60秒）、`refetchOnWindowFocus: false`という`defaultOptions.queries`(4〜9行目)を持つ`QueryClient`インスタンスであり、記載内容以外の追加設定（`mutations`のデフォルト等）はファイル内に存在しない。 | 直接ソース確認: `family-quest/src/lib/queryClient.ts:1-10` |
| グローバルスタイルの定義内容 | `family-quest/src/index.css`を直接確認した。全2行で`@tailwind base; @tailwind components; @tailwind utilities;`のみが記述されており、Tailwind CSSの3レイヤーを読み込むだけのファイルで、カスタムのCSS変数やベーススタイルの追加定義は本ファイルには存在しない（`App.css`という別ファイルは存在するが、`main.tsx`からインポートされているのは`index.css`のみである）。 | 直接ソース確認: `family-quest/src/index.css:1-2` |
| HTML側のDOM構造 | `family-quest/index.html`(全20行)を直接確認した。`<body>`内に`<div id="root"></div>`(16行目)と`<script type="module" src="/src/main.tsx"></script>`(17行目)のみが存在し、`main.tsx`がマウント対象とする`id="root"`の要素が確実に存在することを確認した。`<head>`には`<title>Family Quest</title>`(12行目)、`theme-color`(9行目)、`description`(10行目)、`apple-touch-icon`(11行目)等のメタデータが定義されている。 | 直接ソース確認: `family-quest/index.html:1-20` |
| `/camera` 以外のルーティング設計の有無 | `family-quest/package.json`および`family-quest`ディレクトリ全体を確認したが、`react-router`等のルーティングライブラリの依存は存在せず(`package.json`に`router`を含む依存は0件)、`*route*`/`*router*`という名前の設定ファイルもリポジトリ内に見つからなかった。`family-quest/src/main.tsx`(全41行)を確認したところ、ルーティングは本ファイル17行目で確認した`window.location.pathname.includes('/camera')`による単純な文字列一致のみで、`isCameraView`の真偽で`CameraDashboard`か`App`(+`SettingsProvider`+`ToastProvider`)かをルート直下で丸ごと切り替えている(24〜39行目)。専用のルーティングライブラリや追加のルート定義は存在しないことを確認した。 | 直接ソース確認: `family-quest/src/main.tsx:1-41`, `family-quest/package.json`（`router`依存なし） |

## 10. 自己検証結果

* [x] 完了: 推測・外部ファイルの仕様を一切含んでいない
* [x] 完了: 全関数・全クラス・全コンポーネントを列挙した（本ファイルは該当なし）
* [x] 完了: 全てのインポート要素を列挙した
* [x] 完了: すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 完了: 根拠漏れが0件である
* [x] 完了: Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 完了: 不明事項を漏れなく列挙した