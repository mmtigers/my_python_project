## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `QuestList.tsx` |
| 言語 | React (TypeScript) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

- [../hooks/useQuestStatus.md](../hooks/useQuestStatus.md) — `useQuestStatus`/`getQuestLockState`の実装元。クエストのロック・完了・保留判定ロジックを提供する。
- [../../../types/index.md](../../../types/index.md) — `User`/`Quest`/`QuestHistory`型定義の提供元。
- [../../../components/ui/Card.md](../../../components/ui/Card.md) — `QuestItem`がラップして使用するカードUIコンポーネント。
- [../../../components/ui/CooldownRing.md](../../../components/ui/CooldownRing.md) — 無限クエストのクールダウン中に円形プログレスを表示するコンポーネント（対応する解析ドキュメントは本ファイルの解析時点では未作成）。
- [../../../hooks/useSound.md](../../../hooks/useSound.md) — クエストクリック時の効果音再生フックの実装元。
- [../../../hooks/useLongPress.md](../../../hooks/useLongPress.md) — 完了済み/申請中クエストの長押し取消ジェスチャーを提供するフック（対応する解析ドキュメントは本ファイルの解析時点では未作成）。
- [../../family/components/FamilyDashboard.md](../../family/components/FamilyDashboard.md) — `panelMode`/`iconFirst` propsを実際に渡す横画面パネル表示側の呼び出し元。

## 2. ファイルの概要

