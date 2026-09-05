## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `family-quest/src/components/ui/HlsPlayer.tsx` |
| 言語 | React (TypeScript) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | `288f639` |

## 関連ドキュメント

* [../../features/camera/components/LiveView.md](../../features/camera/components/LiveView.md) - 呼び出し元（ライブ映像表示、`streamUrl`に`/api/cameras/live/{id}/stream.m3u8`を渡す）
* [../../features/camera/components/RecordView.md](../../features/camera/components/RecordView.md) - 呼び出し元（録画再生、`streamUrl`/`startPosition`/`onVideoRef`を渡す）
* [../../../../MY_HOME_SYSTEM/camera_router.md](../../../../MY_HOME_SYSTEM/camera_router.md) - `streamUrl`が指すHLSマニフェスト・セグメント配信エンドポイントの実装元

## 2. ファイルの概要

* `hls.js` ライブラリを用いてHLS（HTTP Live Streaming）形式の映像ストリームを`<video>`要素に再生させる汎用UIコンポーネント。
* `Hls.isSupported()`によりブラウザ対応状況を判定し、対応していれば`hls.js`でストリームを再生、非対応かつSafariのようにネイティブHLS再生に対応するブラウザではネイティブ再生機能にフォールバックする。
* 再生開始位置の指定、自動再生、ミュート、コントロールバー表示可否、外部からの`<video>`要素参照取得、致命的エラー時のメディア回復・エラー表示をサポートする。

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `React`, `useEffect`, `useRef`, `useState` | ライブラリ (`react`) | コンポーネント定義、副作用処理、DOM参照、状態管理 | 根拠: [`import React, { useEffect, useRef, useState } from 'react';`] (行番号: 1 / 抜粋: "import React, { useEffect, useRef, useState } from 'react';") |
| `Hls` | ライブラリ (`hls.js`) | HLSストリームのデコード・再生制御（MSEベース） | 根拠: [`import Hls from 'hls.js';`] (行番号: 2 / 抜粋: "import Hls from 'hls.js';") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `hls.js` (`Hls`クラス) | 本ファイルからは`Hls.isSupported()`、`new Hls(config)`、`loadSource`、`attachMedia`、`on(Hls.Events.ERROR, ...)`、`on(Hls.Events.MANIFEST_PARSED, ...)`、`recoverMediaError`、`destroy`等のAPI呼び出しのみが確認でき、内部のセグメント取得・バッファリング・エラー分類などの実装詳細は読み取れない。`package.json`より バージョンは`^1.5.17`であることが確認できる。 | 根拠: [`Hls.isSupported()`] (行番号: 51 / 抜粋: "if (Hls.isSupported()) {") |
| ブラウザのネイティブHLS再生機構（`video.canPlayType('application/vnd.apple.mpegurl')`） | Safari等が持つネイティブHLS再生の内部実装はブラウザ依存であり、本ファイルからは把握できない。 | 根拠: [`canPlayType`] (行番号: 83 / 抜粋: "} else if (video.canPlayType('application/vnd.apple.mpegurl')) {") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `HlsPlayer`

* **役割**: `streamUrl`で指定されたHLSストリームを`<video>`要素で再生するコンポーネント本体。`hls.js`対応環境では`hls.js`経由、非対応かつネイティブHLS対応環境（Safari等）ではネイティブ`<video src>`再生を行う。致命的な再生エラー時にはオーバーレイでエラーメッセージを表示する。
* 根拠: [`HlsPlayer`] (行番号: 13〜116 / 抜粋: "const HlsPlayer: React.FC<HlsPlayerProps> = ({")


* **引数/リクエスト（Props）**: `HlsPlayerProps`
  * `streamUrl: string` （必須）再生対象のHLSマニフェスト（`.m3u8`）のURL
  * `muted?: boolean` （デフォルト`true`）ミュート再生の可否
  * `autoPlay?: boolean` （デフォルト`true`）自動再生の可否
  * `controls?: boolean` （デフォルト`false`）ブラウザ標準の再生コントロールバー表示可否
  * `startPosition?: number` 再生開始位置（秒）
  * `onVideoRef?: (element: HTMLVideoElement | null) => void` マウント時に`<video>`のDOM要素を呼び出し元へ渡すコールバック
* 根拠: [`HlsPlayerProps`] (行番号: 4〜11 / 抜粋: "interface HlsPlayerProps {")、[デフォルト値] (行番号: 13〜20 / 抜粋: "streamUrl,\n    muted = true,\n    autoPlay = true,\n    controls = false,\n    startPosition,\n    onVideoRef")


