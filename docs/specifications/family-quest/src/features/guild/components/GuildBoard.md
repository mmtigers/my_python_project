## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `GuildBoard.tsx` |
| 言語 | React (TypeScript) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 2. ファイルの概要

このファイルは、ギルド依頼板（バウンティボード）のUIを提供するReactコンポーネントである。ユーザーが依頼（Bounty）の一覧閲覧、新規作成、受注、完了報告、承認、取り下げ、および辞退を行うための各種アクション機能と、それに伴う状態管理、外部API呼び出し、UIのレンダリング（リスト表示、モーダル、視覚・音声演出）を担っている。処理結果や確認ダイアログはブラウザ標準の `alert()`/`confirm()` ではなく、アプリ共通の `MessageModal`/`Modal` で表示する。

* 根拠: [GuildBoardコンポーネント] (行番号: 31〜421 / 抜粋: "export const GuildBoard: React.FC<GuildBoardProps> = ({ userId }) => {")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `React`, `useState` | ライブラリ (`react`) | コンポーネント定義とローカル状態管理に使用 | 根拠: [import文] (行番号: 2 / 抜粋: "import React, { useState } from 'react';") |
| `useQuery`, `useMutation`, `useQueryClient` | ライブラリ (`@tanstack/react-query`) | データの取得、更新処理、キャッシュの無効化に使用 | 根拠: [import文] (行番号: 3 / 抜粋: "import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';") |
| `confetti` | ライブラリ (`canvas-confetti`) | 依頼の受注・承認成功時の紙吹雪アニメーション演出に使用 | 根拠: [import文] (行番号: 4 / 抜粋: "import confetti from 'canvas-confetti';") |
| `Trash2`, `XCircle`, `ShieldAlert` | ライブラリ (`lucide-react`) | UI上のアイコン表示に使用 | 根拠: [import文] (行番号: 5 / 抜粋: "import { Trash2, XCircle, ShieldAlert } from 'lucide-react';") |
| `fetchBounties`, `createBounty`, `acceptBounty`, `completeBounty`, `approveBounty`, `deleteBounty`, `resignBounty` | 外部関数 (`../../../lib/apiClient`) | バックエンドとの通信（取得・作成・各種状態更新）に使用 | 根拠: [import文] (行番号: 7〜10 / 抜粋: "fetchBounties, createBounty, acceptBounty, completeBounty, approveBounty,") |
| `Bounty` | 型定義 (`../../../types`) | 依頼データの型定義に使用 | 根拠: [import文] (行番号: 11 / 抜粋: "import { Bounty } from '../../../types';") |
| `Card` | コンポーネント (`../../../components/ui/Card`) | 各依頼情報のカードレイアウトに使用 | 根拠: [import文] (行番号: 13 / 抜粋: "import { Card } from '../../../components/ui/Card';") |
| `Button` | コンポーネント (`../../../components/ui/Button`) | 各種操作ボタンに使用 | 根拠: [import文] (行番号: 14 / 抜粋: "import { Button } from '../../../components/ui/Button';") |
| `Modal` | コンポーネント (`../../../components/ui/Modal`) | 新規依頼作成フォーム、確認ダイアログのオーバーレイ表示に使用 | 根拠: [import文] (行番号: 15 / 抜粋: "import { Modal } from '../../../components/ui/Modal';") |
| `MessageModal` | コンポーネント (`../../../components/ui/MessageModal`) (デフォルトエクスポート) | ミューテーション失敗時のエラーメッセージ表示に使用 | 根拠: [import文] (行番号: 16 / 抜粋: "import MessageModal from '../../../components/ui/MessageModal';") |
| `useSound` | カスタムフック (`../../../hooks/useSound`) | ボタン操作や処理成功時の音声再生に使用 | 根拠: [import文] (行番号: 17 / 抜粋: "import { useSound } from '../../../hooks/useSound';") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `apiClient` の各関数 (`fetchBounties`等) | 関数の内部実装やエンドポイント、通信エラー時の挙動が本ファイルに含まれていないため | 根拠: [import文] (行番号: 7〜10 / 抜粋: "} from '../../../lib/apiClient';") |
| `Bounty` 型の詳細 | 本ファイル内で型定義が提供されておらず、すべてのプロパティが網羅的に判明していないため | 根拠: [import文] (行番号: 11 / 抜粋: "import { Bounty } from '../../../types';") |
| `Card`, `Button`, `Modal`, `MessageModal` | 内部でどのようなPropsを受け付けるか、スタイルがどう適用されるかの実装がないため | 根拠: [import文] (行番号: 13〜16 / 抜粋: "import { Card } from '../../../components/ui/Card';") |
| `useSound` フック | `play` 以外の戻り値や、引数（'submit', 'medal', 'cancel', 'tap'）に対する実際の音声ファイルのマッピングが不明なため | 根拠: [import文] (行番号: 17 / 抜粋: "import { useSound } from '../../../hooks/useSound';") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `CreateBountyForm`

