## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `InventoryList.tsx` |
| 言語 | React (TypeScript) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 2. ファイルの概要

* ユーザーの所持アイテム（インベントリ）一覧を取得・表示し、アイテムの「使用」および「キャンセル」を行うUIコンポーネント。
* React Queryを用いてサーバーとの定期的な同期（ポーリング）を行いつつ、アイテム操作時には画面への即時反映（楽観的UI更新）を行う責務を持つ。

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `React`, `useState` | モジュール | Reactコンポーネントとしての定義と利用、確認モーダルの表示対象アイテム保持用の状態管理 | 根拠: [`React`, `useState`] (行番号: 1〜1 / 抜粋: "import React, { useState } from 'react';") |
| `useQuery`, `useMutation`, `useQueryClient` | フック | データ取得、データ更新、キャッシュ操作 | 根拠: [`@tanstack/react-query`] (行番号: 2〜2 / 抜粋: "import { useQuery, useMutation") |
| `apiClient` | オブジェクト | サーバーサイドとのAPI通信 | 根拠: [`apiClient`] (行番号: 3〜3 / 抜粋: "import { apiClient } from '...") |
| `Card` | コンポーネント | アイテムごとのUIカードレイアウト表示 | 根拠: [`Card`] (行番号: 4〜4 / 抜粋: "import { Card } from '...") |
| `Button` | コンポーネント | アイテム使用のトリガーボタン | 根拠: [`Button`] (行番号: 5〜5 / 抜粋: "import { Button } from '...") |
| `Modal` | コンポーネント | 「つかう」実行前の確認ダイアログ表示 | 根拠: [`Modal`] (行番号: 6〜6 / 抜粋: "import { Modal } from '../../../components/ui/Modal';") |
| `useSound` | フック | アクション時の効果音再生 | 根拠: [`useSound`] (行番号: 7〜7 / 抜粋: "import { useSound } from '...") |
| `Loader2`, `PackageOpen`, `Clock`, `AlertCircle` | コンポーネント(アイコン) | UI上の状態・装飾を示すアイコン表示 | 根拠: [`lucide-react`] (行番号: 8〜8 / 抜粋: "import { Loader2, PackageOpen") |
| `InventoryItem` | 型定義 | アイテムデータの型チェックと補完 | 根拠: [`InventoryItem`] (行番号: 9〜9 / 抜粋: "import { InventoryItem } from ") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `apiClient`の各メソッド (`fetchInventory`, `useItem`, `cancelItemUsage`) | 具体的なエンドポイント、リクエスト/レスポンス形式、エラーハンドリングの実装が不明（`../../../lib/apiClient`に依存のため要確認）。 | 根拠: [`apiClient`の呼び出し] (行番号: 28〜54 / 抜粋: "queryFn: () => apiClient.") |
| `Card`, `Button`, `Modal`の内部実装 | `../../../components/ui/`配下の実装が提供されていないため、propsの全容やレンダリング内容が不明。 | 根拠: [`Card`, `Button`, `Modal`] (行番号: 4〜6) |
| `useSound`の挙動 | 音声再生時のエラー処理や、再生可能な音声キー（'clear', 'cancel'）の定義が不明（`../../../hooks/useSound`に依存のため要確認）。 | 根拠: [`useSound`] (行番号: 19〜65 / 抜粋: "const { play } = useSound();") |
| `InventoryItem`の詳細な型定義 | コンポーネント内で使用されていないプロパティの有無が不明（`../../../types`に依存のため要確認）。 | 根拠: [`InventoryItem`] (行番号: 9〜9 / 抜粋: "import { InventoryItem } from ") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `InventoryList`

* **役割**: ユーザーのインベントリ一覧を取得し、条件に応じた画面（ローディング、空状態、アイテム一覧）を表示する。また、各アイテムの「使用」（確認モーダル経由）と「キャンセル」の操作を受け付け、キャッシュ更新を行う。
* 根拠: [`InventoryList`] (行番号: 17〜191 / 抜粋: "export const InventoryList: React.FC<Props> = ({ userId }) => {")