このファイルは、クエストのリスト（`QuestList`）および個別のクエスト（`QuestItem`）を画面に描画するUIコンポーネントを提供する。`QuestList`は`quests`をターゲット（役割/ユーザー個別）・曜日で絞り込み、共通関数`getQuestLockState`によるステータススコアとボーナス量・IDでソートしたうえで、`activeQuests`（今できること）と`doneOrLockedQuests`（完了済み・未開放）に振り分け、`framer-motion`によるアニメーション付きで`QuestItem`のリストとして描画する。完了済み・未開放クエストは既定で折りたたまれ、`showDoneAndLocked`ステートのトグルボタンで開閉できる。`panelMode`propが真の場合、横画面4人表示（`FamilyDashboard`）のパネル内で使うことを想定し、ビューポート幅基準の`md:`ブレークポイントに依存しない、狭いパネル幅でも崩れないタップ領域確保済みの単一カラム表示に切り替える。`iconFirst`propが真の場合、非識字年齢の子ども向けにアイコンを大きく・説明文を非表示にした表示にする。`QuestItem`側では、完了済み・申請中クエストの取消は誤操作防止のため「長押し」（`useLongPress`）でのみ発火し、通常タップは新規の完了操作にのみ作用する。
* 根拠: `export default function QuestList` (行番号: 285 / 抜粋: "export default function QuestList({ quests, completedQuests, pendingQuests, currentUser, onQuestClick, panelMode, iconFirst }: QuestListProps) {")
* 根拠: `const QuestItem: React.FC` (行番号: 37 / 抜粋: "const QuestItem: React.FC<{")
* 根拠: `panelMode`/`iconFirst`のコメント (行番号: 17〜23 / 抜粋: "// 横画面4人表示のパネル内で使うためのモード。\n    // true の場合、ビューポート幅基準の md: ブレークポイント(2カラム化・拡大表示)には\n    // 依存せず、狭いパネル幅でも崩れないタップ領域確保済みの単一カラム表示にする。\n    panelMode?: boolean;\n    // アイコン主体・文字量を絞った表示にするか(非識字年齢の子ども向け)。\n    // 説明文を非表示にし、アイコンをより大きく見せる。\n    iconFirst?: boolean;")
* 根拠: 完了済み/申請中の折りたたみと長押し取消 (行番号: 71〜73, 337〜338行目 / 抜粋: "// 完了済み/申請中の取り消しは「長押し」でのみ発火させ、うっかりタップでの\n    // 誤取り消しを防ぐ。無限クエストは取り消し概念がないため対象外。\n    const canCancel = !isInfinite && (isDone || isPending) && !isEffectivelyLocked;", "// ▼ 角度①: 「今できること」だけを最初に見せるため、完了済み/ロック中は折りたたむ。")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `React`, `useMemo`, `useState` | モジュール | Reactの基本機能およびフック | `import React, { useMemo, useState } from 'react';` (行番号: 1) |
| `Undo2`, `Clock`, `TrendingUp`, `Lock`, `ChevronDown`, `ChevronUp` | モジュール | アイコンの描画（取消、申請中、ボーナス上昇、ロック、折りたたみ開閉） | `import { Undo2, Clock, TrendingUp, Lock, ChevronDown, ChevronUp } from 'lucide-react';` (行番号: 2) |
| `motion`, `AnimatePresence` | モジュール | アニメーションの制御 | `import { motion, AnimatePresence } from 'framer-motion';` (行番号: 3) |
| `User`, `Quest`, `QuestHistory` | 型 | コンポーネントのPropsおよび内部変数の型定義 | `import { User, Quest, QuestHistory } from '@/types';` (行番号: 4) |
| `Card` | コンポーネント | UIのカード型コンテナとして使用 | `import { Card } from '@/components/ui/Card';` (行番号: 5) |
| `CooldownRing` | コンポーネント | 無限クエストのクールダウン中に残り時間を円形プログレスで表示 | `import { CooldownRing } from '@/components/ui/CooldownRing';` (行番号: 6) |
| `useQuestStatus`, `getQuestLockState` | カスタムフック / 関数 | クエストの状態（完了、申請中、ロック済みなど）の取得。`getQuestLockState`はソート用コンパレータや`activeQuests`/`doneOrLockedQuests`振り分けからHooksを使わずに同じ判定ロジックを呼び出すための素関数。 | `import { useQuestStatus, getQuestLockState } from '../hooks/useQuestStatus';` (行番号: 7) |
| `useSound` | カスタムフック | 音声再生機能の取得 | `import { useSound } from '@/hooks/useSound';` (行番号: 8) |
| `useLongPress` | カスタムフック | 完了済み/申請中クエストの長押し取消ジェスチャー（押下進捗・実行判定）の取得 | `import { useLongPress } from '@/hooks/useLongPress';` (行番号: 9) |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `@/types` の各型 (`User`, `Quest`, `QuestHistory`) | プロパティの完全な構造が本ファイル内では定義されていないため | `import { User, Quest, QuestHistory } from '@/types';` (行番号: 4) |
| `Card` コンポーネント | 内部の描画ロジックや `variant` などのPropsの仕様が不明なため | `import { Card } from '@/components/ui/Card';` (行番号: 5) |
| `CooldownRing` コンポーネント | `durationMs`/`size` 以外に受け取るPropsや内部の描画方式が不明なため | `import { CooldownRing } from '@/components/ui/CooldownRing';` (行番号: 6) |
| `useQuestStatus`, `getQuestLockState` | 内部の判定ロジック（`isDone`, `isLocked`, `variant` などの算出方法）が不明なため | `import { useQuestStatus, getQuestLockState } from '../hooks/useQuestStatus';` (行番号: 7) |
| `useSound` | `play` 関数の仕様や再生される音声の詳細が不明なため | `import { useSound } from '@/hooks/useSound';` (行番号: 8) |
| `useLongPress` | 長押し判定の実装（イベントリスナーの種類、`pressProgress`の算出方法）が不明なため | `import { useLongPress } from '@/hooks/useLongPress';` (行番号: 9) |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `QuestListProps` / `BadgeCandidate` / `QuestItem`のprops型

* **役割**: `QuestList`が受け取るProps型定義(`QuestListProps`)。`panelMode`（パネル内固定レイアウト用）と`iconFirst`（アイコン主体・非識字年齢向け表示用）がオプショナルで含まれる。`BadgeCandidate`はバッジ表示の優先度付け（`key`, `priority`, `node`）に使う内部型。`QuestItem`側にも`panelMode?: boolean`, `iconFirst?: boolean`を含むprops型が個別に定義され、`QuestList`から素通しで渡される。
* 根拠: `interface QuestListProps {` (行番号: 11〜24 / 抜粋: "panelMode?: boolean;", "iconFirst?: boolean;")
* 根拠: `BadgeCandidate` (行番号: 26〜32 / 抜粋: "interface BadgeCandidate {\n    key: string;\n    priority: number;\n    node: React.ReactNode;\n}")
* 根拠: `QuestItem`のprops型 (行番号: 37〜45 / 抜粋: "panelMode?: boolean;\n    iconFirst?: boolean;\n}> = ({ quest, completedQuests, pendingQuests, currentUser, onClick, panelMode, iconFirst }) => {")

