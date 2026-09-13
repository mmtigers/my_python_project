## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `RoutineFlow.tsx` (family-quest/src/features/routine/components/RoutineFlow.tsx) |
| 言語 | React (TypeScript) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | `83b42db` (+同一ブランチ内で`THEME[flowKey].chip`の冗長参照をコードレビューで修正) |

## 関連ドキュメント

* [../../../lib/routineDataSchema.md](../../../lib/routineDataSchema.md) - 本ファイルがpropsの型として使う`RoutineFlowState`/`RoutineStep`の実装元。
* [../../../hooks/useRoutineData.md](../../../hooks/useRoutineData.md) - 本ファイルの`flow`/`onCompleteStep`/`isCompleting`各propsの実データの取得元（呼び出し元が`useRoutineData`から取得した値をそのまま渡す）。
* [../../../../App.md](../../../../App.md) - `RoutineFlow`（デフォルトエクスポート）と`RoutineFreeTimeBanner`（名前付きエクスポート）の両方を、縦画面の`activeTab === 'quest'`描画分岐内で使う消費元。
* [../../family/components/FamilyDashboard.md](../../family/components/FamilyDashboard.md) - `FamilyPanel`が同様に両コンポーネントを`compact`付きで使う消費元。

## 2. ファイルの概要

「きょうのすごろく」機能のUI本体を提供するファイル。平日の朝/夕方の生活動線を、縦一列のすごろく（路線図）として常時全ステップ表示するコンポーネント`RoutineFlow`（デフォルトエクスポート）と、自由時間中に代わりに表示する現在地バナー`RoutineFreeTimeBanner`（名前付きエクスポート）の2つのコンポーネントを定義する。療育目的として「現在地と見通しを常に見せる」ことを意図しており、ファイル冒頭のコメントによれば、自由時間の終了（チェックポイント）はサーバー側で時刻ベースに強制通過させる設計のため、本コンポーネントは現在のステップを手動で進める手段を持たず、現在のステップの完了報告のみを行う。
* 根拠: ファイル冒頭コメント (行番号: 1〜7 / 抜粋: "// family-quest/src/features/routine/components/RoutineFlow.tsx\n//\n// 「きょうのすごろく」— 平日の朝/夕方の生活動線を、縦一列のすごろく/路線図として\n// 常時全ステップ表示するUI(療育目的: 現在地と見通しを常に見せる)。\n// 自由時間の終了(チェックポイント)だけはサーバー側で時刻ベースに強制通過させる\n// (services/routine_service.py)ため、このコンポーネントには手動で進める手段を\n// 持たせず、現在のステップの完了報告のみを行う。")
* 根拠: `export default RoutineFlow` (行番号: 174 / 抜粋: "export default RoutineFlow;")
* 根拠: `export const RoutineFreeTimeBanner` (行番号: 62 / 抜粋: "export const RoutineFreeTimeBanner: React.FC<{ flowKey: 'am' | 'pm'; flow: RoutineFlowState }> = ({ flowKey, flow }) => {")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `React` | 外部ライブラリ | コンポーネント定義 | 根拠: [インポート宣言] (行番号: 8 / 抜粋: "import React from 'react';") |
| `Bell`, `Check`, `Clock`, `LucideIcon` | 外部ライブラリ(`lucide-react`) | `Bell`はリマインド状態、`Check`は完了状態のアイコン、`Clock`はチェックポイント時刻表示のアイコン、`LucideIcon`は`ICONS`テーブルの値の型注釈 | 根拠: [インポート宣言] (行番号: 9 / 抜粋: "import { Bell, Check, Clock, LucideIcon } from 'lucide-react';") |
| `Droplet`, `UtensilsCrossed`, `Shirt`, `Sparkles`, `Star`, `DoorOpen`, `Waves`, `Cookie`, `Pencil`, `Moon`, `BedDouble` | 外部ライブラリ(`lucide-react`) | `ICONS`テーブルの各`icon_key`に対応するアイコンコンポーネント（`Star`は未知の`icon_key`に対するフォールバックにも使われる） | 根拠: [インポート宣言] (行番号: 10〜13 / 抜粋: "import {\n    Droplet, UtensilsCrossed, Shirt, Sparkles, Star, DoorOpen,\n    Waves, Cookie, Pencil, Moon, BedDouble,\n} from 'lucide-react';") |
| `RoutineFlowState`, `RoutineStep` | 外部モジュール(型定義) | `RoutineFlowState`は`RoutineFlow`/`RoutineFreeTimeBanner`propsの`flow`の型、`RoutineStep`は`RoutineStepRow`の`step`propの型 | 根拠: [インポート宣言] (行番号: 14 / 抜粋: "import { RoutineFlowState, RoutineStep } from '@/lib/routineDataSchema';") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `lucide-react`の各アイコンコンポーネント | 各アイコンの実際のSVG描画内容はライブラリ内部の実装であり、本ファイルからは名称と`size`propの指定のみが分かる。 | 根拠: [インポート宣言] (行番号: 9〜13) |
| `RoutineFlowState`/`RoutineStep`の詳細なフィールド定義 | `@/lib/routineDataSchema.ts`に実装があり、本ファイルからは各フィールドの参照箇所のみが分かる（詳細は`routineDataSchema.md`を参照）。 | 根拠: [インポート宣言] (行番号: 14 / 抜粋: "import { RoutineFlowState, RoutineStep } from '@/lib/routineDataSchema';") |
| `icon_key`の実際の値の全一覧（バックエンド側） | `ICONS`テーブルのキーは本ファイル内でのみ定義されており、バックエンド（`MY_HOME_SYSTEM/routine_data.py`と推測されるが本タスクでは未解析）が実際にどの`icon_key`文字列を送出するかは本ファイルからは不明。 | 根拠: [`ICONS`テーブル定義] (行番号: 16〜28) |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `ICONS` (モジュールレベル定数、非export)