* **引数/リクエスト**: `{ userId: string }`
* 根拠: [`Props`] (行番号: 13〜15 / 抜粋: "type Props = { userId: string; };")


* **戻り値/レスポンス**: `ReactElement`（ローディングUI、空状態UI、またはアイテム一覧のグリッドUIと使用確認`Modal`）
* 根拠: [`InventoryList`のreturn文] (行番号: 88〜190 / 抜粋: "return ( <div className=\"grid")


* **副作用**:
* `apiClient`を利用した外部API呼び出し
* `queryClient.setQueryData`によるローカルキャッシュの直接更新（データの削除およびステータスの変更）
* `queryClient.invalidateQueries`によるキャッシュ無効化と再フェッチのトリガー
* `play`関数による音声（'clear', 'cancel'）の再生
* `setItemToUse`によるローカルstate更新（使用確認モーダルの開閉制御）
* 根拠: [`useMutationAction`, `cancelMutation`, `itemToUse`] (行番号: 23〜67 / 抜粋: "queryClient.setQueryData<InventoryItem[]>")


* **エラーハンドリング**:
* APIデータ取得中（`isLoading`）はローディングアイコンを表示。
* データが空（`!items || items.length === 0`）の場合は専用のメッセージUIを表示。
* ただし、`useMutation`や`useQuery`によるAPIエラー（`isError`など）に対する明示的なUI・キャッチ処理はファイル内に記述なし。
* 根拠: [条件付きレンダリング部分] (行番号: 69〜73 / 抜粋: "if (isLoading) return (")



## 5. 処理フロー図

```mermaid
flowchart TD
    Start([描画開始]) --> Init["外部：useQueryClient, useSoundの初期化"]
    Init --> Query["外部：useQuery(fetchInventory) \n5秒間隔のポーリング"]
    Query --> CheckLoading{"isLoading === true?"}
    
    CheckLoading -- Yes --> RenderLoading["ローディングUI表示"] --> End([描画終了])
    CheckLoading -- No --> CheckEmpty{"itemsが未定義 or 空?"}
    
    CheckEmpty -- Yes --> RenderEmpty["「まだなにも持っていません」UI表示"] --> End
    CheckEmpty -- No --> MapItems{"各アイテム(items)に対する描画ループ"}
    
    MapItems --> CheckStatus{"item.status === 'pending'?"}
    
    CheckStatus -- Yes --> RenderPending["承認待ちスタイルを適用\n「やめる」ボタンを表示"]
    CheckStatus -- No --> RenderOwned["通常スタイルを適用\n「つかう！」ボタンを表示"]
    
    RenderPending --> CancelClick{"「やめる」クリック?"}
    RenderOwned --> UseClick{"「つかう！」クリック?"}
    
    UseClick -- Yes --> SetItemToUse["setItemToUse(item)\nModal表示"]
    SetItemToUse --> ModalChoice{"Modal内で選択"}
    ModalChoice -- "キャンセル" --> CloseModal["setItemToUse(null)"] --> End
    ModalChoice -- "はい" --> MutateUse["外部：useMutationAction.mutate(itemToUse.id)"]
    MutateUse --> CloseModal2["setItemToUse(null)"]
    CloseModal2 --> OptimisticUse["キャッシュから当該アイテムを除外\n(onSuccess)"]
    OptimisticUse --> InvalidateUse["外部：invalidateQueries"]
    InvalidateUse --> PlayClear["外部：play('clear')"]
    
    CancelClick -- Yes --> MutateCancel["外部：cancelMutation.mutate(item.id)"]
    MutateCancel --> OptimisticCancel["キャッシュ内の当該アイテムの\nstatusを'owned'に戻す\n(onSuccess)"]
    OptimisticCancel --> InvalidateCancel["外部：invalidateQueries"]
    InvalidateCancel --> PlayCancel["外部：play('cancel')"]

```

## 6. 依存関係図

