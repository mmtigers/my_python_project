# family-quest 仕様書一覧

タスク(クエスト)をRPG風に管理するReact/TypeScript製SPA「Family Quest」の仕様書索引です。`src/`のディレクトリ構造をミラーする形で格納された56件の仕様書を、実際のディレクトリ構造に沿って整理しています。全体像・他サブシステムとの連携は[全体設計書.md](../全体設計書.md)の「3. サブシステムB: Family Quest」を参照してください。

対応するソースファイル自体が削除済みの仕様書は、末尾の「廃止済み仕様書一覧」に記録のみ残しています(Issue #402 で仕様書ファイル自体は削除済み。新規の実装・参照の対象ではありません)。

## ルート

| 仕様書 | 概要 |
| --- | --- |
| [App.md](./App.md) | ルートコンポーネント。アクティブなタブ・表示モード・選択中ユーザーなどのUI状態を一元管理し、`useLayoutMode`が返すレイアウトモードに応じて横画面用`FamilyDashboard`（4人常時表示）または縦画面用のタブ切替UIを描画する。 |
| [main.md](./main.md) | Reactツリーのレンダリングとプロバイダ（React Query等）設定を行うエントリーポイント。URLパスに`/camera`を含むかで`CameraDashboard`または`App`をルートにマウントする。PWAのService Workerを`registerSW`で明示登録し、1時間ごとの更新チェックと新SW有効化時の自動再読み込みを行う。 |

## src/components/layout

| 仕様書 | 概要 |
| --- | --- |
| [Header.md](./src/components/layout/Header.md) | ユーザー切替および記録（家族の年代記）表示へのナビゲーション機能を持つヘッダーUI。状態を持たないプレゼンテーションコンポーネントで、`showUserSwitcher`が偽の場合はユーザー切替行を省略する（2026-09-06 品質監査: 旧記述の`hideUserSwitcher`は #479 で`showUserSwitcher`へ改名済み。`Header.tsx` 行番号 21）。 |
| [BottomNav.md](./src/components/layout/BottomNav.md) | 画面下部固定のフッターナビゲーション。「クエスト」「ごほうび」「もちもの」「記録」の4タブで構成される。 |

## src/components/ui

| 仕様書 | 概要 |
| --- | --- |
| [AvatarUploader.md](./src/components/ui/AvatarUploader.md) | アバター画像の選択・プレビュー・サーバーへのアップロードを行うモーダルUIコンポーネント。エラー・成功メッセージはモーダル内のインラインUIで表示する。 |
| [Button.md](./src/components/ui/Button.md) | Framer Motionによるアニメーション付きボタン。バリエーション・サイズ・ローディング状態を制御し、クリック時に外部フックで音声再生も行う。 |
| [Card.md](./src/components/ui/Card.md) | 汎用的なカード型UIコンポーネント。`variant`や`onClick`の有無に応じて適用スタイルを動的に切り替える。 |
| [ChunkErrorBoundary.md](./src/components/ui/ChunkErrorBoundary.md) | `lazy()`チャンクの読み込み失敗(SW更新後の旧チャンク404)を捕捉し自動再読み込みするエラーバウンダリ。それ以外の描画エラーには「再読み込み」ボタン付きフォールバックを表示する。 |
| [ConfirmModal.md](./src/components/ui/ConfirmModal.md) | クエスト完了・報酬購入・クエスト却下の確認モーダル。Issue #552で`App.tsx`から抽出された。 |
| [CooldownRing.md](./src/components/ui/CooldownRing.md) | 無限クエストの連打防止クールダウン(60秒)の残り時間を、円形SVGプログレスリングとして視覚的に表示するコンポーネント。 |
| [CountUp.md](./src/components/ui/CountUp.md) | `framer-motion`のバネ物理モデルを用いて数値をカウントアップ表示するコンポーネント。プレフィックス・サフィックス・カンマ区切りに対応。 |
| [HlsPlayer.md](./src/components/ui/HlsPlayer.md) | `hls.js`を用いてHLS形式の映像ストリームを再生する汎用UIコンポーネント。カメラ機能で利用され、非対応ブラウザ向けのネイティブ再生フォールバックも備える。 |
| [MessageModal.md](./src/components/ui/MessageModal.md) | タイトル・メッセージ・任意アイコンを表示するモーダルダイアログ。`onRetry`が渡された場合は「閉じる」/「再試行」の2ボタン、渡されない場合は単一の「OK」ボタンを表示する。 |
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

## src/features/camera/components

| 仕様書 | 概要 |
| --- | --- |
| [CameraDashboard.md](./src/features/camera/components/CameraDashboard.md) | 監視カメラ機能全体のエントリーポイントとなる全画面ダッシュボード。「ライブ映像」「録画再生」タブを切り替え、それぞれ`LiveView`・`RecordView`へ描画を委譲する。設定歯車ボタンから`CameraSettingsModal`を開ける。 |
| [CameraSettingsModal.md](./src/features/camera/components/CameraSettingsModal.md) | カメラ単位の有効/無効を切り替えるモーダル。`PUT /api/cameras/settings/{camera_id}`で永続化し、成功時に`onToggled`経由で`CameraDashboard`側の一覧を再取得させる。 |
| [LiveView.md](./src/features/camera/components/LiveView.md) | 複数の監視カメラのライブ映像を一覧表示するコンポーネント。サムネイルのグリッド表示と、1台を大きく表示するシングルビューを切り替えられる。 |
| [RecordView.md](./src/features/camera/components/RecordView.md) | 指定した日付・時刻の録画映像を複数カメラ分同期して再生する画面。同期再生・一時停止・再生速度の一括変更に対応する。 |

## src/features/camera/types

| 仕様書 | 概要 |
| --- | --- |
| [index.md](./src/features/camera/types/index.md) | カメラ機能配下で共有される、カメラ設定情報のデータ構造`CameraConfig`を定義する型定義ファイル。 |

## src/features/family/components

| 仕様書 | 概要 |
| --- | --- |
| [FamilyDashboard.md](./src/features/family/components/FamilyDashboard.md) | 横画面（常設デバイス）用のメインレイアウト。パパ・ママ・兄・妹を1行4列のグリッドで常時表示し、各パネル内でステータスとその日のクエスト一覧／ごほうび画面が完結する。 |
| [FamilyLog.md](./src/features/family/components/FamilyLog.md) | ユーザーごとの列（カラム）に分けて、日付ごとにグループ化したタイムライン形式の冒険記録を表示する。以前あった家族の総力（パーティランク・総レベル等）の集計表示は廃止済み。 |
| [UserStatusCard.md](./src/features/family/components/UserStatusCard.md) | 選択中ユーザーの名前・職業クラス・レベル・所持ゴールド・獲得メダル数を表示するステータスカード。HP・EXP表示は廃止済み（Issue #327）。 |

## src/features/quest/components

| 仕様書 | 概要 |
| --- | --- |
| [ApprovalList.md](./src/features/quest/components/ApprovalList.md) | 承認待ちクエストの一覧を表示し、ボタン操作またはスワイプ（右＝承認／左＝却下）で承認・却下を行うUIコンポーネント。複数件あるときの一括承認・折りたたみ表示にも対応する。旧アイテム使用承認UIは廃止済み。 |
| [QuestList.md](./src/features/quest/components/QuestList.md) | クエスト一覧（`QuestList`）と個別クエスト（`QuestItem`）を描画。ターゲットで絞り込み（曜日フィルタはIssue #412 F-L1で削除）、状態スコアでソートしてアニメーション付きで表示する。`panelMode`／`iconFirst`propで横画面パネル用・非識字年齢の子ども向け表示に切り替え可能。 |

## src/features/quest/hooks

| 仕様書 | 概要 |
| --- | --- |
| [useQuestStatus.md](./src/features/quest/hooks/useQuestStatus.md) | クエストの進行状態（完了・保留・ロック・無限クエストなど）を判定する純粋関数`getQuestLockState`と、それをラップして表示用タイトル・variantまで算出するカスタムフック`useQuestStatus`を提供する。 |

## src/features/shop/components

| 仕様書 | 概要 |
| --- | --- |
| [InventoryList.md](./src/features/shop/components/InventoryList.md) | ユーザーの所持アイテム（インベントリ）一覧を取得・表示し、アイテムの「使用」「キャンセル」を行うUIコンポーネント。React Queryでのポーリングと楽観的UI更新を行う。 |
| [RewardList.md](./src/features/shop/components/RewardList.md) | ユーザー情報と保有ゴールドに基づき、購入可能な商品を価格順にソートして表示するUIコンポーネント。購入可否に応じて見た目を切り替える。 |
| [RewardShop.md](./src/features/shop/components/RewardShop.md) | 「ごほうび」画面のコンポーネント。購入可能な報酬一覧（`RewardList`）のみを描画する薄いラッパーで、所持ゴールド表示は呼び出し元のステータスカードに、所持品（`InventoryList`）表示は独立タブに、それぞれ役割が移管されている。 |

## src/hooks

| 仕様書 | 概要 |
| --- | --- |
| [useGameData.md](./src/hooks/useGameData.md) | React Queryを用いて、ユーザー・クエスト・報酬・年代記等のデータ取得・定期更新（ポーリング）と、完了・承認・却下・取消・購入のAPIリクエストを統合管理するカスタムフック。旧`pendingInventory`クエリはアイテム使用承認フロー廃止に伴い削除済み。 |
| [useLayoutMode.md](./src/hooks/useLayoutMode.md) | 横画面／縦画面のレイアウト判定を行うカスタムフック。`window.matchMedia`の一致状況を購読し、リサイズや画面回転にリアルタイムに追従する。 |
| [useLongPress.md](./src/hooks/useLongPress.md) | クエスト取り消し操作の誤タップ防止のための長押しジェスチャーを提供するカスタムフック。 |
| [useConfirmDialog.md](./src/hooks/useConfirmDialog.md) | 完了・購入・却下の確認モーダルまわりの状態クラスタ(confirmMode/confirmTarget/confirmUser/rejectReason/isConfirming)を保持するカスタムフック。Issue #552で`App.tsx`から新規抽出。 |
| [useCurrentUser.md](./src/hooks/useCurrentUser.md) | 選択中ユーザーの`localStorage`永続化解決・保存を行うカスタムフック。Issue #552で`App.tsx`から新規抽出。 |
| [useOnlineStatus.md](./src/hooks/useOnlineStatus.md) | `navigator.onLine`と`online`/`offline`イベントを利用してオンライン／オフライン状態を検知するカスタムフック。 |
| [useSound.md](./src/hooks/useSound.md) | 効果音を再生するためのカスタムフック。音声ファイルパスを一元管理し、`HTMLAudioElement`インスタンスをキャッシュする。 |

## src/lib

| 仕様書 | 概要 |
| --- | --- |
| [actionResult.md](./src/lib/actionResult.md) | `useGameData`の各種ミューテーション結果を表す`ActionResult`型と、エラーメッセージを解決する`resolveErrorText`を提供する。Issue #552で`App.tsx`から新規抽出。 |
| [apiClient.md](./src/lib/apiClient.md) | バックエンドAPIへ通信するHTTPクライアント（`ApiClient`クラス）を提供。ベースURL解決・共通ヘッダ設定・JSON送受信・エラーハンドリングをカプセル化する。 |
| [currentUserStorage.md](./src/lib/currentUserStorage.md) | 選択中ユーザーの`user_id`を`localStorage`へ読み書きするヘルパー(`loadSavedUserId`/`saveCurrentUserId`)。Issue #552で`App.tsx`から新規抽出。 |
| [errorDetail.md](./src/lib/errorDetail.md) | `apiClient`がスローした例外から表示用文字列を取り出す`extractErrorDetail`と、`/api/quest/data`取得失敗（Zod検証失敗を含む）をバナー向けに要約する`describeGameDataError`を提供する。 |
| [gameDataSchema.md](./src/lib/gameDataSchema.md) | `GET /api/quest/data`のレスポンスをランタイム検証するZodスキーマ`gameDataResponseSchema`と、購入APIレスポンス検証用の`purchaseResponseSchema`（Issue #444）を提供する。 |
| [masterData.md](./src/lib/masterData.md) | サーバー接続エラー発生時のみ使用されるフォールバック用のダミーデータを定義・エクスポートする。 |
| [outOfScopeReload.md](./src/lib/outOfScopeReload.md) | Service Workerのスコープ(`/quest/`)外のページ（`/camera`）向けに、`controllerchange`(Issue #362)の代わりとなる更新検知を提供する`isOutsideServiceWorkerScope`・`createUpdateChecker`を提供する（Issue #591）。 |
| [pathSegments.md](./src/lib/pathSegments.md) | `routing.ts`の`isCameraRoute`と`outOfScopeReload.ts`の`isOutsideServiceWorkerScope`で重複していたパスセグメント分割ロジックを集約した共有ヘルパー`getPathSegments`を提供する（コードレビュー指摘対応、2026-09-10）。 |
| [queryClient.md](./src/lib/queryClient.md) | `@tanstack/react-query`の`QueryClient`を初期化し、システム全体のデータフェッチングのデフォルト動作（再試行回数・キャッシュ期限等）を定義したインスタンスをエクスポートする。 |
| [questTargeting.md](./src/lib/questTargeting.md) | クエストの`target_user`判定（`all`/`siblings`/`role_`プレフィックス/個別`user_id`一致）を行う`isQuestVisibleToUser`を提供する。`QuestList.tsx`と`FamilyDashboard.tsx`で重複していたロジックを集約したもの。 |
| [routing.md](./src/lib/routing.md) | `main.tsx`のルートビュー切り替え判定（`/camera`・`/quest/camera`をカメラビューとして扱うか）を担う純粋関数`isCameraRoute`を提供する（Issue #472）。 |
| [userRole.md](./src/lib/userRole.md) | 保護者判定`isParentUser`と、承認・却下・購入の記録名義となる代表親を解決する`getRepresentativeParent`を提供する。Issue #552で`App.tsx`から新規抽出。 |
| [utils.md](./src/lib/utils.md) | Tailwind CSSのクラス名をマージ（結合・競合解決）するユーティリティ関数`cn`を提供する。 |

## src/types

| 仕様書 | 概要 |
| --- | --- |
| [index.md](./src/types/index.md) | アプリケーション全体で使用される共通の型定義（ユーザー・クエスト・クエスト履歴・報酬・インベントリ等）を提供する。装備・ボス・ギルド依頼・ファミリーマイレージ関連の型は機能廃止に伴い削除済み。 |

## 廃止済み仕様書一覧

以下は対応するソースファイルが削除済みのため、仕様書ファイル自体も削除したもの(Issue #402)。`.github/scripts/check_spec_drift.py` の週次監査で「孤立ドキュメント」として報告され続けるのを避けるため、記録はこの一覧のみに残す。内容が必要な場合は git 履歴(削除コミット以前)を参照すること。機能廃止の経緯は[全体設計書.md](../全体設計書.md)の改訂メモを参照。

| 旧仕様書(旧ソース) | 廃止理由 |
| --- | --- |
| `src/components/ui/BattleEffect.md` (`BattleEffect.tsx`) | ボス機能の廃止に伴い削除(`ffdc8c2`)。クエスト完了（ボス攻撃時）の視覚演出コンポーネント。 |
| `src/components/ui/LevelUpModal.md` (`LevelUpModal.tsx`) | レベルアップ等の通知をトースト通知に統一した改修で削除(`1818d5a`)。 |
| `src/features/admin/components/AdminDashboard.md` (`AdminDashboard.tsx`) | ボスHP調整機能およびファミリーマイレージ設定機能の廃止に伴い削除。 |
| `src/features/family/components/BossCard.md` (`BossCard.tsx`) | ボス機能の廃止に伴い削除。出現中ボスの情報と攻撃ボタンを表示していたコンポーネント。 |
| `src/features/family/components/FamilyMileageCard.md` (`FamilyMileageCard.tsx`) | ファミリーマイレージ機能の廃止に伴い削除。 |
| `src/features/family/components/FamilyParty.md` (`FamilyParty.tsx`) | パーティ機能の廃止に伴い削除。 |
| `src/features/family/components/WeeklyTrends.md` (`WeeklyTrends.tsx`) | 週間ランキング機能の廃止に伴い削除。 |
| `src/features/guild/components/GuildBoard.md` (`GuildBoard.tsx`) | ギルド機能（ギルド討伐依頼板UI）の廃止に伴い削除。 |
| `src/features/shop/components/EquipmentShop.md` (`EquipmentShop.tsx`) | 装備機能（装備購入・装着UI）の廃止に伴い削除。 |
| `src/features/shop/components/ShopContainer.md` (`ShopContainer.tsx`) | デッドコードとして削除済み。「お店」「もちもの」タブ切り替えUIは本コンポーネントに集約されておらず、現在は`App.tsx`が`RewardShop`／`InventoryList`を直接マウントする。 |
| `src/utils/gameHelpers.md` (`gameHelpers.js`) | どこからも参照されない死にコードとして削除(`690c941`)。`getNextLevelExp`はバックエンド`game_logic.py`の`calculate_next_level_exp`と同一式の重複実装だった。 |