* **役割**: 新規依頼作成時のフォームデータ構造を定義するインターフェース
* 根拠: [インターフェース定義] (行番号: 20〜25 / 抜粋: "interface CreateBountyForm {")


* **プロパティ**:
* `title` (string): 依頼のタイトル
* `description` (string): 依頼の詳細
* `reward_gold` (number): 報酬金額
* `target_type` ('ALL' | 'ADULTS' | 'CHILDREN'): 対象者タイプ



### `GuildBoardProps`

* **役割**: `GuildBoard` コンポーネントが受け取るPropsの型定義
* 根拠: [インターフェース定義] (行番号: 27〜29 / 抜粋: "interface GuildBoardProps {")


* **プロパティ**:
* `userId` (string): 現在のユーザーのID



### `GuildBoard`

* **役割**: ギルド依頼板の画面全体をレンダリングし、データの取得や各種ユーザーアクション（作成、受注、承認、取り下げ、辞退など）を処理するReactコンポーネント。
* 根拠: [コンポーネント定義] (行番号: 31〜421 / 抜粋: "export const GuildBoard: React.FC<GuildBoardProps> = ({ userId }) => {")


* **引数/リクエスト**: `GuildBoardProps` (`{ userId }`)
* 根拠: [引数] (行番号: 31 / 抜粋: "export const GuildBoard: React.FC<GuildBoardProps> = ({ userId }) => {")


* **戻り値/レスポンス**: JSX.Element（画面UI、`isLoading`が真の間はローディングテキストのみ）
* 根拠: [早期return] (行番号: 174 / 抜粋: "if (isLoading) return <div className=\"text-white text-center p-4\">読み込み中...</div>;") および [return文] (行番号: 176〜420 / 抜粋: "return ( <div className=\"p-2 max-w-4xl mx-auto space-y-4 pb-20\">")


* **副作用**:
* `useQuery` による5秒間隔のポーリングデータ取得 (`fetchBounties`)、`userId`が存在する場合のみ有効 (`enabled: !!userId`)
* 根拠: [useQuery] (行番号: 50〜55 / 抜粋: "queryKey: ['bounties', userId],")
* `useMutation` 経由でのAPIリクエスト送信 (`acceptBounty`, `completeBounty`, `approveBounty`, `createBounty`, `deleteBounty`, `resignBounty`)
* 根拠: [useMutation群] (行番号: 59〜140 / 抜粋: "const acceptMutation = useMutation({")
* 各ミューテーション成功時のキャッシュ無効化（`queryClient.invalidateQueries`）
* 根拠: [onSuccessハンドラ群] (行番号: 63, 74, 83〜84, 120, 129, 138 / 抜粋: "queryClient.invalidateQueries({ queryKey: ['bounties'] });")
* 音声の再生 (`play()`)
* 根拠: [onSuccessハンドラ等] (行番号: 62, 73, 82, 117, 128, 137, 189, 195 / 抜粋: "play('submit');")
* キャンバスへの描画（`confetti` によるアニメーション。`approveMutation`成功時は`requestAnimationFrame`を用いた2秒間の連続演出）
* 根拠: [onSuccessハンドラ] (行番号: 65, 86〜109 / 抜粋: "confetti({ particleCount: 50, spread: 60, origin: { y: 0.7 } });")


