# family-quest 仕様書一覧

タスク(クエスト)をRPG風に管理するReact/TypeScript製SPA「Family Quest」の仕様書索引です。`src/`のディレクトリ構造をミラーする形で格納された53件の仕様書を、実際のディレクトリ構造に沿って整理しています。全体像・他サブシステムとの連携は[全体設計書.md](../全体設計書.md)の「3. サブシステムB: Family Quest」を参照してください。

「(廃止)」の付いた仕様書は、2026-08のFamily Quest大改修等で対応するソースファイル自体が削除済みのものです。削除された記録として残置されているのみで、新規の実装・参照の対象ではありません。

## ルート

| 仕様書 | 概要 |
| --- | --- |
| [App.md](./App.md) | ルートコンポーネント。アクティブなタブ・表示モード・選択中ユーザーなどのUI状態を一元管理し、`useLayoutMode`が返すレイアウトモードに応じて横画面用`FamilyDashboard`（4人常時表示）または縦画面用のタブ切替UIを描画する。 |
| [main.md](./main.md) | Reactツリーのレンダリングとプロバイダ（React Query等）設定を行うエントリーポイント。URLパスに`/camera`を含むかで`CameraDashboard`または`App`をルートにマウントする。 |

## src/components/layout

| 仕様書 | 概要 |
| --- | --- |
| [Header.md](./src/components/layout/Header.md) | ユーザー切替および記録（家族の年代記）表示へのナビゲーション機能を持つヘッダーUI。状態を持たないプレゼンテーションコンポーネントで、`hideUserSwitcher`が真の場合はユーザー切替行を省略する。 |
| [BottomNav.md](./src/components/layout/BottomNav.md) | 画面下部固定のフッターナビゲーション。「クエスト」「ごほうび」「もちもの」「記録」の4タブで構成される。 |

## src/components/ui

| 仕様書 | 概要 |
| --- | --- |
| [AvatarUploader.md](./src/components/ui/AvatarUploader.md) | アバター画像の選択・プレビュー・サーバーへのアップロードを行うモーダルUIコンポーネント。エラー・成功メッセージはモーダル内のインラインUIで表示する。 |
| [BattleEffect.md](./src/components/ui/BattleEffect.md) | (廃止) ボス機能の廃止に伴い削除。クエスト完了（ボス攻撃時）の視覚演出を担っていたコンポーネント。 |
| [Button.md](./src/components/ui/Button.md) | Framer Motionによるアニメーション付きボタン。バリエーション・サイズ・ローディング状態を制御し、クリック時に外部フックで音声再生も行う。 |
| [Card.md](./src/components/ui/Card.md) | 汎用的なカード型UIコンポーネント。`variant`や`onClick`の有無に応じて適用スタイルを動的に切り替える。 |
| [CooldownRing.md](./src/components/ui/CooldownRing.md) | 無限クエストの連打防止クールダウン(60秒)の残り時間を、円形SVGプログレスリングとして視覚的に表示するコンポーネント。 |
| [CountUp.md](./src/components/ui/CountUp.md) | `framer-motion`のバネ物理モデルを用いて数値をカウントアップ表示するコンポーネント。プレフィックス・サフィックス・カンマ区切りに対応。 |
| [HlsPlayer.md](./src/components/ui/HlsPlayer.md) | `hls.js`を用いてHLS形式の映像ストリームを再生する汎用UIコンポーネント。カメラ機能で利用され、非対応ブラウザ向けのネイティブ再生フォールバックも備える。 |
| [LevelUpModal.md](./src/components/ui/LevelUpModal.md) | ユーザーのレベルアップ情報を表示し、同時に効果音を再生するモーダルコンポーネント。 |
| [MessageModal.md](./src/components/ui/MessageModal.md) | タイトル・メッセージ・任意アイコンと「OK」ボタンのみを持つ、状態を持たないシンプルなモーダルダイアログ。 |
| [Modal.md](./src/components/ui/Modal.md) | ESCキー・背景クリック・閉じるボタンに応じて非表示処理を呼び出す汎用モーダルウィンドウ。 |
| [SettingsModal.md](./src/components/ui/SettingsModal.md) | 表示密度・非識字モード対象ユーザー・ユーザー別パネルアクセントカラーをまとめて設定するモーダル画面。`useSettings`フック経由でContext状態を操作する。 |

## src/context