* **戻り値/レスポンス**: JSX要素。`<video>`要素と、`streamError`が`true`の場合に重ねて表示されるエラーオーバーレイ`<div>`を含む`<div>`。
* 根拠: [return文] (行番号: 101〜115 / 抜粋: "return (\n        <div className=\"relative w-full h-full\">")


* **副作用**:
  * `useEffect`内で`videoRef.current`（`<video>`要素）を取得し、`onVideoRef`が渡されていれば呼び出す。
  * `Hls.isSupported()`が`true`の場合、`new Hls({ startPosition: ... })`でインスタンスを生成し、`hls.loadSource(streamUrl)`・`hls.attachMedia(video)`でストリームをアタッチし、`Hls.Events.ERROR`・`Hls.Events.MANIFEST_PARSED`・**（Issue #392で追加）** `Hls.Events.FRAG_LOADED`イベントを購読する。`FRAG_LOADED`はセグメント取得成功＝ネットワーク回復の合図として、後述のバックオフ再試行カウンタ`networkRetryCount`を`0`にリセットする。
  * `Hls.isSupported()`が`false`かつ`video.canPlayType('application/vnd.apple.mpegurl')`が真の場合、`video.src = streamUrl`を直接設定し、`loadedmetadata`・`error`のネイティブDOMイベントを購読する。
  * クリーンアップ関数（`useEffect`の戻り値）で、`disposed`フラグを立てて保留中のバックオフ再試行タイマーを`clearTimeout`し、`Hls.isSupported()`であれば`hls?.destroy()`、そうでなければ（ネイティブ再生時のみ）`loadedmetadata`/`error`のリスナーを`removeEventListener`する。**（Issue #392 / F-L4で追加）** 最後に`onVideoRef`が渡されていれば`onVideoRef(null)`を呼び、アンマウント・`streamUrl`差し替え時に呼び出し元（`RecordView.tsx`の`videoRefs`等）へ切り離しを通知する（以前は呼ばれておらず、切離済み要素への参照が残っていた）。
* 根拠: [`useEffect`] (行番号: 27〜101 / 抜粋: "useEffect(() => {\n        const video = videoRef.current;")、[cleanup] (行番号: 133〜150 / 抜粋: "return () => {\n            disposed = true;\n            if (retryTimer !== null) {\n                window.clearTimeout(retryTimer);\n                retryTimer = null;\n            }\n            if (Hls.isSupported()) {\n                hls?.destroy();\n            } else {", "if (onVideoRef) onVideoRef(null);")