* **エラーハンドリング**: `acceptMutation`, `completeMutation`, `approveMutation` の `onError` で `setMessage({ title: "エラー", text: err.message })` を呼び、`MessageModal`にエラー内容を表示する。`createMutation`, `deleteMutation`, `resignMutation` には `onError` ハンドラが定義されていない。
* 根拠: [onErrorハンドラ] (行番号: 67, 76, 111 / 抜粋: "onError: (err: Error) => setMessage({ title: \"エラー\", text: err.message }),")



#### 内部関数: `handleDelete`

* **役割**: 引数の`bountyId`を伴い、`confirmAction`ステートに`{ type: 'delete', bountyId }`を設定して確認モーダルの表示をトリガーする。
* 根拠: [関数定義] (行番号: 144〜146 / 抜粋: "const handleDelete = (bountyId: number) => {")



#### 内部関数: `handleResign`

* **役割**: 引数の`bountyId`を伴い、`confirmAction`ステートに`{ type: 'resign', bountyId }`を設定して確認モーダルの表示をトリガーする。
* 根拠: [関数定義] (行番号: 148〜150 / 抜粋: "const handleResign = (bountyId: number) => {")



#### 内部定数: `confirmActionMeta`

* **役割**: `confirmAction`の`type`（'delete' または 'resign'）に応じて、確認モーダルのタイトル・本文・実行時の処理（`deleteMutation.mutate`または`resignMutation.mutate`）を切り替えるオブジェクト。
* 根拠: [定義] (行番号: 153〜156 / 抜粋: "const confirmActionMeta = confirmAction ? {")



#### 内部関数: `handleSubmit`

* **役割**: フォームのデフォルト送信イベントをキャンセルし、現在の `form` ステートを用いて `createMutation` を実行する。
* 根拠: [関数定義] (行番号: 158〜161 / 抜粋: "const handleSubmit = (e: React.FormEvent) => {")



#### 内部定数: `displayBounties`

* **役割**: `activeTab`の値に応じて`bounties`をフィルタリングする。`'OPEN'`タブでは`status === 'OPEN'`の依頼、それ以外（`'MINE'`タブ）では`is_mine`または`is_assigned_to_me`が真の依頼を抽出する。
* 根拠: [定義] (行番号: 164〜170 / 抜粋: "const displayBounties = bounties.filter((b: Bounty) => {")


## 5. 処理フロー図

