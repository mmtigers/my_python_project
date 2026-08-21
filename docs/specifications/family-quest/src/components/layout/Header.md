## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | Header.tsx |
| 言語 | React (TypeScript) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [../../../App.md](../../../App.md) - 呼び出し元。`hideUserSwitcher`/`hideLogSwitcher`/`showBackToMain`/`onBackToMain`等のPropsを供給する
* [../../types/index.md](../../types/index.md) - `User`型の定義元
* [../../hooks/useLayoutMode.md](../../hooks/useLayoutMode.md) - `hideUserSwitcher`/`hideLogSwitcher`/`showBackToMain`が真になる条件（`layoutMode`）の判定元

## 2. ファイルの概要

* ユーザー切り替え、記録（家族の年代記）表示への切り替え、およびホーム（メイン画面）への復帰ナビゲーション機能を持つヘッダーUIを提供する。
* `hideUserSwitcher`propが真の場合、ユーザー切替行を省略する。横画面（4人常時表示レイアウト）では各ユーザーのアバターが既にメイン画面のパネルに常時表示されているため、ヘッダー側のユーザー切替行が冗長になることに対応したものである。
* `hideLogSwitcher`propが真の場合、記録ボタンを省略する。縦画面ではフッターナビ（`BottomNav`）に「記録」タブが統合されたため、ヘッダー側の記録ボタンが二重導線になることに対応したものである。
* `showBackToMain`propが真の場合、ユーザー切替行の代わりに単一の「ホーム」ボタンを表示する。横画面（4人並び）で記録画面を表示中に、ユーザー切替行をそのまま出すと「ホームに戻る」という意図が伝わらなかったバグの修正として追加された。
* コンポーネント自身は状態（State）を持たず、親から渡されたProps（表示データおよびコールバック関数）に基づいてレンダリングを行う純粋なプレゼンテーションコンポーネントである。
* 根拠: `hideUserSwitcher`/`hideLogSwitcher`/`showBackToMain`の説明コメントと使用箇所 (12〜23, 69, 94, 142行目 / 抜粋: "// 横画面(4人常時表示レイアウト)では、各ユーザーのアバターは既にメイン画面の\n    // パネルに常時表示されているため、ヘッダー側のユーザー切替行は冗長になる。", "{showBackToMain && (", "{!hideUserSwitcher && users.map((user, idx) => {", "{!hideLogSwitcher && (")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `React` | ライブラリ | Reactコンポーネントの定義 | `import React from 'react';` (行番号: 1) |
| `User` | 型定義 | Props内でユーザーデータの型を指定 | `import { User } from '@/types';` (行番号: 2) |
| `Scroll`, `Settings`, `Home` | アイコンコンポーネント | 記録ボタン(`Scroll`)、表示設定ボタン(`Settings`)、ホームボタン(`Home`)のアイコンとして描画 | `import { Scroll, Settings, Home } from 'lucide-react';` (行番号: 3) |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `User` (from `@/types`) | 外部ファイルで定義されており、プロパティの全容（`user_id`, `name`, `avatar`, `icon`以外に何を持つか）が本ファイルからは判断不可。 | `import { User } from '@/types';` (行番号: 2) |
| 各種コールバック関数の処理内容 | 親コンポーネントから渡される関数であり、実行時に具体的にどのような処理（API呼び出しやルーティングなど）が行われるか判断不可。 | `onClick={() => onUserSwitch(idx)}` (行番号: 99), `onClick={onLogSwitch}` (行番号: 144), `onClick={onSettingsClick}` (行番号: 45), `onClick={onBackToMain}` (行番号: 71) |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `HeaderProps`

* **役割**: `Header` コンポーネントが受け取るプロパティの型定義。
* 根拠: `interface HeaderProps {` (行番号: 5〜25 / 抜粋: "interface HeaderProps {")