* **役割**: バックエンドから送られてくる`step.icon_key`文字列を、実際に描画するアイコンコンポーネント（`LucideIcon`）にマッピングするルックアップテーブル。`wash`/`meal`/`clothes`/`teeth`/`free`/`leave`/`handwash`/`snack`/`homework`/`nightprep`/`sleep`の11キーを定義する。
* 根拠: [定数定義] (行番号: 16〜28 / 抜粋: "const ICONS: Record<string, LucideIcon> = {\n    wash: Droplet,\n    meal: UtensilsCrossed,\n    clothes: Shirt,\n    teeth: Sparkles,\n    free: Star,\n    leave: DoorOpen,\n    handwash: Waves,\n    snack: Cookie,\n    homework: Pencil,\n    nightprep: Moon,\n    sleep: BedDouble,\n};")

* **引数/リクエスト**: 該当なし（ルックアップテーブルであり関数ではない）
* **戻り値/レスポンス**: 該当なし
* **副作用**: なし
* **エラーハンドリング**: 該当なし（未定義キーへのアクセスは`undefined`を返すのみで、実際のフォールバック処理は`RoutineStepRow`側（`ICONS[step.icon_key] || Star`）が担う。8章参照）

### `THEME` (モジュールレベル定数、非export)

* **役割**: `am`（朝、黄色系）と`pm`（夕方、紫系）の2つの時間帯それぞれについて、`text`/`chip`/`node`/`nodeDone`/`seg`/`spotlight`/`btn`/`banner`の8種類のTailwind CSSクラス文字列をまとめたテーマ定義オブジェクト。`as const`で宣言され、`RoutineFlow`/`RoutineFreeTimeBanner`/`RoutineStepRow`の全てが`flowKey`（`'am' | 'pm'`）に応じてこのテーブルから対応するクラスを引いて使う。
* 根拠: [定数定義] (行番号: 30〜51 / 抜粋: "const THEME = {\n    am: {\n        text: 'text-yellow-400',\n        chip: 'bg-yellow-900/40 text-yellow-300 border-yellow-500',\n        node: 'bg-gradient-to-br from-yellow-300 to-yellow-500 text-yellow-950 border-yellow-300',\n        nodeDone: 'bg-yellow-900/40 border-yellow-500 text-yellow-300',\n        seg: 'bg-yellow-500',\n        spotlight: 'bg-gradient-to-br from-yellow-900/50 to-yellow-900/10 border-yellow-500/60',\n        btn: 'bg-gradient-to-r from-yellow-300 to-yellow-500 text-yellow-950 shadow-yellow-500/40',\n        banner: 'bg-yellow-900/30 border-yellow-500/50',\n    },\n    pm: {\n        text: 'text-purple-400',", "} as const;")