* **エラーハンドリング**: `Hls.Events.ERROR`イベントで`data.fatal`が`true`の場合、`console.error`でログ出力した上で種別ごとに分岐する。**（Issue #392で修正）** `data.type === Hls.ErrorTypes.NETWORK_ERROR`（`camera_router.py`がffmpeg起動待ちで返す503応答やサーバー再起動中の接続失敗などで発生しうる）は`scheduleNetworkRetry`により`1000 * 2^n`ms（`n`は再試行回数、`NETWORK_RETRY_MAX_MS`＝30秒で頭打ち）のバックオフで`hls.startLoad()`を再試行し、`NETWORK_RETRY_MAX_ATTEMPTS`（6回）を超えたら`giveUp`（`hls.destroy()`＋`setStreamError(true)`）する。`data.type === Hls.ErrorTypes.MEDIA_ERROR`であれば直近3秒以内に回復を試みていない場合のみ`hls.recoverMediaError()`で回復を試行し（3秒以内の連続エラーは`giveUp`）、それ以外の致命的エラー種別では即座に`giveUp`する。以前はNETWORK_ERRORも即`hls.destroy()`していたため、ライブ4分割を常時表示している端末では一時的な503やサーバー再起動でタイルが再マウントまで永久に死んでいた。Safariのネイティブ再生時は`error`イベントで`handleNativeError`が呼ばれ`console.error`＋`setStreamError(true)`する。**（Issue #443で修正）** `video.play()`失敗時（Promiseのreject、`handleLoadedMetadata`内・`MANIFEST_PARSED`ハンドラ内の計2箇所）は、以前は`.catch(e => console.error("Play failed:", e))`でログ出力するのみで`streamError`状態を更新していなかったが、現在はいずれも`catch`コールバック内で`console.error`に加え`setStreamError(true)`を呼び、無音の一時停止画面のままにせずエラーオーバーレイを表示するようになった。**（Issue #443で追加）** さらに、`Hls.isSupported()`が偽かつ`video.canPlayType('application/vnd.apple.mpegurl')`も偽（hls.js非対応・ネイティブHLS再生も非対応）の場合、以前はどちらの`if`/`else if`分岐にも入らず`hls`変数が未初期化のまま何も起きない（映像もエラー表示も出ない）サイレント失敗になっていたが、現在は新設された`else`分岐で`console.error`のログ出力と`setStreamError(true)`を行い、明示的にエラーオーバーレイを表示する。`streamError`が`true`の間は「映像を取得できませんでした」というオーバーレイに加え、**（Issue #392で追加）** 押下で`retryNonce`をインクリメントして`useEffect`を再実行させ（＝HLSインスタンスを再生成する）「再試行」ボタンを表示する。ボタンの`onClick`は`e.stopPropagation()`で、`LiveView.tsx`のタイル全体に付いた「1台拡大表示」の`onClick`への伝播を止める。
* 根拠: [`Hls.Events.ERROR`とNETWORK_ERROR分岐] (行番号: 105〜124 / 抜粋: "instance.on(Hls.Events.ERROR, (_event, data) => {\n                if (!data.fatal) return;\n                console.error(\"HLS Fatal Error:\", data);\n                if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {\n                    scheduleNetworkRetry();")、[`scheduleNetworkRetry`/`giveUp`] (行番号: 19〜21, 74〜96 / 抜粋: "const NETWORK_RETRY_BASE_MS = 1000;\nconst NETWORK_RETRY_MAX_MS = 30 * 1000;\nconst NETWORK_RETRY_MAX_ATTEMPTS = 6;", "const giveUp = () => {\n            hls?.destroy();\n            hls = undefined;\n            setStreamError(true);\n        };", "const scheduleNetworkRetry = () => {\n            if (networkRetryCount >= NETWORK_RETRY_MAX_ATTEMPTS) {")、[`video.play()`失敗時の`setStreamError(true)`追加] (行番号: 64〜71, 132〜138 / 抜粋: "// #443: 以前はvideo.play()の失敗(自動再生ポリシー等)がconsole.errorのみで\n                // UI状態に反映されず、ユーザーには無音の一時停止画面が残っていた。\n                video.play().catch(e => {\n                    console.error(\"Play failed:\", e);\n                    setStreamError(true);\n                });")、[非対応ブラウザ向け`else`分岐の追加] (行番号: 144〜150 / 抜粋: "} else {\n            // #443: hls.js非対応かつブラウザのネイティブHLS再生にも非対応の場合、\n            // 以前はどちらの分岐にも入らず、hls変数が未初期化のまま何も起きない\n            // (映像もエラー表示も出ない)画面になっていた。明示的にエラー表示を出す。\n            console.error(\"HLS is not supported by hls.js and native HLS playback is unavailable in this browser.\");\n            setStreamError(true);\n        }")、[再試行ボタン] (行番号: 172〜192 / 抜粋: "{streamError && (\n                <div className=\"absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/80 text-white text-sm text-center p-4\">", "onClick={(e) => { e.stopPropagation(); setRetryNonce(n => n + 1); }}")

## 5. 処理フロー図