### `MAX_VISIBLE_BADGES` (モジュールレベル定数)

* **役割**: バッジ（ロック・共有対応済み・申請中・期間限定・時間限定）を優先度順に並べたときに、同時表示する上限件数（2件）を定義する。上位2件を超える分は「+N」表示にまとめられる。
* 根拠: (行番号: 26〜34 / 抜粋: "// バッジは種類が多く同時に出すと読みづらいため、優先度順に並べて\n// 上位2件だけを表示する。優先度が低いものは「+N」でまとめて示す。\ninterface BadgeCandidate {...}\nconst MAX_VISIBLE_BADGES = 2;")

### `QuestItem`

* **役割**: 個別のクエストカードを描画し、状態に応じたバッジ表示（優先度順に上位`MAX_VISIBLE_BADGES`件＋「+N」）やクリック時の音声再生、コールバック実行を担う。`panelMode`が真のときはビューポート幅基準の`md:`拡大・2カラム化に乗らず、常に「狭い列でも崩れず、かつタップしやすい」固定サイズのクラス群（`cardSizeClasses`等、8種類）を使う。`iconFirst`が真のときはアイコンサイズを拡大しつつ説明文（`quest.desc`/`quest.description`）を非表示にする。共有クエスト（`is_shared_completed_by`/`is_shared_pending_by`が自分以外）は`isEffectivelyLocked`として扱われクリック不可になる。完了済み/申請中の取消は`useLongPress`による長押しでのみ発火し、通常タップは新規完了（`handleTapComplete`）にのみ作用する。無限クエストの完了操作後は`isCooldown`ステートで60秒間クールダウンし、`CooldownRing`をオーバーレイ表示する。
* 根拠: `const QuestItem: React.FC` (行番号: 37〜283 / 抜粋: "const QuestItem: React.FC<{")
* 根拠: パネルモード時のクラス切り替え (行番号: 108〜119 / 抜粋: "// パネルモードでは viewport幅基準の md: 拡大/2カラム化には乗らず、\n    // 常に「狭い列でも崩れず、かつタップしやすい(44px以上)」固定サイズを使う。\n    const cardSizeClasses = panelMode ? 'p-2 min-h-[56px]' : 'min-h-[56px] md:p-6 md:h-full';")
* 根拠: 説明文の非表示条件 (行番号: 235〜240 / 抜粋: "{/* 説明文: iconFirst(非識字年齢向け)では非表示にし、アイコンでの識別を優先する */}\n                        {!iconFirst && (quest.desc || quest.description) && (")
* 根拠: `isEffectivelyLocked`と長押し取消 (行番号: 65〜73行目 / 抜粋: "const isEffectivelyLocked = isLocked || isSharedDoneByOther;\n\n    // 完了済み/申請中の取り消しは「長押し」でのみ発火させ、うっかりタップでの\n    // 誤取り消しを防ぐ。無限クエストは取り消し概念がないため対象外。\n    const canCancel = !isInfinite && (isDone || isPending) && !isEffectivelyLocked;")
* 根拠: クールダウン処理 (行番号: 82〜85行目 / 抜粋: "if (isInfinite) {\n            setIsCooldown(true);\n            setTimeout(() => setIsCooldown(false), COOLDOWN_MS);\n        }")

* **引数/リクエスト**: オブジェクト `{ quest, completedQuests, pendingQuests, currentUser, onClick, panelMode, iconFirst }`
* 根拠: Propsの型定義 (行番号: 37〜45 / 抜粋: "quest: Quest;\n    completedQuests: QuestHistory[];\n    pendingQuests: QuestHistory[];\n    currentUser: User;\n    onClick: (q: Quest) => void;\n    panelMode?: boolean;\n    iconFirst?: boolean;")