* **引数/リクエスト**: 該当なし
* **戻り値/レスポンス**: 該当なし
* **副作用**: なし
* **エラーハンドリング**: 該当なし

### `RoutineFlowProps` (型定義、非export)

* **役割**: `RoutineFlow`コンポーネントが受け取るPropsのインターフェース。`flowKey`（`'am' | 'pm'`、テーマ色の切り替えに使用）、`flow`（`RoutineFlowState`、当該フローの現在の状態）、`onCompleteStep`（現在のステップの完了報告コールバック）、`isCompleting`（送信中フラグ、任意）、`compact`（横画面パネル向けの縮小表示フラグ、任意）を持つ。
* 根拠: [インターフェース定義] (行番号: 53〜59 / 抜粋: "interface RoutineFlowProps {\n    flowKey: 'am' | 'pm';\n    flow: RoutineFlowState;\n    onCompleteStep: (stepKey: string) => void;\n    isCompleting?: boolean;\n    compact?: boolean;\n}")

* **引数/リクエスト**: 該当なし（型定義であり関数ではない）
* **戻り値/レスポンス**: 該当なし
* **副作用**: なし
* **エラーハンドリング**: 該当なし

### `RoutineFlow` (デフォルトエクスポート、コンポーネント)

* **役割**: `flowKey`/`flow`/`onCompleteStep`/`isCompleting`/`compact`のpropsを受け取り、`flow`の状態に応じて4通りの描画分岐を持つコンポーネント。(1) `flow.started`が偽（未開始）なら`null`を返し何も表示しない。(2) `flow.is_complete`が真なら、絵文字とテーマカラーを使った完了祝賀カード（「🎉」＋`{flow.title}・完了！`）を表示する。(3) `flow.in_free_time`が真なら、`RoutineFreeTimeBanner`（後述）へ描画を委譲する。(4) 上記いずれにも該当しない（誘導中）場合、`flow.steps`の各要素を`RoutineStepRow`（内部コンポーネント、後述）としてマップし、縦一列に並べて全ステップを常時表示する。
* 根拠: [コンポーネント本体] (行番号: 78〜111 / 抜粋: "const RoutineFlow: React.FC<RoutineFlowProps> = ({ flowKey, flow, onCompleteStep, isCompleting, compact }) => {\n    if (!flow.started) return null;")
* 根拠: 完了分岐 (行番号: 82〜89 / 抜粋: "if (flow.is_complete) {\n        return (\n            <div className={`rounded-2xl border p-5 text-center ${theme.spotlight}`}>\n                <div className=\"text-3xl mb-1\">🎉</div>\n                <div className={`font-bold ${theme.text}`}>{flow.title}・完了！</div>\n            </div>\n        );\n    }")
* 根拠: 自由時間分岐 (行番号: 91〜93 / 抜粋: "if (flow.in_free_time) {\n        return <RoutineFreeTimeBanner flowKey={flowKey} flow={flow} />;\n    }")
* 根拠: 誘導中の全ステップ描画 (行番号: 95〜110 / 抜粋: "return (\n        <div className=\"flex flex-col\">\n            {flow.steps.map((step, idx) => (\n                <RoutineStepRow\n                    key={step.key}\n                    flowKey={flowKey}\n                    step={step}\n                    checkpointTime={flow.checkpoint_time}\n                    isLast={idx === flow.steps.length - 1}\n                    onComplete={() => onCompleteStep(step.key)}\n                    isCompleting={!!isCompleting}\n                    compact={!!compact}\n                />\n            ))}\n        </div>\n    );")