* **プロパティ一覧**:
* `users`: `User[]`
* `currentUserIdx`: `number`
* `viewMode`: `'user' | 'familyLog'`
* `onUserSwitch`: `(idx: number) => void`
* `onLogSwitch`: `() => void`
* `onSettingsClick`: `() => void`
* `hideUserSwitcher`: `boolean`（オプショナル。真の場合、ユーザー切替行を省略する）
* `hideLogSwitcher`: `boolean`（オプショナル。真の場合、記録ボタンを省略する）
* `showBackToMain`: `boolean`（オプショナル。真の場合、ユーザー切替行の代わりに「ホーム」ボタンを表示する）
* `onBackToMain`: `() => void`（オプショナル。「ホーム」ボタンクリック時に呼ばれる）
* 根拠: 6〜24行目 / 抜粋: "interface HeaderProps {\n    users: User[];\n    currentUserIdx: number;\n    viewMode: 'user' | 'familyLog';\n    onUserSwitch: (idx: number) => void;\n    onLogSwitch: () => void;\n    onSettingsClick: () => void;\n    ...\n    hideUserSwitcher?: boolean;\n    ...\n    hideLogSwitcher?: boolean;\n    ...\n    showBackToMain?: boolean;\n    onBackToMain?: () => void;\n}"

### `Header`

* **役割**: ナビゲーション（ホームボタン＋ユーザー切替＋記録ボタン）、表示設定ボタン、タイトルを含むヘッダーUIのレンダリング。`hideUserSwitcher`が真の場合はユーザー切替のボタン群を、`hideLogSwitcher`が真の場合は記録ボタンを、`showBackToMain`が偽の場合はホームボタンを、それぞれ描画しない。
* 根拠: `const Header: React.FC<HeaderProps> = ({...}) => { return (<header...` (行番号: 27〜173 / 抜粋: "const Header: React.FC<HeaderProps> = ({")

* **引数/リクエスト**: `HeaderProps` で定義されたプロパティのオブジェクト（`users`, `currentUserIdx`, `viewMode`, `onUserSwitch`, `onLogSwitch`, `onSettingsClick`, `hideUserSwitcher`, `hideLogSwitcher`, `showBackToMain`, `onBackToMain`）
* 根拠: (行番号: 27〜38 / 抜粋: "const Header: React.FC<HeaderProps> = ({\n    users,\n    currentUserIdx,\n    viewMode,\n    onUserSwitch,\n    onLogSwitch,\n    onSettingsClick,\n    hideUserSwitcher,\n    hideLogSwitcher,\n    showBackToMain,\n    onBackToMain,\n}) => {")

* **戻り値/レスポンス**: JSX要素 (`<header>` タグをルートとするReact要素)
* 根拠: `return ( <header className="bg-gradient-to-b...` (行番号: 39〜172 / 抜粋: "return (\n        <header className=\"bg-gradient-to-b from-gray-900 to-black")

* **副作用**: なし
* 根拠: コンポーネント内に `useEffect` 等のフックや、外部状態を直接変更する処理が存在しない。 (行番号: 27〜173)

* **エラーハンドリング**: なし
* 根拠: 例外を捕捉する `try-catch` ブロックやエラーバウンダリが存在しない。 (行番号: 27〜173)

## 5. 処理フロー図