```mermaid
graph TD
    subgraph Components
        InventoryList["InventoryList Component"]
    end
    
    subgraph ReactQuery ["@tanstack/react-query"]
        useQuery["useQuery"]
        useMutation["useMutation"]
        useQueryClient["useQueryClient"]
    end
    
    subgraph CustomHooks
        useSoundHook["hooks/useSound"]
    end
    
    subgraph UI ["UI Components & Icons"]
        Card["Card"]
        Button["Button"]
        Modal["Modal"]
        Lucide["lucide-react (Icons)"]
    end
    
    subgraph ExternalAPI ["API Layer"]
        apiClient["lib/apiClient"]
    end
    
    subgraph Types
        InventoryItem["types/InventoryItem"]
    end

    InventoryList --> useQuery
    InventoryList --> useMutation
    InventoryList --> useQueryClient
    InventoryList --> useSoundHook
    InventoryList --> Card
    InventoryList --> Button
    InventoryList --> Modal
    InventoryList --> Lucide
    InventoryList -.-> InventoryItem

    useQuery -.->|fetchInventory| apiClient
    useMutation -.->|useItem / cancelItemUsage| apiClient

```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `../../../lib/apiClient.ts` | APIの実際のエンドポイント、パラメータ仕様、レスポンス構造、およびAPI側で発生しうるエラーの詳細を把握するため。 | 根拠: [`apiClient`への依存] (行番号: 3 / 抜粋: "import { apiClient }") |
| 中 | `../../../types/index.ts` | `InventoryItem`が持つ全プロパティ（特に`status`が取りうる他の値）を正確に特定し、UI上に反映漏れがないか確認するため。 | 根拠: [`InventoryItem`への依存] (行番号: 9 / 抜粋: "import { InventoryItem }") |
| 中 | `../../../components/ui/Modal.tsx` | `isOpen`/`onClose`/`footer`以外に受け付けるprops、およびアクセシビリティ対応（フォーカストラップ等）の実装状況を確認するため。 | 根拠: [`Modal`への依存] (行番号: 6 / 抜粋: "import { Modal }") |
| 低 | `../../../hooks/useSound.ts` | 再生可能な音声キーの全容や、音声ファイルのロード状況による動作への影響を確認するため。 | 根拠: [`useSound`への依存] (行番号: 7 / 抜粋: "import { useSound }") |

## 8. 保守上の注意点

* **エラーハンドリングの欠如**: `useQuery`および`useMutation`実行時にAPI通信エラーが発生した場合、エラーを画面上に表示・通知する処理が記述されていません。
* **楽観的UI更新の巻き戻し処理**: `useMutation`の`onSuccess`でキャッシュの即時書き換えを行っていますが、`onError`時のロールバック処理が記述されていないため、通信失敗時に画面状態とサーバー状態が一時的に乖離する可能性があります（`invalidateQueries`による次回のフェッチで上書きされる仕様と見受けられます）。
* **ポーリング負荷**: `refetchInterval: 5000` が設定されており、5秒ごとに自動フェッチが走るため、ユーザー数が多い場合はサーバー負荷への影響を考慮する必要があります。
* **確認ダイアログの状態管理**: アイテム使用時の確認はブラウザネイティブの`confirm()`ではなく、`itemToUse`ステートとアプリ標準の`Modal`コンポーネントで実装されている。`useMutationAction.mutate`呼び出しと`setItemToUse(null)`が同一の`onClick`内で連続実行されるため、ミューテーションの成否に関わらずモーダルは即座に閉じる（成否のフィードバックは`onSuccess`側の楽観的更新にのみ依存する）。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `item.status`の取りうる全値 | 現在のコードでは`'pending'`と`'owned'`のみ扱われているが、他のステータスが存在するか不明なため。 | `../../../types` |
| API通信エラー時のデフォルトの挙動 | グローバルなエラーハンドラーの有無がこのファイル単独では不明なため。 | `../../../lib/apiClient` または親コンポーネント群 |
| `play('clear')`等の音声の有無 | 指定されたキーに対応する音声が確実に存在するかが不明なため。 | `../../../hooks/useSound` |
| `Modal`コンポーネントの内部実装 | `isOpen`/`onClose`/`title`/`footer`のprops以外にどのような機能（フォーカストラップ、アニメーション等）を持つか不明なため。 | `../../../components/ui/Modal` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した
* [x] 完了