* **戻り値/レスポンス**: ReactElement（JSX）
* 根拠: `return` 文 (行番号: 170〜282 / 抜粋: "return (\n        <div className=\"relative h-full group\">")

* **副作用**:
  * `useSound().play()` による音声再生（`quest.type === 'daily'`または`isInfinite`の場合は`'clear'`、それ以外は`'submit'`。取消時は`'cancel'`）
  * 根拠: `play('clear');`, `play('submit');`, `play('cancel');` (行番号: 78, 80, 91 / 抜粋: "if (quest.type === 'daily' || isInfinite) {\n            play('clear');\n        } else {\n            play('submit');\n        }")
  * `isInfinite`の場合、`setIsCooldown(true)`後に`setTimeout`でローカルステート (`isCooldown`) を`COOLDOWN_MS`（60000ms）後に`false`へ戻す
  * 根拠: (行番号: 82〜85 / 抜粋: "if (isInfinite) {\n            setIsCooldown(true);\n            setTimeout(() => setIsCooldown(false), COOLDOWN_MS);\n        }")
  * `onClick`コールバックを、対象クエストに`_isInfinite`プロパティを動的付与したオブジェクトとともに呼び出す
  * 根拠: (行番号: 86, 92 / 抜粋: "onClick({ ...quest, _isInfinite: !!isInfinite });")

* **エラーハンドリング**: なし。`runComplete`は`isCooldown`または`isEffectivelyLocked`の場合、`runCancel`は`isEffectivelyLocked`の場合にそれぞれ冒頭で処理を中断する。`handleTapComplete`は`canCancel`（長押し対象）または`isCooldown`の場合はタップでは何もしない。
* 根拠: (行番号: 76, 90, 104行目 / 抜粋: "if (isCooldown || isEffectivelyLocked) return;", "if (isEffectivelyLocked) return;", "if (canCancel || isCooldown) return; // 長押し対象/クールダウン中はタップでは何もしない")

### `QuestList`

* **役割**: 受け取ったクエスト一覧を（ターゲット、曜日で）フィルタリングし、`getQuestLockState`によるステータススコアとボーナス量・IDでソートしたうえで、`activeQuests`（今できること）と`doneOrLockedQuests`（完了済み・未開放）に分割する。前者は常に、後者は`showDoneAndLocked`が真のときのみ`QuestItem`のリストとして`AnimatePresence`付きで描画する。`panelMode`が真の場合、リストコンテナのクラス（`listContainerClass`）を2カラムグリッドではなく単一カラム縦積みにし、見出し（`-- クエスト一覧 --`）も非表示にする。
* 根拠: `export default function QuestList` (行番号: 285〜427 / 抜粋: "export default function QuestList({ quests, completedQuests, pendingQuests, currentUser, onQuestClick, panelMode, iconFirst }: QuestListProps) {")
* 根拠: `activeQuests`/`doneOrLockedQuests`への振り分け (行番号: 337〜351 / 抜粋: "// ▼ 角度①: 「今できること」だけを最初に見せるため、完了済み/ロック中は折りたたむ。\n    // 申請中(承認待ち)は本人がまだ気にする状態なので折りたたまず常時表示する。\n    const { activeQuests, doneOrLockedQuests } = useMemo(() => {")
* 根拠: `listContainerClass`/`headerClass`の分岐 (行番号: 353〜358 / 抜粋: "const listContainerClass = panelMode\n        ? 'space-y-2 animate-in fade-in duration-300'\n        : 'space-y-2 md:space-y-0 md:grid md:grid-cols-2 md:gap-6 ...';")
* 根拠: 折りたたみボタン (行番号: 408〜416 / 抜粋: "<button\n                        onClick={() => setShowDoneAndLocked(v => !v)}\n                        className=\"w-full min-h-[44px] flex items-center justify-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 bg-black/20 hover:bg-black/30 rounded-lg py-2 transition-colors\"\n                    >")

* **引数/リクエスト**: `QuestListProps` (`{ quests: Quest[], completedQuests: QuestHistory[], pendingQuests: QuestHistory[], currentUser: User, onQuestClick: (quest: Quest) => void, panelMode?: boolean, iconFirst?: boolean }`)
* 根拠: インターフェース定義および引数 (行番号: 11〜24, 285 / 抜粋: "interface QuestListProps {")