| 仕様書 | 概要 |
| --- | --- |
| [SettingsContext.md](./src/context/SettingsContext.md) | アプリ全体の表示設定（表示密度・非識字モード対象・テーマカラー）を`localStorage`に永続化して管理する`SettingsProvider`コンポーネント。 |
| [ToastContext.md](./src/context/ToastContext.md) | レベルアップ等の通知をブロッキングモーダルではなく自動で消えるトーストとして表示する`ToastProvider`コンポーネント。 |
| [settingsShared.md](./src/context/settingsShared.md) | `SettingsContext.tsx`/`useSettings.ts`から参照される型・定数・React Contextオブジェクトを集約するモジュール。 |
| [toastShared.md](./src/context/toastShared.md) | `ToastContext.tsx`/`useToast.ts`から参照される型定義とReact Contextオブジェクトを集約するモジュール。 |
| [useSettings.md](./src/context/useSettings.md) | `SettingsContext`から値を取得するカスタムフック。Provider外で呼ばれた場合は例外を投げる。 |
| [useToast.md](./src/context/useToast.md) | `ToastContext`から値を取得するカスタムフック。Provider外で呼ばれた場合は例外を投げる。 |

## src/features/admin/components

| 仕様書 | 概要 |
| --- | --- |
| [AdminDashboard.md](./src/features/admin/components/AdminDashboard.md) | (廃止) ボスHP調整機能およびファミリーマイレージ設定機能の廃止に伴い削除。 |

## src/features/camera/components

| 仕様書 | 概要 |
| --- | --- |
| [CameraDashboard.md](./src/features/camera/components/CameraDashboard.md) | 監視カメラ機能全体のエントリーポイントとなる全画面ダッシュボード。「ライブ映像」「録画再生」タブを切り替え、それぞれ`LiveView`・`RecordView`へ描画を委譲する。 |
| [LiveView.md](./src/features/camera/components/LiveView.md) | 複数の監視カメラのライブ映像を一覧表示するコンポーネント。サムネイルのグリッド表示と、1台を大きく表示するシングルビューを切り替えられる。 |
| [RecordView.md](./src/features/camera/components/RecordView.md) | 指定した日付・時刻の録画映像を複数カメラ分同期して再生する画面。同期再生・一時停止・再生速度の一括変更に対応する。 |

## src/features/camera/types

| 仕様書 | 概要 |
| --- | --- |
| [index.md](./src/features/camera/types/index.md) | カメラ機能配下で共有される、カメラ設定情報のデータ構造`CameraConfig`を定義する型定義ファイル。 |

## src/features/family/components

| 仕様書 | 概要 |
| --- | --- |
| [BossCard.md](./src/features/family/components/BossCard.md) | (廃止) ボス機能の廃止に伴い削除。出現中ボスの情報と攻撃ボタンを表示していたコンポーネント。 |
| [FamilyDashboard.md](./src/features/family/components/FamilyDashboard.md) | 横画面（常設デバイス）用のメインレイアウト。パパ・ママ・兄・妹を1行4列のグリッドで常時表示し、各パネル内でステータスとその日のクエスト一覧／ごほうび画面が完結する。 |
| [FamilyLog.md](./src/features/family/components/FamilyLog.md) | 家族のステータス情報（ランク・レベル・クエスト数・所持金）と、日付ごとにグループ化したタイムライン形式の冒険記録を表示する。 |
| [FamilyMileageCard.md](./src/features/family/components/FamilyMileageCard.md) | (廃止) ファミリーマイレージ機能の廃止に伴い削除。 |
| [FamilyParty.md](./src/features/family/components/FamilyParty.md) | (廃止) パーティ機能の廃止に伴い削除。 |
| [UserStatusCard.md](./src/features/family/components/UserStatusCard.md) | 選択中ユーザーのレベル・HP・EXP・所持ゴールド・獲得メダル等をRPGのステータスカード風に可視化するコンポーネント。 |
| [WeeklyTrends.md](./src/features/family/components/WeeklyTrends.md) | (廃止) 週間ランキング機能の廃止に伴い削除。 |

## src/features/guild/components

| 仕様書 | 概要 |
| --- | --- |
| [GuildBoard.md](./src/features/guild/components/GuildBoard.md) | (廃止) ギルド機能（ギルド討伐依頼板UI）の廃止に伴い削除。 |

## src/features/quest/components

| 仕様書 | 概要 |
| --- | --- |
| [ApprovalList.md](./src/features/quest/components/ApprovalList.md) | 承認待ちのクエストおよびアイテム使用申請の一覧を表示し、承認・拒否のアクションを実行するUIコンポーネント。アイテム承認確認は`Modal`コンポーネントで行う。 |
| [QuestList.md](./src/features/quest/components/QuestList.md) | クエスト一覧（`QuestList`）と個別クエスト（`QuestItem`）を描画。ターゲット・曜日で絞り込み、状態スコアでソートしてアニメーション付きで表示する。`panelMode`／`iconFirst`propで横画面パネル用・非識字年齢の子ども向け表示に切り替え可能。 |