* **引数/リクエスト**: `RoutineFlowProps`（`flowKey: 'am' | 'pm'`, `flow: RoutineFlowState`, `onCompleteStep: (stepKey: string) => void`, `isCompleting?: boolean`, `compact?: boolean`）
* 根拠: (行番号: 78 / 抜粋: "const RoutineFlow: React.FC<RoutineFlowProps> = ({ flowKey, flow, onCompleteStep, isCompleting, compact }) => {")

* **戻り値/レスポンス**: `null`（未開始時）、または上記4分岐いずれかのJSX要素
* 根拠: (行番号: 79, 83〜88, 92, 96〜109)

* **副作用**: なし（描画のみ。実際のAPI通信は`onCompleteStep`コールバック経由で呼び出し元、実際には`useRoutineData`の`completeStep`に委譲される）
* **エラーハンドリング**: なし（本コンポーネント自体は`flow`の値による分岐のみを行い、`onCompleteStep`の呼び出し結果（成功/失敗）を判定・表示する処理は持たない）

### `RoutineFreeTimeBanner` (名前付きエクスポート、コンポーネント)

* **役割**: 自由時間中の現在地表示専用のバナーコンポーネント。`flow.started`が偽、または`flow.in_free_time`が偽の場合は`null`を返す（`RoutineFlow`から`flow.in_free_time`が真の場合にのみ呼ばれる想定だが、本コンポーネント単体で直接呼び出された場合にも同じガードが働く）。星（`Star`）アイコンと「自由時間中」という見出し、`flow.checkpoint_time`が存在すれば「{checkpoint_time} になったら次へ進むよ」という案内文を表示する。
* 根拠: [コンポーネント本体] (行番号: 62〜76 / 抜粋: "export const RoutineFreeTimeBanner: React.FC<{ flowKey: 'am' | 'pm'; flow: RoutineFlowState }> = ({ flowKey, flow }) => {\n    if (!flow.started || !flow.in_free_time) return null;\n    const theme = THEME[flowKey];\n    return (\n        <div className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs ${theme.banner}`}>\n            <Star size={16} className={theme.text} />\n            <div className=\"flex-1 min-w-0\">\n                <div className={`font-bold ${theme.text}`}>自由時間中</div>\n                {flow.checkpoint_time && (\n                    <div className=\"text-gray-300 truncate\">{flow.checkpoint_time} になったら次へ進むよ</div>\n                )}\n            </div>\n        </div>\n    );\n};")
* 根拠: ファイル冒頭のコメント（自由時間中は別画面に任せる設計意図） (行番号: 61 / 抜粋: "// 自由時間中は別画面(既存のクエスト選択UI)に任せ、ここでは現在地バナーだけを表示する。")

* **引数/リクエスト**: `{ flowKey: 'am' | 'pm'; flow: RoutineFlowState }`
* 根拠: (行番号: 62 / 抜粋: "export const RoutineFreeTimeBanner: React.FC<{ flowKey: 'am' | 'pm'; flow: RoutineFlowState }> = ({ flowKey, flow }) => {")

* **戻り値/レスポンス**: `null`（`flow.started`が偽、または`flow.in_free_time`が偽の場合）、またはバナーのJSX要素
* 根拠: (行番号: 63 / 抜粋: "if (!flow.started || !flow.in_free_time) return null;")

* **副作用**: なし
* **エラーハンドリング**: なし（`flow.checkpoint_time`が`null`/falsy の場合は案内文自体を描画しない条件分岐のみで、例外処理は無い）
* 根拠: (行番号: 70〜72 / 抜粋: "{flow.checkpoint_time && (\n                    <div className=\"text-gray-300 truncate\">{flow.checkpoint_time} になったら次へ進むよ</div>\n                )}")

### `RoutineStepRow` (内部コンポーネント、非export)