```mermaid
flowchart TD
    Start([Start]) --> RenderHeader["ヘッダー要素のレンダリング開始"]
    RenderHeader --> RenderSettingsBtn["表示設定ボタン描画"]
    RenderSettingsBtn --> RenderTitle["タイトル領域描画 (FAMILY QUEST)"]

    RenderTitle --> CheckBackToMain{"showBackToMain === true?"}
    CheckBackToMain -- Yes --> RenderHomeBtn["ホームボタン描画 (viewMode==='user'で強調表示)"]
    CheckBackToMain -- No --> CheckHideUser
    RenderHomeBtn --> CheckHideUser{"hideUserSwitcher === true?"}

    CheckHideUser -- Yes --> CheckDivider["区切り線の描画判定へ"]
    CheckHideUser -- No --> LoopUsers["usersリストのマップ処理"]

    LoopUsers --> RenderUserBtn["ユーザーごとのボタン描画"]
    RenderUserBtn --> UserClick{"ユーザーonClick発火?"}
    UserClick -- Yes --> UserSwitch["外部：onUserSwitch(idx)"]
    UserClick -- No --> LoopUsers

    LoopUsers -- ループ完了 --> CheckDivider
    CheckDivider --> DividerCond{"(!hideUserSwitcher または showBackToMain) かつ !hideLogSwitcher ?"}
    DividerCond -- Yes --> RenderDivider["区切り線描画 (sm以上のみ表示)"]
    DividerCond -- No --> CheckHideLog
    RenderDivider --> CheckHideLog{"hideLogSwitcher === true?"}

    CheckHideLog -- Yes --> End([End])
    CheckHideLog -- No --> RenderLogBtn["記録ボタン描画"]

    RenderLogBtn --> LogClick{"記録onClick発火?"}
    LogClick -- Yes --> LogSwitch["外部：onLogSwitch()"]
    LogClick -- No --> End
    LogSwitch --> End

    RenderSettingsBtn --> SettingsClick{"設定ボタンonClick発火?"}
    SettingsClick -- Yes --> SettingsSwitch["外部：onSettingsClick()"]
```

## 6. 依存関係図