## src/features/quest/hooks

| 仕様書 | 概要 |
| --- | --- |
| [useQuestStatus.md](./src/features/quest/hooks/useQuestStatus.md) | クエストの進行状態（完了・保留・ロック・無限クエストなど）を判定する純粋関数`getQuestLockState`と、それをラップして表示用タイトル・variantまで算出するカスタムフック`useQuestStatus`を提供する。 |

## src/features/shop/components

| 仕様書 | 概要 |
| --- | --- |
| [EquipmentShop.md](./src/features/shop/components/EquipmentShop.md) | (廃止) 装備機能（装備購入・装着UI）の廃止に伴い削除。 |
| [InventoryList.md](./src/features/shop/components/InventoryList.md) | ユーザーの所持アイテム（インベントリ）一覧を取得・表示し、アイテムの「使用」「キャンセル」を行うUIコンポーネント。React Queryでのポーリングと楽観的UI更新を行う。 |
| [RewardList.md](./src/features/shop/components/RewardList.md) | ユーザー情報と保有ゴールドに基づき、購入可能な商品を価格順にソートして表示するUIコンポーネント。購入可否に応じて見た目を切り替える。 |
| [RewardShop.md](./src/features/shop/components/RewardShop.md) | 「ごほうび」画面のコンポーネント。所持ゴールド表示 → 購入可能な報酬一覧（`RewardList`） → 所持品（`InventoryList`）の順に構成し、旧「もちもの」独立タブは廃止された。 |
| [ShopContainer.md](./src/features/shop/components/ShopContainer.md) | (廃止) デッドコードとして削除済み。「お店」「もちもの」タブ切り替えUIは本コンポーネントに集約されておらず、現在は`App.tsx`が`RewardShop`／`InventoryList`を直接マウントする。 |

## src/hooks

| 仕様書 | 概要 |
| --- | --- |
| [useGameData.md](./src/hooks/useGameData.md) | React Queryを用いて、ユーザー・クエスト・報酬・年代記・承認待ちインベントリ等のデータ取得・定期更新（ポーリング）と、完了・承認・却下・取消・購入のAPIリクエストを統合管理するカスタムフック。 |
| [useLayoutMode.md](./src/hooks/useLayoutMode.md) | 横画面／縦画面のレイアウト判定を行うカスタムフック。`window.matchMedia`の一致状況を購読し、リサイズや画面回転にリアルタイムに追従する。 |
| [useLongPress.md](./src/hooks/useLongPress.md) | クエスト取り消し操作の誤タップ防止のための長押しジェスチャーを提供するカスタムフック。 |
| [useOnlineStatus.md](./src/hooks/useOnlineStatus.md) | `navigator.onLine`と`online`/`offline`イベントを利用してオンライン／オフライン状態を検知するカスタムフック。 |
| [useSound.md](./src/hooks/useSound.md) | 効果音を再生するためのカスタムフック。音声ファイルパスを一元管理し、`HTMLAudioElement`インスタンスをキャッシュする。 |

## src/lib

| 仕様書 | 概要 |
| --- | --- |
| [apiClient.md](./src/lib/apiClient.md) | バックエンドAPIへ通信するHTTPクライアント（`ApiClient`クラス）を提供。ベースURL解決・共通ヘッダ設定・JSON送受信・エラーハンドリングをカプセル化する。 |
| [masterData.md](./src/lib/masterData.md) | サーバー接続エラー発生時のみ使用されるフォールバック用のダミーデータを定義・エクスポートする。 |
| [queryClient.md](./src/lib/queryClient.md) | `@tanstack/react-query`の`QueryClient`を初期化し、システム全体のデータフェッチングのデフォルト動作（再試行回数・キャッシュ期限等）を定義したインスタンスをエクスポートする。 |
| [utils.md](./src/lib/utils.md) | Tailwind CSSのクラス名をマージ（結合・競合解決）するユーティリティ関数`cn`を提供する。 |

## src/types

| 仕様書 | 概要 |
| --- | --- |
| [index.md](./src/types/index.md) | アプリケーション全体で使用される共通の型定義（ユーザー・クエスト・クエスト履歴・報酬・インベントリ等）を提供する。装備・ボス・ギルド依頼・ファミリーマイレージ関連の型は機能廃止に伴い削除済み。 |

## src/utils

| 仕様書 | 概要 |
| --- | --- |
| [gameHelpers.md](./src/utils/gameHelpers.md) | 曜日配列の提供、現在時刻（曜日インデックス・HHMM形式）の取得、指定レベルに応じた次レベル必要経験値の計算などを行う純粋なユーティリティ・定数群。 |