* **役割**: `RoutineFlow`の誘導中分岐から`flow.steps`の各要素についてマップされる、1ステップぶんの行を描画する内部コンポーネント。左側に円形のステップノード（`step.status`に応じて`done`＝チェックマーク、`remind`＝ベルアイコン、それ以外＝`ICONS[step.icon_key] || Star`のアイコン）と、最終ステップでなければその下に接続線（`step.is_checkpoint`が真ならチェックポイント時刻を示すチップ、偽ならただの縦棒）を描画する。右側には、`step.status === 'current'`の場合は強調されたカード（見出し「🌟 今ここ」＋ステップ名＋「完了！」ボタン、`isCompleting`中は`disabled`）を、それ以外の場合は単純なラベル表示（`status === 'remind'`なら「まだだよ」バッジを先頭に付与）を描画する。`compact`propに応じてノード・アイコン・フォントサイズ・余白を縮小する。
* 根拠: [コンポーネント本体] (行番号: 113〜172 / 抜粋: "const RoutineStepRow: React.FC<{\n    flowKey: 'am' | 'pm';\n    step: RoutineStep;\n    checkpointTime: string | null;\n    isLast: boolean;\n    onComplete: () => void;\n    isCompleting: boolean;\n    compact: boolean;\n}> = ({ flowKey, step, checkpointTime, isLast, onComplete, isCompleting, compact }) => {")
* 根拠: アイコン・ノードサイズの算出とフォールバック (行番号: 122〜130 / 抜粋: "const theme = THEME[flowKey];\n    const Icon = ICONS[step.icon_key] || Star;\n    const nodeSize = compact ? 'w-8 h-8' : (step.status === 'current' ? 'w-14 h-14' : 'w-11 h-11');\n    const iconSize = compact ? 14 : (step.status === 'current' ? 24 : 18);\n\n    let nodeClass = 'border-gray-600 bg-gray-800 text-gray-500';\n    if (step.status === 'done') nodeClass = theme.nodeDone;\n    if (step.status === 'current') nodeClass = `${theme.node} ${!compact ? 'animate-pulse' : ''}`;\n    if (step.status === 'remind') nodeClass = 'bg-orange-900/40 border-orange-500 text-orange-300';")
* 根拠: 接続線・チェックポイントチップの分岐 (行番号: 138〜146 / 抜粋: "{!isLast && (\n                    step.is_checkpoint ? (\n                        <span className={`my-1 flex items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-[10px] font-bold whitespace-nowrap ${theme.chip}`}>\n                            <Clock size={10} />{checkpointTime}\n                        </span>\n                    ) : (\n                        <div className={`w-[3px] flex-1 min-h-[16px] rounded-full ${step.status === 'done' ? theme.seg : 'bg-gray-700'}`} />\n                    )\n                )}")
* 根拠: `current`ステップの完了ボタン (行番号: 150〜162 / 抜粋: "{step.status === 'current' ? (\n                    <div className={`rounded-2xl border p-4 ${theme.spotlight}`}>\n                        <div className={`flex items-center gap-1 text-xs font-bold mb-1 ${theme.text}`}>🌟 今ここ</div>\n                        <div className=\"text-xl font-black text-white mb-3\">{step.label}</div>\n                        <button\n                            type=\"button\"\n                            onClick={onComplete}\n                            disabled={isCompleting}\n                            className={`w-full rounded-full py-3 font-black shadow-lg disabled:opacity-50 ${theme.btn}`}\n                        >\n                            完了！\n                        </button>\n                    </div>\n                ) : (")

* **引数/リクエスト**: `{ flowKey: 'am' | 'pm'; step: RoutineStep; checkpointTime: string | null; isLast: boolean; onComplete: () => void; isCompleting: boolean; compact: boolean }`
* 根拠: (行番号: 113〜120)

* **戻り値/レスポンス**: JSX要素（1ステップぶんの行）
* 根拠: (行番号: 132〜171)

* **副作用**: 「完了！」ボタンのクリックにより`onComplete`（`RoutineFlow`側で`() => onCompleteStep(step.key)`として渡される）を呼び出す。
* 根拠: (行番号: 154〜160 / 抜粋: "<button\n                            type=\"button\"\n                            onClick={onComplete}\n                            disabled={isCompleting}")