```mermaid
flowchart TD
    Start(["Mount / streamUrl・autoPlay・startPosition・onVideoRef変更時"]) --> GetVideo{"videoRef.current が存在するか"}
    GetVideo -- No --> End(["終了 (useEffect早期return)"])
    GetVideo -- Yes --> CallOnVideoRef{"onVideoRef が渡されているか"}
    CallOnVideoRef -- Yes --> InvokeOnVideoRef["onVideoRef(video) 呼び出し"] --> ResetError
    CallOnVideoRef -- No --> ResetError["setStreamError(false)"]
    ResetError --> CheckSupport{"Hls.isSupported() ?"}

    CheckSupport -- Yes --> CreateHls["new Hls({ startPosition }) を生成"]
    CreateHls --> LoadAttach["hls.loadSource(streamUrl); hls.attachMedia(video)"]
    LoadAttach --> SubscribeErr["hls.on(Hls.Events.ERROR, ...) を購読"]
    SubscribeErr --> SubscribeParsed["hls.on(Hls.Events.MANIFEST_PARSED, ...) を購読"]
    SubscribeParsed --> WaitEventsHls["イベント待機"]

    WaitEventsHls -- "ERROR (fatal)" --> CheckType{"data.type === MEDIA_ERROR ?"}
    CheckType -- Yes --> CheckInterval{"直近3秒以内に\n回復試行済みか"}
    CheckInterval -- No --> RecoverMedia["hls.recoverMediaError()"]
    CheckInterval -- Yes --> DestroyOnRepeat["hls.destroy(); setStreamError(true)"]
    CheckType -- No --> DestroyOther["hls.destroy(); setStreamError(true)"]
    WaitEventsHls -- "MANIFEST_PARSED" --> AutoPlayCheckHls{"autoPlay ?"}
    AutoPlayCheckHls -- Yes --> PlayHls["video.play()"]
    PlayHls -- "reject (#443)" --> PlayFailHls["console.error(...); setStreamError(true)"]
    AutoPlayCheckHls -- No --> WaitEventsHls

    CheckSupport -- No --> CheckNative{"video.canPlayType\n('application/vnd.apple.mpegurl') ?"}
    CheckNative -- Yes --> SetSrc["video.src = streamUrl"]
    SetSrc --> AddListeners["addEventListener('loadedmetadata', handleLoadedMetadata)\naddEventListener('error', handleNativeError)"]
    AddListeners --> WaitEventsNative["イベント待機"]
    WaitEventsNative -- "loadedmetadata" --> SeekAndPlay["startPositionがあればcurrentTime設定 → autoPlayならvideo.play()"]
    SeekAndPlay -- "reject (#443)" --> PlayFailNative["console.error(...); setStreamError(true)"]
    WaitEventsNative -- "error" --> NativeErr["console.error(...); setStreamError(true)"]
    CheckNative -- No --> UnsupportedBoth["#443: hls.js非対応かつネイティブ非対応\nconsole.error(...); setStreamError(true) (hls未生成)"]

    RecoverMedia --> Cleanup["Unmount / 依存値変更時のクリーンアップ"]
    DestroyOnRepeat --> Cleanup
    DestroyOther --> Cleanup
    PlayHls --> Cleanup
    PlayFailHls --> Cleanup
    SeekAndPlay --> Cleanup
    PlayFailNative --> Cleanup
    NativeErr --> Cleanup
    UnsupportedBoth --> Cleanup

    Cleanup --> HlsExists{"hls変数が存在するか"}
    HlsExists -- Yes --> DestroyCleanup["hls.destroy()"]
    HlsExists -- No --> RemoveListeners["removeEventListener('loadedmetadata', ...)\nremoveEventListener('error', ...)"]
    DestroyCleanup --> End2(["終了"])
    RemoveListeners --> End2

```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "HlsPlayer.tsx"
        Component_HlsPlayer["HlsPlayer (Component)"]
        Props["HlsPlayerProps"]
        State_StreamError["streamError (useState)"]
        Handlers["handleNativeError / handleLoadedMetadata"]
    end

    subgraph "外部ライブラリ"
        React["react (useEffect/useRef/useState)"]
        HlsJs["hls.js (Hls)"]
    end

    subgraph "DOM / ブラウザAPI"
        VideoElement["HTMLVideoElement"]
        NativeHlsSupport["video.canPlayType('application/vnd.apple.mpegurl')"]
    end

    Component_HlsPlayer --> React
    Component_HlsPlayer --> HlsJs
    Component_HlsPlayer --> Props
    Component_HlsPlayer --> State_StreamError
    Component_HlsPlayer --> Handlers
    Component_HlsPlayer --> VideoElement
    HlsJs --> VideoElement
    Handlers --> NativeHlsSupport