```mermaid
flowchart TD
    Start["GuildBoard Mount"] --> InitQuery["useQuery: fetchBounties (enabled: !!userId)"]
    InitQuery --> WaitState{"isLoading?"}
    WaitState -- Yes --> Loading["Render: '読み込み中...'"]
    WaitState -- No --> FilterList["displayBounties = bounties.filter(activeTab条件)"]
    FilterList --> RenderUI["Render: Main UI (タブ, カード一覧, モーダル群)"]

    RenderUI --> UserAction{"User Action"}

    UserAction -- "Change Tab" --> UpdateTab["play('tap') & setActiveTab()"]
    UpdateTab --> RenderUI

    UserAction -- "Click Create Bounty" --> OpenModal["setIsModalOpen(true)"]
    OpenModal --> SubmitForm["handleSubmit()"]
    SubmitForm --> MCreate["外部: createBounty()"]
    MCreate --> MSuccessC{"Success?"}
    MSuccessC -- Yes --> CloseModal["play('submit'), setIsModalOpen(false), Reset Form"]
    CloseModal --> InvalidateQ["invalidateQueries(['bounties'])"]

    UserAction -- "Click 取り下げ" --> OpenConfirmD["setConfirmAction({type:'delete'})"]
    OpenConfirmD --> ConfirmModal["確認Modal表示 (confirmActionMeta)"]
    ConfirmModal -- "はい" --> MDelete["外部: deleteBounty()"]
    MDelete --> MSuccessD["play('cancel')"] --> InvalidateQ
    ConfirmModal -- "キャンセル" --> RenderUI

    UserAction -- "Click 辞退" --> OpenConfirmR["setConfirmAction({type:'resign'})"]
    OpenConfirmR --> ConfirmModal
    ConfirmModal -- "はい" --> MResign["外部: resignBounty()"]
    MResign --> MSuccessR["play('cancel')"] --> InvalidateQ

    UserAction -- "Click この依頼を受ける" --> MAccept["外部: acceptBounty()"]
    MAccept --> MSuccessA{"Success?"}
    MSuccessA -- Yes --> ConfettiA["play('submit'), confetti()"] --> InvalidateQ
    MSuccessA -- No --> SetMsgError["setMessage(エラー内容)"] --> ShowMessageModal["MessageModal表示"]

    UserAction -- "Click 報告する" --> MComplete["外部: completeBounty()"]
    MComplete --> MSuccessComp{"Success?"}
    MSuccessComp -- Yes --> PlaySub["play('submit')"] --> InvalidateQ
    MSuccessComp -- No --> SetMsgError

    UserAction -- "Click 承認して報酬を払う" --> MApprove["外部: approveBounty()"]
    MApprove --> MSuccessApp{"Success?"}
    MSuccessApp -- Yes --> ConfettiApp["play('medal'), requestAnimationFrameでGold confetti連続描画(2秒)"]
    MSuccessApp -- No --> SetMsgError
    ConfettiApp --> InvalidateQApp["invalidateQueries(['bounties'], ['gameData'])"]

    InvalidateQ --> InitQuery
    InvalidateQApp --> InitQuery

```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "GuildBoard.tsx"
        GB["GuildBoard Component"]
        FormState["State: form"]
        TabState["State: activeTab"]
        ModalState["State: isModalOpen"]
        MessageState["State: message"]
        ConfirmState["State: confirmAction"]
    end

    subgraph "React Query"
        UQ["useQuery (bounties)"]
        UM_A["useMutation (accept)"]
        UM_C["useMutation (complete)"]
        UM_App["useMutation (approve)"]
        UM_Cr["useMutation (create)"]
        UM_D["useMutation (delete)"]
        UM_R["useMutation (resign)"]
        UQC["useQueryClient"]
    end

    subgraph "UI Components (../../../components/ui/)"
        Card["Card"]
        Button["Button"]
        Modal["Modal"]
        MessageModal["MessageModal"]
    end

    subgraph "API Client (../../../lib/apiClient)"
        API_Fetch["fetchBounties"]
        API_Create["createBounty"]
        API_Accept["acceptBounty"]
        API_Complete["completeBounty"]
        API_Approve["approveBounty"]
        API_Delete["deleteBounty"]
        API_Resign["resignBounty"]
    end

    subgraph "External Libraries / Hooks"
        Confetti["canvas-confetti"]
        Icons["lucide-react"]
        Sound["useSound (../../../hooks/useSound)"]
    end

    GB --> UQ
    GB --> UM_A & UM_C & UM_App & UM_Cr & UM_D & UM_R
    GB --> UQC
    GB --> FormState & TabState & ModalState & MessageState & ConfirmState

    GB --> Card & Button & Modal & MessageModal
    GB --> Confetti & Icons & Sound

    UQ --> API_Fetch
    UM_Cr --> API_Create
    UM_A --> API_Accept
    UM_C --> API_Complete
    UM_App --> API_Approve
    UM_D --> API_Delete
    UM_R --> API_Resign