* **エラーハンドリング**: なし（`onComplete`の呼び出し結果自体はこのコンポーネントの関知するところではない。`isCompleting`が真の間はボタンを`disabled`にするのみ）

## 5. 処理フロー図

以下は`RoutineFlow`コンポーネントの4分岐ロジックです。

```mermaid
flowchart TD
    Start(["RoutineFlow({flowKey, flow, onCompleteStep, isCompleting, compact}) 描画"]) --> StartedCheck{"flow.started === true ?"}
    StartedCheck -- No --> ReturnNull["return null (未開始・何も表示しない)"]
    StartedCheck -- Yes --> CompleteCheck{"flow.is_complete === true ?"}
    CompleteCheck -- Yes --> RenderCelebration["祝賀カード描画:\n🎉 + {flow.title}・完了！"]
    CompleteCheck -- No --> FreeTimeCheck{"flow.in_free_time === true ?"}
    FreeTimeCheck -- Yes --> DelegateBanner["RoutineFreeTimeBanner に描画を委譲"]
    FreeTimeCheck -- No --> RenderRail["flow.steps を map し、\nRoutineStepRow を縦一列に全ステップ描画(誘導中)"]

    RenderRail --> StepStatus{"各ステップの step.status"}
    StepStatus -- "current" --> CurrentCard["強調カード描画: 🌟 今ここ + ラベル + 完了！ボタン\n(onClickで onCompleteStep(step.key) 実行、isCompleting中はdisabled)"]
    StepStatus -- "done" --> DoneRow["チェックマークアイコン + ラベル(通常表示)"]
    StepStatus -- "remind" --> RemindRow["ベルアイコン + 「まだだよ」バッジ + ラベル"]
    StepStatus -- "locked" --> LockedRow["ICONS[icon_key]（無ければStar）アイコン + ラベル(グレー表示)"]

    subgraph "RoutineFreeTimeBanner(flowKey, flow)"
        BStart{"flow.started && flow.in_free_time ?"}
        BStart -- No --> BNull["return null"]
        BStart -- Yes --> BRender["Starアイコン + 「自由時間中」+\n(checkpoint_timeがあれば案内文)"]
    end
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "RoutineFlow.tsx"
        ICONS
        THEME
        RoutineFlow["RoutineFlow (default export)"]
        RoutineFreeTimeBanner["RoutineFreeTimeBanner (named export)"]
        RoutineStepRow["RoutineStepRow (内部, 非export)"]
    end

    subgraph "外部ライブラリ"
        React["react"]
        LucideReact["lucide-react\n(Bell/Check/Clock/Droplet/UtensilsCrossed/Shirt/\nSparkles/Star/DoorOpen/Waves/Cookie/Pencil/Moon/BedDouble/LucideIcon)"]
    end

    subgraph "外部モジュール"
        routineDataSchema["@/lib/routineDataSchema.ts\n(RoutineFlowState, RoutineStep)"]
    end

    subgraph "呼び出し元"
        App["../../../../App.tsx"]
        FamilyDashboard["../../family/components/FamilyDashboard.tsx (FamilyPanel)"]
    end

    RoutineFlow --> React
    RoutineFlow --> ICONS
    RoutineFlow --> THEME
    RoutineFlow --> RoutineStepRow
    RoutineFlow -->|"in_free_time時に委譲"| RoutineFreeTimeBanner
    RoutineFlow --> routineDataSchema

    RoutineFreeTimeBanner --> THEME
    RoutineFreeTimeBanner --> LucideReact
    RoutineFreeTimeBanner --> routineDataSchema

    RoutineStepRow --> THEME
    RoutineStepRow --> ICONS
    RoutineStepRow --> LucideReact
    RoutineStepRow --> routineDataSchema

    ICONS --> LucideReact

    App -->|"import (default) + import { RoutineFreeTimeBanner }"| RoutineFlow
    App -->|import| RoutineFreeTimeBanner
    FamilyDashboard -->|"import (default) + import { RoutineFreeTimeBanner }、compact付き"| RoutineFlow
    FamilyDashboard -->|import| RoutineFreeTimeBanner
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `MY_HOME_SYSTEM/services/routine_service.py`, `MY_HOME_SYSTEM/routine_data.py` | `icon_key`として実際にバックエンドが送出しうる値の全一覧を確認し、`ICONS`テーブル（11キー）に存在しない`icon_key`が来た場合の`Star`フォールバック（8章参照）が実際に発生しうるかを検証するため。 | `ICONS`テーブル (行番号: 16〜28)、`icon_key`の参照箇所 (行番号: 123) |
| 中 | `../../../hooks/useRoutineData.ts` | `onCompleteStep`に渡される`completeStep`関数の実際の通信・エラーハンドリング（`RoutineFlow`自身はこれを一切判定しない）を確認するため。 | `useRoutineData.md`参照 |
| 中 | `RoutineFlow.test.tsx`（テストファイルのため仕様書は持たない） | 各`step.status`分岐（`current`/`done`/`remind`/`locked`）の実際の描画結果、および完了ボタンクリック時の`onCompleteStep`呼び出し引数を確認するため。 | 本ファイル自体からはテストの内容は不明（除外対象ファイル） |
| 低 | `../../../../App.tsx`, `../../family/components/FamilyDashboard.tsx` | `compact`propが実際にどちらの消費元でどう設定されるか（縦画面は`compact`なし、横画面パネルは`compact`あり）を確認するため。 | `App.md`/`FamilyDashboard.md`のルーティング分岐箇所参照 |

## 8. 保守上の注意点

* **手動でチェックポイントを進める手段が存在しない（設計意図）**: ファイル冒頭のコメントの通り、自由時間の終了（チェックポイント）はサーバー側の時刻ベース判定のみに委ねる設計であり、本コンポーネントには「今すぐ次に進む」ボタンや、チェックポイントステップそのものを完了報告する手段は実装されていない。`RoutineStepRow`の完了ボタンは`step.status === 'current'`の任意のステップに対して呼ばれるが、チェックポイントの通過自体はこのボタン操作の結果ではなく、`useRoutineData`のポーリング（15秒間隔、`useRoutineData.md`参照）がサーバー側の状態変化を検知して反映するのを待つ形になる。
* 根拠: (行番号: 5〜7 / 抜粋: "// 自由時間の終了(チェックポイント)だけはサーバー側で時刻ベースに強制通過させる\n// (services/routine_service.py)ため、このコンポーネントには手動で進める手段を\n// 持たせず、現在のステップの完了報告のみを行う。")
* **未知の`icon_key`は`Star`へ無言でフォールバックする**: `RoutineStepRow`は`ICONS[step.icon_key] || Star`という式でアイコンを解決しており、`ICONS`テーブル（11キー）に存在しない`icon_key`をバックエンドが送出した場合、エラーや警告を出すことなく`Star`アイコンへ静かにフォールバックする。バックエンド側（`routine_data.py`と推測、未解析）に新しいステップ種別を追加する際は、対応する`icon_key`をこの`ICONS`テーブルにも追加しないと、意図しないアイコン（星）が表示され続ける。
* 根拠: (行番号: 123 / 抜粋: "const Icon = ICONS[step.icon_key] || Star;")
* **`RoutineFreeTimeBanner`は`RoutineFlow`から呼ばれる場合と直接呼ばれる場合の両方に対応する**: `RoutineFlow`は`flow.in_free_time`が真の場合に`RoutineFreeTimeBanner`へ描画を委譲するが、`RoutineFreeTimeBanner`自体も冒頭で同じ条件（`!flow.started || !flow.in_free_time`）をチェックして`null`を返すガードを持つ。これにより、`App.tsx`/`FamilyDashboard.tsx`側が`selectRoutineFlow`（`routineDataSchema.ts`、内部で`isRoutineFlowFreeTime`を使う。コードレビューで両ファイルの重複ロジックをこの関数へ集約）の戻り値`freeTimeKey`で判定した上で`RoutineFreeTimeBanner`を直接マウントする現在の使い方（`RoutineFlow`を介さない）でも安全に動作する。
* 根拠: (行番号: 63 / 抜粋: "if (!flow.started || !flow.in_free_time) return null;")
* **`compact`propはノード・アイコン・余白のサイズのみを変える視覚的な調整であり、機能自体は変わらない**: `compact`が真の場合、`RoutineStepRow`のノードサイズ・アイコンサイズ・行間パディングが縮小され、`current`ステップの`animate-pulse`アニメーションも無効化されるが、完了ボタンの機能や描画される情報の内容自体は`compact`の有無で変わらない。
* 根拠: (行番号: 124〜125, 129, 149 / 抜粋: "const nodeSize = compact ? 'w-8 h-8' : (step.status === 'current' ? 'w-14 h-14' : 'w-11 h-11');\n    const iconSize = compact ? 14 : (step.status === 'current' ? 24 : 18);", "if (step.status === 'current') nodeClass = `${theme.node} ${!compact ? 'animate-pulse' : ''}`;", "<div className={`flex-1 min-w-0 ${compact ? 'pb-2' : 'pb-5'}`}>")
* **[修正済み] `RoutineStepRow`内での`THEME`の冗長な再参照（コードレビューで発覚）**: `RoutineStepRow`は関数冒頭で`const theme = THEME[flowKey];`（行番号122）と一度だけ取り出しているにもかかわらず、チェックポイントチップのクラス名では以前`THEME[flowKey].chip`と`THEME`テーブルを直接再インデックスしていた（実害はない。`THEME`はモジュールレベルの静的定数のため）。他のスタイル参照箇所（`theme.node`/`theme.nodeDone`/`theme.seg`/`theme.spotlight`/`theme.text`/`theme.btn`等）と一貫させるため`theme.chip`に統一した。
* 根拠: (行番号: 122, 140 / 抜粋: "const theme = THEME[flowKey];", "<span className={`my-1 flex items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-[10px] font-bold whitespace-nowrap ${theme.chip}`}>")

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| バックエンドが実際に送出する`icon_key`の全一覧 | `ICONS`テーブルは11キーを定義しているが、これがバックエンドの全ステップ種別を網羅しているかどうかは本ファイル（フロントエンドのみ）からは確認できない。 | `MY_HOME_SYSTEM/routine_data.py`（ファイル名から推測、未確認）、`MY_HOME_SYSTEM/services/routine_service.py` |
| `flow.steps`が空配列の場合の描画結果 | `flow.steps.map(...)`は空配列に対しては単に何もレンダリングしない（React上は空の`<div className="flex flex-col">`のみが描画される）と推測されるが、バックエンドが実際にこの状態（誘導中かつステップ0件）を返しうるかどうかは本ファイルからは不明。 | `MY_HOME_SYSTEM/services/routine_service.py` |
| `step.is_checkpoint`が最終ステップ（`isLast === true`）の場合の表示 | `isLast`が真の場合、接続線・チェックポイントチップの描画ブロック自体がスキップされる（`{!isLast && (...)}`）ため、最終ステップが`is_checkpoint: true`の場合でもチェックポイント時刻チップは表示されない。これが意図された仕様か、想定されていない組み合わせなのかは本ファイルからは不明。 | `MY_HOME_SYSTEM/services/routine_service.py`（`steps`の生成ロジック、最終ステップが`is_checkpoint`になりうるか） |

## 相互参照による補足情報

なし（本タスクでは`MY_HOME_SYSTEM`側のバックエンドファイルを解析対象としていないため、上記の不明事項を補足する他ドキュメントは現時点で存在しない）。

## 10. 自己検証結果

* [x] 完了: 推測・外部ファイルの仕様を一切含んでいない
* [x] 完了: 全関数・全クラス・全コンポーネントを列挙した（デフォルトエクスポート`RoutineFlow`、名前付きエクスポート`RoutineFreeTimeBanner`、内部コンポーネント`RoutineStepRow`、モジュールレベル定数`ICONS`/`THEME`を含む）
* [x] 完了: 全てのインポート要素を列挙した
* [x] 完了: すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 完了: 根拠漏れが0件である
* [x] 完了: Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 完了: 不明事項を漏れなく列挙した