```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `family-quest/src/features/camera/components/LiveView.tsx`, `RecordView.tsx` | `HlsPlayer`の呼び出し元であり、`streamUrl`・`startPosition`・`onVideoRef`等がどう組み立てられ、実際のストリームURLとして渡されるかを確認する必要がある。 | 根拠: [`HlsPlayerProps`] (行番号: 4〜11 / 抜粋: "interface HlsPlayerProps {") |
| 中 | `hls.js` ライブラリ本体（`node_modules/hls.js`） | `Hls`クラスの内部実装（エラー分類の詳細、`recoverMediaError`の具体的な回復挙動、`MANIFEST_PARSED`以外のイベント種別など）を確認するため。`package.json`記載のバージョンは`^1.5.17`。 | 根拠: [`Hls.isSupported()`] (行番号: 51 / 抜粋: "if (Hls.isSupported()) {") |
| 低 | バックエンドの`/api/cameras/live/{id}/stream.m3u8`・`/api/cameras/record/...`エンドポイント実装 | `streamUrl`として渡されるHLSマニフェストの生成方式（セグメント長、有効期限等）を確認するため。 | 根拠: [呼び出し元での`streamUrl`組み立て] 本ファイル単体では確認できない |

## 8. 保守上の注意点

* **[修正済み] NETWORK_ERRORのバックオフ再試行と再試行ボタン（Issue #392）**: 以前は`MEDIA_ERROR`以外の`fatal`エラーを即`hls.destroy()`しており、`camera_router.py`がffmpeg起動待ちで返しうる503応答やサーバー再起動中の一時的な接続断でも、ライブ4分割を常時表示している端末ではタイルが再マウントまで永久に死んでいた。修正後は`NETWORK_ERROR`のみ指数バックオフ（1s→2s→4s→8s→16s→30s、最大6回）で`hls.startLoad()`を再試行し、`Hls.Events.FRAG_LOADED`（セグメント取得成功）で再試行回数をリセットする。再試行上限を超えた場合とその他の致命的エラーは従来通り`hls.destroy()`＋エラー表示だが、オーバーレイに「再試行」ボタンを追加し、押下で`retryNonce`ステートをインクリメントして`useEffect`を再実行させる（＝HLSインスタンスを一から作り直す）ことでユーザー操作による復帰手段を用意した。
* 根拠: (行番号: 15〜21, 67〜122, 161〜172)
* **[修正済み] アンマウント時に`onVideoRef(null)`を呼ぶ（Issue #392 / F-L4）**: 以前はクリーンアップ関数が`onVideoRef`を一切呼んでおらず、`RecordView.tsx`の`videoRefs`（カメラIDごとの`<video>`参照マップ）に、アンマウント済み・差し替え済みの要素への参照が残り続けていた。修正後はクリーンアップの最後で`onVideoRef?.(null)`を呼ぶ。
* 根拠: (行番号: 149 / 抜粋: "if (onVideoRef) onVideoRef(null);")
* **契約テスト**: `HlsPlayer.retry.test.tsx`が`hls.js`を`vi.mock`でモックし、NETWORK_ERRORのバックオフ・`FRAG_LOADED`によるリセット・再試行上限超過後のボタン表示とクリック時のインスタンス再生成・非NETWORK/MEDIA致命的エラーの即時失敗・`onVideoRef`のマウント/アンマウント呼び出し・アンマウント後に保留中の再試行タイマーが発火しないことを検証する（既存の`HlsPlayer.test.tsx`はSafariネイティブ再生パスのリスナー着脱を実`hls.js`で検証しており対象が異なる）。

* **[修正済み] `hls.js`非対応かつネイティブHLS非対応ブラウザでのサイレント失敗（Issue #443）**: 以前は`video.canPlayType`が偽を返すブラウザの場合、`if (Hls.isSupported())`と`else if (video.canPlayType(...))`のいずれの分岐にも入らず、`hls`変数は未初期化（`let hls: Hls | undefined;`のまま代入されない）のまま、ユーザーには何の映像もエラー表示も出ない「サイレント失敗」状態になっていた。現在は新設された`else`分岐で`console.error`のログ出力と`setStreamError(true)`を行い、明示的にエラーオーバーレイを表示する。クリーンアップ時、この経路では元々`hls`が未設定・リスナーも未登録のため、`removeEventListener`が空振りする点自体は変わらない（実害はない）。
* 根拠: [`let hls: Hls | undefined;`] (行番号: 46 / 抜粋: "let hls: Hls | undefined;")、[新設の`else`分岐] (行番号: 144〜150 / 抜粋: "} else {\n            // #443: hls.js非対応かつブラウザのネイティブHLS再生にも非対応の場合、\n            // 以前はどちらの分岐にも入らず、hls変数が未初期化のまま何も起きない\n            // (映像もエラー表示も出ない)画面になっていた。明示的にエラー表示を出す。\n            console.error(\"HLS is not supported by hls.js and native HLS playback is unavailable in this browser.\");\n            setStreamError(true);\n        }")
* `recoverDecodingErrorDate`による3秒間隔の連続エラー抑制ロジックは、コメント上「無限ループ防止」を目的としているが、`MEDIA_ERROR`以外の`fatal`エラー（`NETWORK_ERROR`等）に対しては即座に`hls.destroy()`されるのみで、リトライは行われない。
* 根拠: [`if (data.type === Hls.ErrorTypes.MEDIA_ERROR)`] (行番号: 110 / 抜粋: "if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {")
* **[修正済み] `video.play()`失敗時に`streamError`が更新されなかった問題（Issue #443）**: 以前は自動再生ポリシー等による`video.play()`の失敗（Promiseのreject）が`console.error`でログ出力されるのみで`streamError`状態には反映されず、ユーザー向けのエラーオーバーレイは表示されずに無音の一時停止画面のまま残る可能性があった。現在は`handleLoadedMetadata`内・`MANIFEST_PARSED`ハンドラ内の両方の`catch`コールバックで`setStreamError(true)`も呼ぶよう修正された。
* 根拠: (行番号: 64〜71, 132〜138 / 抜粋: "video.play().catch(e => {\n                    console.error(\"Play failed:\", e);\n                    setStreamError(true);\n                });")
* `useEffect`の依存配列に`onVideoRef`が含まれているため、呼び出し元が毎レンダーで新規のインライン関数を渡した場合、`streamUrl`が変わっていなくても`useEffect`が再実行され、HLSのセットアップ（`new Hls`・`loadSource`・`attachMedia`）がやり直される可能性がある（呼び出し元の`RecordView.tsx`にはこれを回避するためのコメントが存在する）。
* 根拠: [依存配列] (行番号: 170 / 抜粋: "}, [streamUrl, autoPlay, startPosition, onVideoRef, retryNonce]);")

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `hls.js`（`Hls`クラス）の内部実装詳細 | 本ファイルからは呼び出しているAPI（`isSupported`, `loadSource`, `attachMedia`, `recoverMediaError`, `destroy`等）のみが確認でき、内部ロジックは不明なため（リポジトリ内を`node_modules/hls.js`で検索したが、`node_modules`は`.gitignore`規則により追跡対象外で実体は存在せず、解消不可。`package.json`上のバージョン指定`^1.5.17`のみ確認できる） | `hls.js`ライブラリ本体のソース、または`node_modules/hls.js/package.json` |
| `streamUrl`として渡される実際のHLSマニフェストの仕様（セグメント長、更新頻度など） | 本ファイルはURLを受け取って再生するのみで、マニフェストの生成側の情報を持たないため | バックエンドの`/api/cameras/live/...`・`/api/cameras/record/...`エンドポイント実装 |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `streamUrl`として渡される実際のHLSマニフェストの仕様（セグメント長、更新頻度など） | `MY_HOME_SYSTEM/services/camera_service.py`を直接確認した。ライブ配信の`start_hls_stream`（84〜119行目）は`ffmpeg`を`-hls_time 2`（セグメント長2秒）・`-hls_list_size 5`（プレイリストに保持するセグメント数5件）・`-hls_flags delete_segments`（再生済みセグメントを自動削除）で起動し、`stream.m3u8`へ出力する。録画再生用の`generate_record_playlist`（143〜242行目）は10分単位のmp4ファイル群を`ffconcat`で結合したうえで`-hls_time 4`・`-hls_playlist_type vod`のVOD形式で`record_{target_date}.m3u8`を生成し、当日分は毎回再生成、過去日付分は既存ファイルをキャッシュとして返す（184〜189行目）設計であることを確認した。またルーティング側の`MY_HOME_SYSTEM/routers/camera_router.py`も直接確認し、`GET /live/{camera_id}/stream.m3u8`（42〜60行目）と`GET /record/{camera_id}/{target_date}/{filename}`（72〜101行目、拡張子で`.m3u8`生成と`.ts`セグメント配信を分岐）の実体を確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/services/camera_service.py:84-119, 143-242`, `MY_HOME_SYSTEM/routers/camera_router.py:42-101` |
| `hls.js`（`Hls`クラス）の内部実装詳細 | リポジトリ内を`node_modules/hls.js`で検索したが、`family-quest/.gitignore`10行目・リポジトリルート`.gitignore`6行目の`node_modules`規則により依存パッケージの実体はリポジトリに存在せず、解消不可であることを確認した。`family-quest/package.json`にはバージョン`"hls.js": "^1.5.17"`という指定のみが確認できる。 | 直接ソース確認: `family-quest/package.json`（`hls.js`バージョン指定行）、`family-quest/.gitignore:10`（`node_modules`の追跡除外を確認、`node_modules/hls.js`自体はリポジトリ内に存在せず） |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