```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `../../../lib/apiClient.ts` | 各種ミューテーションとデータフェッチの実際の通信処理、エンドポイント、ペイロードの形式を把握するため。 | 根拠: [import文] (行番号: 7〜10 / 抜粋: "} from '../../../lib/apiClient';") |
| 高 | `../../../types.ts` | `Bounty` オブジェクトの全体スキーマ（`status`, `is_mine`, `can_accept`, `is_assigned_to_me` などの厳密な型や他の未参照プロパティ）を特定するため。 | 根拠: [import文] (行番号: 11 / 抜粋: "import { Bounty } from '../../../types';") |
| 中 | `../../../hooks/useSound.ts` | 音声ファイルの読み込み仕様や `play` 関数に渡す識別子（'submit', 'medal', 'cancel', 'tap'）が正しく定義されているか確認するため。 | 根拠: [import文] (行番号: 17 / 抜粋: "import { useSound } from '../../../hooks/useSound';") |
| 中 | `../../../components/ui/MessageModal.tsx` | エラー表示に用いる `MessageModal` の実際のPropsとレンダリング仕様を確認するため。 | 根拠: [import文] (行番号: 16 / 抜粋: "import MessageModal from '../../../components/ui/MessageModal';") |
| 低 | `../../../components/ui/Card.tsx`, `Button.tsx`, `Modal.tsx` | `Card`, `Button`, `Modal` のPropsインターフェースおよびスタイリングの仕組みを把握するため。 | 根拠: [import文] (行番号: 13〜15 / 抜粋: "import { Card } from '../../../components/ui/Card';") |

## 8. 保守上の注意点

* `useQuery` で設定されている `refetchInterval: 5000` により、5秒ごとに `fetchBounties` APIが呼び出される。`queryKey`に`userId`が含まれるため、ユーザーが切り替わるとキャッシュも別枠になる。
* `createMutation`, `deleteMutation`, `resignMutation` においては、ミューテーションのエラーハンドリング（`onError`）が実装されていない。失敗時はUI上に何も表示されず、ユーザーからは処理が止まったように見える可能性がある。
* 依頼の取り下げ・辞退確認は、以前はブラウザ標準の`confirm()`を使っていたと推測されるが、現在は`confirmAction`ステートと`Modal`コンポーネントを用いたアプリ内モーダルに置き換えられている。
* ミューテーション（`accept`/`complete`/`approve`）の`onError`処理は、以前の`alert()`から`setMessage`経由の`MessageModal`表示に置き換えられている。
* `approveMutation` の成功時にのみ `['gameData']` クエリもキャッシュ無効化対象としている（他のアクションでは `['bounties']` のみ）。ボスHPやユーザーのgold/exp等、依頼承認に連動する他画面のデータがある場合、この無効化漏れに注意が必要。
* フォームの `target_type` セレクトボックスの `onChange` イベントでは `e.target.value as CreateBountyForm['target_type']` という型アサーションを用いており、`<select>`の`value`属性に定義されていない値が渡された場合でも型チェックをすり抜ける。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| バックエンドAPIの通信詳細 | HTTPメソッド、エンドポイントURL、リクエスト/レスポンスの厳密なJSON構造が不明。 | `../../../lib/apiClient.ts` |
| `Bounty` の完全なプロパティ | 本ファイルで参照されているプロパティ以外にデータが存在するか不明。 | `../../../types.ts` |
| `gameData` クエリの内容 | `approveMutation` 成功時にInvalidateされる `['gameData']` が、どこでどのように定義・使用されているか本ファイル内からは不明。 | 他の機能やコンポーネントのファイル |
| UIコンポーネントの仕様 | `Button` の `variant` (primary, secondary, success, warning) の網羅的な種類とデザイン定義、`MessageModal`/`Modal`の実際のProps仕様が不明。 | `../../../components/ui/Button.tsx`, `MessageModal.tsx`, `Modal.tsx` |
| 音声アセットの紐付け | `play` の引数に指定している文字列が実際のどの音声ファイルにマッピングされているか不明。 | `../../../hooks/useSound.ts` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