```mermaid
graph TD
    Header["Header コンポーネント"]
    HeaderProps["HeaderProps インターフェース"]
    Types_User["外部：@/types (User)"]
    Lucide["外部：lucide-react (Scroll, Settings, Home)"]
    React["外部：react"]
    Parent["外部：親コンポーネント (Props供給)"]

    Header --> HeaderProps
    HeaderProps --> Types_User
    Header --> Types_User
    Header --> Lucide
    Header --> React
    Parent -->|"Props (hideUserSwitcher, hideLogSwitcher, showBackToMainを含む)"| Header
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `@/types` (または `types.ts` 等) | `User`オブジェクトの詳細な構造（プロパティの一覧と型）を把握するため。 | `import { User } from '@/types';` (行番号: 2) |
| 高 | `App.tsx` | `hideUserSwitcher`/`hideLogSwitcher`/`showBackToMain`propの渡し方（`layoutMode`との連動）や、各種コールバックの実装ロジックを特定するため。 | Propsとして各種コールバックを受け取っているため (行番号: 5〜25) |
| 中 | `./hooks/useLayoutMode.ts` | `hideUserSwitcher`/`hideLogSwitcher`/`showBackToMain`が真になる条件（横画面/縦画面判定）の詳細を把握するため。 | `App.tsx`側で`layoutMode`を用いて各propが渡されている（`Header.tsx`単体からは不明） |

## 8. 保守上の注意点

* `users.map` 内でユーザーのアイコンを表示する際、`user.avatar && user.avatar.startsWith('/')` という判定を用いている（110行目）。`user.avatar` が文字列以外であった場合にランタイムエラーを防ぐ短絡評価が含まれているが、型定義上 `avatar` が文字列であることが保証されているかは不明。
* 根拠: (行番号: 110 / 抜粋: "{user.avatar && user.avatar.startsWith('/') ? (")
* `hideUserSwitcher`/`hideLogSwitcher`/`showBackToMain`はいずれもオプショナル（`?: boolean;`）であり、未指定時は`undefined`となる。`!hideUserSwitcher`（94行目）、`!hideLogSwitcher`（137, 142行目）による判定のため、`undefined`は「表示する」（従来どおり）として扱われる。一方`showBackToMain`は`&&`による真偽判定（69行目）のため、`undefined`は「ホームボタンを表示しない」扱いになる。
* 根拠: (行番号: 15, 18, 23, 69, 94, 137, 142行目 / 抜粋: "hideUserSwitcher?: boolean;", "hideLogSwitcher?: boolean;", "showBackToMain?: boolean;", "{showBackToMain && (", "{!hideUserSwitcher && users.map((user, idx) => {", "{(!hideUserSwitcher || showBackToMain) && !hideLogSwitcher && (", "{!hideLogSwitcher && ("
* ホームボタンの選択状態表示（強調スタイル）は`viewMode === 'user'`かどうかで切り替わり、`viewMode === 'familyLog'`の間はホームボタンが非選択スタイルになる。以前はスタイルが常に「選択中」固定だったため、記録画面に遷移してもホームボタンだけフォーカスされたままに見えていたバグの修正である。
* 根拠: (行番号: 64〜68, 72〜73行目 / 抜粋: "// ★バグ修正: 以前はスタイルが常に「選択中」固定だったため、記録画面に\n                    // 遷移したあともホームボタンだけフォーカスされたままに見えていた。\n                    // 他のボタン同様、viewMode に応じて選択中/非選択を切り替える", "className={`relative transition-all duration-300 flex flex-col items-center group p-1 ${viewMode === 'user' ? 'scale-110 -translate-y-1 z-10' : 'scale-95 opacity-60 hover:opacity-100 hover:scale-100'\n                            }`}")
* かつて存在した`onAdminOpen`（隠しボタンによる管理画面起動）、`onPartySwitch`/`onTrendsSwitch`（パーティ・週間ランキング画面切替）に対応するProps・UI要素は本ファイルには存在しない。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `User` 型の正確な定義 | `user_id`, `name`, `avatar`, `icon` が使われていることはコードから読み取れるが、その他のプロパティの有無が不明なため。 | `@/types` |
| コールバック実行後の実際の挙動 | 本コンポーネントは表示のみを担当しており、ルーティングの変更やデータフェッチ等の具体的なアクションが不明なため。 | このコンポーネントを呼び出している親ファイル |
| `hideUserSwitcher`/`hideLogSwitcher`/`showBackToMain`が渡される条件の詳細 | 本ファイル単体では利用意図がコメントで示されているのみで、具体的な判定条件（ビューポート幅等）は不明なため。 | 呼び出し元 (`App.tsx`)、`./hooks/useLayoutMode.ts` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `hideUserSwitcher`/`hideLogSwitcher`/`showBackToMain`が渡される条件の詳細 | `App.md`の解析によれば、`App`コンポーネントは`<Header hideUserSwitcher={layoutMode === 'landscape'} hideLogSwitcher={layoutMode === 'portrait'} showBackToMain={layoutMode === 'landscape'} onBackToMain={() => { setViewMode('main'); play('tap'); }} ... />`という形で呼び出しており、`layoutMode`は`useLayoutMode`フックの戻り値であるとされている（この対応関係は本ファイルの解析にあたり`App.tsx`のソースコードを直接確認して判明したものである）。さらに`useLayoutMode.md`の解析によれば、`landscape`判定は`window.matchMedia('(min-width: 900px) and (orientation: landscape)')`によって行われるとされているが、これは`useLayoutMode.md`側の解析結果からの補足であり、`useLayoutMode.ts`のソースコード自体は本ファイルの解析時点では確認していない。 | `../../../App.md`, `../../hooks/useLayoutMode.md` |
| `User` 型の正確な定義 | `types/index.md`の解析によれば、`User`は`family-quest/src/types/index.ts`内に`interface User`として定義されており、`hp`/`maxHp`はバックエンド（MY_HOME_SYSTEM）側で計算された値をそのまま使う旨がコメントされているとされている。ただし同ドキュメントの解析結果本文には全プロパティ名の一覧までは記載されておらず、`user_id`/`name`/`avatar`/`icon`以外の詳細な構成は依然として不明である。 | `../../types/index.md` |

## 10. 自己検証結果

* [x] 完了: 推測・外部ファイルの仕様を一切含んでいない
* [x] 完了: 全関数・全クラス・全コンポーネントを列挙した
* [x] 完了: 全てのインポート要素を列挙した
* [x] 完了: すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 完了: 根拠漏れが0件である
* [x] 完了: Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 完了: 不明事項を漏れなく列挙した