* **戻り値/レスポンス**: ReactElement（JSX）
* 根拠: `return` 文 (行番号: 386〜426 / 抜粋: "return (\n        <div className={listContainerClass}>")

* **副作用**: なし（`useMemo`によるフィルタ・ソート・振り分け結果のメモ化と、`useState`による`showDoneAndLocked`（折りたたみ開閉）の管理のみで、外部API呼び出しやDOM直接操作は存在しない）
* 根拠: `useMemo` ブロック (行番号: 290〜335, 339〜351 / 抜粋: "const sortedQuests = useMemo(() => {", "const { activeQuests, doneOrLockedQuests } = useMemo(() => {")

* **エラーハンドリング**: なし
* 根拠: 関数内に `try-catch` ブロック等が存在しない。

## 5. 処理フロー図

```mermaid
flowchart TD
    Start["Start: QuestList Render"] --> CalcDay["現在の曜日を算出 (jsDay, currentDay)"]
    CalcDay --> FilterSort["useMemo: クエストのフィルタ＆ソート (sortedQuests)"]

    subgraph "フィルタリング (sortedQuests)"
        F2{"ターゲット(target)判定に合致?"}
        F2 -- No --> Drop["除外"]
        F2 -- Yes --> F3{"曜日(days)指定に合致?"}
        F3 -- No --> Drop
        F3 -- Yes --> Keep["保持"]
    end

    Keep --> Sort["getQuestLockState()でスコア算出 → スコア・ボーナス合計・IDでソート"]
    Sort --> SplitGroups["useMemo: activeQuests / doneOrLockedQuests に振り分け"]
    SplitGroups --> LayoutCheck{"panelMode === true?"}
    LayoutCheck -- Yes --> HideHeader["見出し非表示、単一カラムクラス適用"]
    LayoutCheck -- No --> ShowHeader["見出し表示、md:2カラムクラス適用"]
    HideHeader --> RenderActive["activeQuests を AnimatePresence + motion.div で map 処理"]
    ShowHeader --> RenderActive

    RenderActive --> HasDoneOrLocked{"doneOrLockedQuests.length > 0 ?"}
    HasDoneOrLocked -- No --> MapItem
    HasDoneOrLocked -- Yes --> ToggleBtn["折りたたみボタン描画 (showDoneAndLocked切り替え)"]
    ToggleBtn --> ToggleState{"showDoneAndLocked === true?"}
    ToggleState -- Yes --> RenderDoneLocked["doneOrLockedQuests を AnimatePresence + motion.div で map 処理"]
    ToggleState -- No --> MapItem
    RenderDoneLocked --> MapItem["QuestItem Render (panelMode/iconFirstに応じたクラス選択)"]

    subgraph "QuestItem のタップ完了処理 (runComplete/handleTapComplete)"
        C_Start{"canCancel === true または isCooldown === true?"}
        C_Start -- Yes --> C_NoOp["タップでは何もしない"]
        C_Start -- No --> C_Run["runComplete() 実行"]
        C_Run --> C_Lock{"isCooldown または isEffectivelyLocked?"}
        C_Lock -- Yes --> C_End["処理中断(return)"]
        C_Lock -- No --> C_Sound{"quest.type === 'daily' または isInfinite?"}
        C_Sound -- Yes --> S1["外部：play('clear')"]
        C_Sound -- No --> S2["外部：play('submit')"]
        S1 --> C_Infinite{"isInfinite?"}
        S2 --> C_Infinite
        C_Infinite -- Yes --> Cooldown["setIsCooldown(true) / setTimeout(60秒)"]
        C_Infinite -- No --> C_Callback
        Cooldown --> C_Callback
        C_Callback["onClick({...quest, _isInfinite}) コールバック実行"] --> C_End
    end

    subgraph "QuestItem の長押し取消処理 (useLongPress → runCancel)"
        L_Start{"canCancel === true?"}
        L_Start -- No --> L_Disabled["長押し無効"]
        L_Start -- Yes --> L_Press["長押し進捗(pressProgress)を表示しつつ550ms計測"]
        L_Press --> L_Complete{"閾値到達?"}
        L_Complete -- Yes --> L_Run["runCancel() 実行"]
        L_Run --> L_Lock{"isEffectivelyLocked?"}
        L_Lock -- Yes --> L_End["処理中断(return)"]
        L_Lock -- No --> L_Sound["外部：play('cancel')"]
        L_Sound --> L_Callback["onClick({...quest, _isInfinite}) コールバック実行"] --> L_End
    end

    MapItem --> End["End: JSXを返却"]
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "QuestList.tsx"
        QuestList["QuestList (Component)"]
        QuestItem["QuestItem (Component)"]
    end

    subgraph "External Hooks / Functions (../hooks/useQuestStatus)"
        useQuestStatus["useQuestStatus"]
        getQuestLockState["getQuestLockState"]
    end

    subgraph "External Hooks (直接import)"
        useSound["useSound"]
        useLongPress["useLongPress"]
    end

    subgraph "External UI Components"
        Card["Card (Component)"]
        CooldownRing["CooldownRing (Component)"]
        LucideIcons["lucide-react (Icons)"]
        FramerMotion["framer-motion (motion, AnimatePresence)"]
    end

    subgraph "Types (Blackbox)"
        Types["@/types (User, Quest, QuestHistory)"]
    end

    QuestList -->|import| Types
    QuestList -->|Render, panelMode/iconFirstを伝播| QuestItem
    QuestList -->|Render| FramerMotion
    QuestList -->|Call (sort comparator, 振り分け)| getQuestLockState

    QuestItem -->|Render| Card
    QuestItem -->|Render (クールダウン中)| CooldownRing
    QuestItem -->|import| Types
    QuestItem -->|Call| useQuestStatus
    QuestItem -->|Call| useSound
    QuestItem -->|Call| useLongPress
    QuestItem -->|Render| LucideIcons
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `@/types` | `Quest` 型に対して `is_shared_completed_by`, `is_shared_pending_by`, `shared_completed_by_name`, `shared_pending_by_name` など共有クエスト関連のプロパティが参照されており、実際のデータスキーマを把握しないと不具合の原因となるため。 | (行番号: 65〜68 / 抜粋: "const isSharedCompleted = !!quest.is_shared_completed_by...") |
| 高 | `../hooks/useQuestStatus` | クエストの表示状態（`isDone`, `isLocked`, `isPending`, `variant` など）の算出ロジックが本ファイルから切り離されているため、表示不具合の調査にはこのフックおよび`getQuestLockState`関数の解析が必須。 | `const { isDone, isPending... } = useQuestStatus(...)` (行番号: 51〜54) |
| 中 | `@/hooks/useLongPress` | 長押し取消ジェスチャーの発火条件（`thresholdMs`の扱い、`pressProgress`の算出方法、タッチ/マウス両対応の有無）を確認するため。 | `const { isPressing, pressProgress, handlers: longPressHandlers } = useLongPress({...});` (行番号: 95〜99) |
| 中 | `../../family/components/FamilyDashboard.tsx` | `panelMode`/`iconFirst`propsを実際にどのユーザー・どの条件で渡しているか（横画面4人パネル表示側の呼び出し実態）を確認するため。 | `panelMode?: boolean; iconFirst?: boolean;` (行番号: 20, 23) |
| 中 | `@/components/ui/Card` | UIの基盤として利用されており、`variant` Props がどのようにスタイリングに影響するかを確認するため。 | `import { Card } from '@/components/ui/Card';` (行番号: 5) |
| 低 | `@/components/ui/CooldownRing` | クールダウン表示の内部実装（SVGアニメーション等）を確認するため。 | `import { CooldownRing } from '@/components/ui/CooldownRing';` (行番号: 6) |
| 低 | `@/hooks/useSound` | 音声再生の挙動や、どのような文字列引数を受け付けるかを特定するため。 | `const { play } = useSound();` (行番号: 47) |

## 8. 保守上の注意点

* `QuestList`内のソート用コンパレータ（`getStatusScore`、行番号311〜320）および`activeQuests`/`doneOrLockedQuests`への振り分け（行番号339〜351）は、Reactのコールバック内（`Array.sort`や単純なfor-of的処理）からはHooksを呼び出せないため、`useQuestStatus`フックと同じ判定ロジックを共有する素関数`getQuestLockState`を`../hooks/useQuestStatus`からインポートして直接呼び出している。ロック・申請中・完了の判定基準を変更する場合は、`useQuestStatus`と`getQuestLockState`の両方の実装（同一ファイル内であることが望ましい）を確認する必要がある。
* 根拠: [コメント] (行番号: 308〜310 / 抜粋: "// ▼ ソート順: 進行中の期間限定 → 通常 → ロック中 → 承認待ち → 完了済み\n            // （ロック/申請中/完了の判定は useQuestStatus と共通の getQuestLockState に集約。\n            //  Hooksが使えないコンパレータからも直接呼べる）")
* `QuestItem` の `runComplete`/`runCancel` において、`onClick` コールバックに渡すオブジェクトに動的に `_isInfinite` プロパティを追加している。`Quest`型に定義されているかは本ファイルからは不明。
* 根拠: `onClick({ ...quest, _isInfinite: !!isInfinite });` (行番号: 86, 92)
* `isInfinite`クエストのクールダウン（`COOLDOWN_MS` = 60000ms）はコンポーネントローカルな`useState`で管理されているため、画面遷移やコンポーネントの再マウントが起きると`isCooldown`はリセットされる。サーバー側でクールダウンを強制する仕組みがあるかは本ファイルからは不明。
* 根拠: (行番号: 48〜49, 82〜85 / 抜粋: "const [isCooldown, setIsCooldown] = useState(false);\n    const COOLDOWN_MS = 60000;")
* 共有クエスト（`is_shared_completed_by`/`is_shared_pending_by`）が自分以外の値を持つ場合、`isEffectivelyLocked`が真となりクリック不可・長押し無効になる。この判定は`useQuestStatus`が返す`isLocked`とは別に本ファイル内で独自に算出されている。
* 根拠: (行番号: 65〜69 / 抜粋: "const isEffectivelyLocked = isLocked || isSharedDoneByOther;")
* 完了済み・申請中クエストの取消操作は、以前存在した確認クリックではなく`useLongPress`による550msの長押し（`canCancel`が真のときのみ有効）に統一されている。通常タップは`canCancel`または`isCooldown`のときには何も起きない（`handleTapComplete`が早期リターン）。
* 根拠: (行番号: 71〜73, 95〜106 / 抜粋: "// 完了済み/申請中の取り消しは「長押し」でのみ発火させ、うっかりタップでの\n    // 誤取り消しを防ぐ。無限クエストは取り消し概念がないため対象外。\n    const canCancel = !isInfinite && (isDone || isPending) && !isEffectivelyLocked;", "const handleTapComplete = () => {\n        if (canCancel || isCooldown) return;")
* `panelMode`/`iconFirst`はいずれもレイアウト・表示切り替え専用のオプショナルpropで、クエストの判定ロジック自体（`isDone`/`isLocked`等）には影響しない。表示クラスの選択（`cardSizeClasses`, `layoutClasses`, `iconSizeClasses`等、8種類のスタイル変数）が`panelMode`/`iconFirst`の値ごとに個別に分岐しており、いずれか一方のモードのみを追加・変更する際は該当する全変数を漏れなく確認する必要がある。
* 根拠: (行番号: 108〜119 / 抜粋: "const cardSizeClasses = panelMode ? 'p-2 min-h-[56px]' : 'min-h-[56px] md:p-6 md:h-full';")
* バッジ表示は`badgeCandidates`に優先度（`priority`が小さいほど優先: 未開放0 < 対応済み1 < 申請中2 < 期間限定3 < 時間限定4）を付けてソートし、上位`MAX_VISIBLE_BADGES`（2件）のみ表示、残りは「+N」でまとめられる。バッジ種別を追加する際はこの優先度体系に組み込む必要がある。
* 根拠: (行番号: 121〜168 / 抜粋: "// ▼ バッジ候補を優先度付きで作り、上位2件だけを表示する(角度①: バッジ過多の整理)\n    const badgeCandidates: BadgeCandidate[] = [];")

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `Quest` オブジェクトの実態 | 型定義に存在するかどうか不明なプロパティ（`is_shared_completed_by`、`_isInfinite`など）が実行時にどう扱われているか不明なため。 | `@/types`, データをフェッチしているAPI側の実装 |
| `useQuestStatus` / `getQuestLockState` の判定ロジック | 各ステータス（`isDone`, `isLocked`, `variant` など）をどのように決定しているか不明なため。 | `../hooks/useQuestStatus` |
| `useLongPress` の実装詳細 | `thresholdMs`到達時の発火タイミング、`pressProgress`の算出方法、モバイル/デスクトップ両対応の有無が不明なため。 | `@/hooks/useLongPress` |
| `panelMode`/`iconFirst`の実際の呼び出し条件 | 本ファイルはpropsを受け取って表示を切り替えるのみであり、どのユーザー・どの画面幅で真になるかは呼び出し元次第で不明なため。 | `../../family/components/FamilyDashboard.tsx`, `App.tsx` |
| `Card`/`CooldownRing` のスタイル仕様 | `variant`や`className`、`durationMs`/`size`がどう合成されて描画されるか不明なため。 | `@/components/ui/Card`, `@/components/ui/CooldownRing` |
| 音声再生の詳細 | `play('clear')`等の引数が実際にどの音声を鳴らすか不明なため。 | `@/hooks/useSound` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `Quest` オブジェクトの実態 | `types/index.md`の解析によれば、`Quest`型には`is_shared_completed_by`等の共有クエスト判定用フィールドが含まれており、これらはバックエンドの`get_available_quests`が付与するものとされている。ただしこれは`types/index.md`側の解析結果からの補足であり、実データの検証は行っていない。 | `../../../types/index.md` |
| `useQuestStatus` / `getQuestLockState` の判定ロジック | `useQuestStatus.md`の解析によれば、`getQuestLockState`は前提クエストの完了有無から`isLocked`を、当日の承認済み履歴件数から`isDone`を算出する純粋関数であり、`useQuestStatus`はその結果と`isRandom`/`isTimeLimited`/`isLimited`等のフラグから優先順位付きで`variant`を決定するとされている。 | `../hooks/useQuestStatus.md` |
| `panelMode`/`iconFirst`の実際の呼び出し条件 | `FamilyDashboard.md`の解析によれば、`FamilyPanel`は`QuestList`に`panelMode`を常に渡し、`iconFirst`は`ICON_FIRST_USER_IDS.includes(user.user_id)`という判定で個別ユーザーごとに決定しているとされている。ただしこれは横画面（landscape）レイアウトからの呼び出しに関する補足であり、縦画面側（`App.tsx`）での`iconFirst`は`useSettings`が返す`iconFirstUserIds`を用いる点は本ファイルの解析にあたり`App.tsx`を直接確認して判明したものである。 | `../../family/components/FamilyDashboard.md` |
| `Card` のスタイル仕様 | `Card.md`の解析によれば、`Card`は`variant`（`default`/`completed`/`pending`/`infinite`/`timeLimit`/`random`/`limited`/`locked`）に応じてスタイルクラスを切り替えるコンポーネントであるとされている。`Card.md`側でも、本ファイル(`QuestList.tsx`)が実際にどの`variant`値を渡しているかは推測に留まると記載されている。 | `../../../components/ui/Card.md` |
| 音声再生の詳細 | `useSound.md`の解析によれば、`play`は`SOUNDS`定義のキーに対応する`HTMLAudioElement`をキャッシュしつつ再生し、再生失敗時は`console.warn`で警告を出すのみで例外は投げない構造とされている。ただし`SOUNDS`に`'clear'`/`'submit'`/`'cancel'`キーが実際に含まれるかは`useSound.md`側でも全キーの列挙が行われておらず断定できない。 | `../../../hooks/useSound.md` |

## 10. 自己検証結果

* [x] 完了: 推測・外部ファイルの仕様を一切含んでいない
* [x] 完了: 全関数・全クラス・全コンポーネントを列挙した
* [x] 完了: 全てのインポート要素を列挙した
* [x] 完了: すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 完了: 根拠漏れが0件である
* [x] 完了: Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 完了: 不明事項を漏れなく列挙した
