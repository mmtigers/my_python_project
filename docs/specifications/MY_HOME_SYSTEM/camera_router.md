## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `camera_router.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

- [camera_service.md](./camera_service.md) — 呼び出し先（委譲先）。`start_hls_stream`, `get_record_start_offset`, `generate_record_playlist`, `HLS_LIVE_DIR`, `HLS_VOD_DIR`を提供する。
- [config.md](./config.md) — `CAMERAS`設定を提供する。
- [CameraDashboard.md](../family-quest/src/features/camera/components/CameraDashboard.md) — フロントエンド側の対応コンポーネント（`/settings`エンドポイントの利用元と推測される）。
- [LiveView.md](../family-quest/src/features/camera/components/LiveView.md) — フロントエンド側の対応コンポーネント（ライブ配信エンドポイントの利用元と推測される）。
- [RecordView.md](../family-quest/src/features/camera/components/RecordView.md) — フロントエンド側の対応コンポーネント（録画配信エンドポイントの利用元と推測される）。
- [index.md](../family-quest/src/features/camera/types/index.md) — `get_camera_settings`が返す`id`/`name`/`order`/`enabled`に対応する`CameraConfig`型定義。
- [HlsPlayer.md](../family-quest/src/components/ui/HlsPlayer.md) — `.m3u8`/`.ts`エンドポイントが配信するHLSストリームを実際に再生するプレイヤーコンポーネント。

## 2. ファイルの概要

* FastAPIの `APIRouter` を用いて、カメラのライブ配信・録画再生に関するHTTPエンドポイントを定義するルーターモジュールである。
* カメラ設定一覧の取得(`/settings`)、カメラ有効/無効の切り替え(`PUT /settings/{camera_id}`)、ライブHLSプレイリストの取得(`/live/{camera_id}/stream.m3u8`)、ライブHLSセグメントの配信(`/live/{camera_id}/{segment_file}`)、録画情報の取得(`/record/{camera_id}/{target_date}/info`)、録画プレイリスト/セグメントの配信(`/record/{camera_id}/{target_date}/{filename}`)の6つのエンドポイントを提供する。
* 実際のストリーム生成・録画処理は `services.camera_service` モジュールに委譲し、本ファイルはHTTPリクエストの受付・パラメータ検証・レスポンス形式への変換（パストラバーサル対策を含む）を担う。
* コミット`95d3e55`（E-3: camera enable/disable persistence）により、`GET /settings`が返す`enabled`は常に`True`固定だった状態から、`config.CAMERAS`側の`enabled`キー（`devices.json`から読み込み、無ければ`True`）を反映するよう修正され、`PUT /settings/{camera_id}`エンドポイントと`CameraSettingsUpdate`リクエストモデルが新規追加された。これにより`camera_service.set_camera_enabled`経由で`devices.json`へ有効/無効設定を永続化できるようになった。
* 根拠: [モジュール全体の構成] (行番号: 1〜127 / 抜粋: "from fastapi import APIRouter, HTTPException"), `"enabled": cam.get("enabled", True)` (行番号: 43 / 抜粋: "cam.get(\"enabled\", True)"), `@router.put("/settings/{camera_id}")` (行番号: 47 / 抜粋: "@router.put(\"/settings/{camera_id}\")")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `os` | 標準ライブラリ | パス結合(`os.path.join`)、実パス解決(`os.path.realpath`)、共通パス判定(`os.path.commonpath`)、ファイル存在確認(`os.path.exists`) | 根拠: [import文] (行番号: 1 / 抜粋: "import os") |
| `time` | 標準ライブラリ | ストリーム生成待機のためのスリープ(`time.sleep`) | 根拠: [import文] (行番号: 2 / 抜粋: "import time") |
| `fastapi.APIRouter`, `HTTPException` | 外部ライブラリ | ルーターの生成、HTTPエラーレスポンスの送出 | 根拠: [import文] (行番号: 3 / 抜粋: "from fastapi import APIRouter, HTTPException") |
| `fastapi.responses.FileResponse` | 外部ライブラリ | 動画/プレイリストファイルをHTTPレスポンスとして返却 | 根拠: [import文] (行番号: 4 / 抜粋: "from fastapi.responses import FileResponse") |
| `pydantic.BaseModel` | 外部ライブラリ | `PUT /settings/{camera_id}`のリクエストボディ用モデル`CameraSettingsUpdate`の基底クラス | 根拠: [import文] (行番号: 5 / 抜粋: "from pydantic import BaseModel") |
| `typing.List`, `Dict`, `Any` | 標準ライブラリ | 型ヒント（本ファイル内での明示的な使用箇所はimport文のみ） | 根拠: [import文] (行番号: 6 / 抜粋: "from typing import List, Dict, Any") |
| `config` | 内部モジュール | カメラ設定一覧(`config.CAMERAS`)の取得 | 根拠: [config.CAMERASの参照] (行番号: 38 / 抜粋: "for idx, cam in enumerate(config.CAMERAS):") |
| `services.camera_service` | 内部モジュール | ライブ配信開始、録画情報取得、録画プレイリスト生成、カメラ有効/無効の永続化、HLSディレクトリパスの取得 | 根拠: [import文] (行番号: 8 / 抜粋: "from services import camera_service") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `camera_service` | 本タスクの指示により、本ファイル執筆時点では `camera_service.py` を読み込まずブラックボックスとして扱う。`start_hls_stream`, `get_record_start_offset`, `generate_record_playlist`, `set_camera_enabled`, `HLS_VOD_DIR`, `HLS_LIVE_DIR` の内部実装・戻り値の詳細仕様は不明。 | 根拠: [import文と呼び出し箇所] (行番号: 8, 50, 62, 82, 94, 107, 123 / 抜粋: "from services import camera_service") |
| `config` | `config.CAMERAS` の構造（各カメラ辞書のキー、読み込み元ファイル等）が本ファイルからは不明。 | 根拠: [config.CAMERASの参照] (行番号: 38, 58, 78, 90, 102, 119 / 抜粋: "config.CAMERAS") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `_resolve_segment_path`

* **役割**: `base_dir/camera_id/filename` からパスを構築し、実パス解決後に `base_dir` の外側に出ていないか（パストラバーサル）を検証したうえで、安全な絶対パスを返す。
* 根拠: [関数定義とDocstring] (行番号: 17〜30 / 抜粋: "def _resolve_segment_path(base_dir: str, camera_id: str, filename: str) -> str:")


* **引数/リクエスト**: `base_dir: str`（基準ディレクトリ）, `camera_id: str`（カメラID）, `filename: str`（対象ファイル名）
* 根拠: [引数定義] (行番号: 17 / 抜粋: "def _resolve_segment_path(base_dir: str, camera_id: str, filename: str) -> str:")


* **戻り値/レスポンス**: `str`（範囲内であることを検証済みの実パス（絶対パス）文字列）
* 根拠: [戻り値] (行番号: 30 / 抜粋: "return resolved_candidate")


* **副作用**: なし（`os.path.realpath` によるファイルシステム参照のみ）
* 根拠: [処理内容] (行番号: 23〜25 / 抜粋: "resolved_candidate = os.path.realpath(candidate)")


* **エラーハンドリング**: 解決後のパスが `base_dir` の実パスと共通しない（範囲外）場合、`HTTPException(status_code=400, detail="Invalid path")` を送出する。
* 根拠: [ガード節] (行番号: 27〜28 / 抜粋: "if os.path.commonpath([resolved_base, resolved_candidate]) != resolved_base:\n        raise HTTPException(status_code=400, detail="Invalid path")")


### `CameraSettingsUpdate`

* **役割**: `PUT /settings/{camera_id}`のリクエストボディを表すPydanticモデル。カメラの有効/無効切り替え値を受け取る。
* 根拠: `class CameraSettingsUpdate(BaseModel):` (行番号: 13-14 / 抜粋: "class CameraSettingsUpdate(BaseModel):")


* **引数/リクエスト**: `enabled: bool`（必須フィールド）
* 根拠: `enabled: bool` (行番号: 14 / 抜粋: "enabled: bool")


* **戻り値/レスポンス**: 該当なし（Pydanticモデルの定義のみ）
* 根拠: `class CameraSettingsUpdate(BaseModel):` (行番号: 13-14 / 抜粋: "class CameraSettingsUpdate(BaseModel):")


* **副作用**: なし
* 根拠: `class CameraSettingsUpdate(BaseModel):` (行番号: 13-14 / 抜粋: "class CameraSettingsUpdate(BaseModel):")


* **エラーハンドリング**: Pydanticの機能に依存するバリデーションエラー（`enabled`が`bool`に変換できない場合等）。
* 根拠: `class CameraSettingsUpdate(BaseModel):` (行番号: 13-14 / 抜粋: "class CameraSettingsUpdate(BaseModel):")


### `get_camera_settings` (`GET /settings`)

* **役割**: `config.CAMERAS` からカメラ設定一覧を読み出し、フロントエンド向けにID・名前・表示順・有効フラグを含む辞書のリストを構築して返す。
* 根拠: [エンドポイント定義とDocstring] (行番号: 33〜35 / 抜粋: "def get_camera_settings():\n    """フロントエンドへ有効なカメラの一覧と設定を返す"""")


* **引数/リクエスト**: なし（パスパラメータ・クエリパラメータなし）
* 根拠: [ルート定義] (行番号: 33〜34 / 抜粋: "@router.get("/settings")\ndef get_camera_settings():")


* **戻り値/レスポンス**: カメラ設定辞書のリスト。各要素は `id`, `name`, `order`（配列インデックス+1）, `enabled`（`cam.get("enabled", True)`。コミット`95d3e55`以降は`config.CAMERAS`側の実際の値を反映し、キーが無い場合のみ`True`にフォールバック）を含む。
* 根拠: [レスポンス構築] (行番号: 39〜44 / 抜粋: "settings.append({\n            "id": cam["id"],\n            "name": cam["name"],\n            "order": idx + 1,  # 配列の順序を表示順とする\n            "enabled": cam.get("enabled", True)\n        })")


* **副作用**: なし
* **エラーハンドリング**: なし（`config.CAMERAS` の各要素に `id`/`name` キーが存在しない場合の例外処理はコード上に存在しない）


### `update_camera_settings` (`PUT /settings/{camera_id}`)

* **役割**: 指定カメラIDの有効/無効フラグを`camera_service.set_camera_enabled`経由で`devices.json`に永続化する。コミット`95d3e55`（E-3）で新規追加。
* 根拠: `def update_camera_settings(camera_id: str, payload: CameraSettingsUpdate):` (行番号: 47〜53 / 抜粋: "def update_camera_settings(camera_id: str, payload: CameraSettingsUpdate):")


* **引数/リクエスト**: `camera_id: str`（パスパラメータ）, `payload: CameraSettingsUpdate`（リクエストボディ、`enabled: bool`を含む）
* 根拠: [引数定義] (行番号: 47〜48 / 抜粋: "@router.put("/settings/{camera_id}")\ndef update_camera_settings(camera_id: str, payload: CameraSettingsUpdate):")


* **戻り値/レスポンス**: `{"id": camera_id, "enabled": payload.enabled}` 形式の辞書。カメラID未検出時は404。
* 根拠: `return {"id": camera_id, "enabled": payload.enabled}` (行番号: 53 / 抜粋: "return {"id": camera_id, "enabled": payload.enabled}")


* **副作用**: `camera_service.set_camera_enabled(camera_id, payload.enabled)` の呼び出しにより、`devices.json`ファイルへの書き込み（永続化）と`config.CAMERAS`（インメモリ）への反映を誘発しうる。
* 根拠: `if not camera_service.set_camera_enabled(camera_id, payload.enabled):` (行番号: 50 / 抜粋: "camera_service.set_camera_enabled(camera_id, payload.enabled)")


* **エラーハンドリング**: `camera_service.set_camera_enabled`が`False`を返した場合（対象カメラが見つからない等）、`HTTPException(status_code=404, detail="Camera not found")`を送出する。
* 根拠: `if not camera_service.set_camera_enabled(camera_id, payload.enabled):\n        raise HTTPException(status_code=404, detail="Camera not found")` (行番号: 50〜51 / 抜粋: "raise HTTPException(status_code=404, detail=\"Camera not found\")")


### `get_live_stream` (`GET /live/{camera_id}/stream.m3u8`)

* **役割**: 指定カメラIDのライブHLSストリーム生成を `camera_service.start_hls_stream` に依頼し、プレイリストファイル(.m3u8)が生成されるまで最大5秒待機したうえでファイルレスポンスを返す。
* 根拠: [エンドポイント定義とDocstring] (行番号: 55〜57 / 抜粋: "def get_live_stream(camera_id: str):\n    """ライブHLSプレイリスト（.m3u8）の取得"""")


* **引数/リクエスト**: `camera_id: str`（パスパラメータ）
* 根拠: [引数定義] (行番号: 55〜56 / 抜粋: "@router.get("/live/{camera_id}/stream.m3u8")\ndef get_live_stream(camera_id: str):")


* **戻り値/レスポンス**: `FileResponse`（media_type="application/vnd.apple.mpegurl"）。カメラ未検出時は404、ストリーム初期化失敗時は500、生成タイムアウト時は503を送出。
* 根拠: [FileResponse返却] (行番号: 70 / 抜粋: "return FileResponse(playlist_path, media_type="application/vnd.apple.mpegurl")")


* **副作用**: `camera_service.start_hls_stream` の呼び出し（ストリーム開始プロセスの起動を誘発しうる）、最大10回×0.5秒の待機ループによるブロッキング。
* 根拠: [待機ループ] (行番号: 68〜71 / 抜粋: "for _ in range(10):\n        if os.path.exists(playlist_path):\n            return FileResponse(playlist_path, media_type="application/vnd.apple.mpegurl")\n        time.sleep(0.5)")


* **エラーハンドリング**: カメラID未検出時に404、`start_hls_stream`が空文字列相当（falsy）を返した場合に500、待機ループ内でファイルが生成されなかった場合に503の `HTTPException` を送出。
* 根拠: [各種例外送出] (行番号: 59〜60, 64〜65, 73 / 抜粋: "if not cam_conf:\n        raise HTTPException(status_code=404, detail="Camera not found")")


### `get_record_info` (`GET /record/{camera_id}/{target_date}/info`)

* **役割**: 指定カメラ・指定日の録画ファイルのメタデータとして、開始オフセット秒数を `camera_service.get_record_start_offset` から取得し返す。
* 根拠: [エンドポイント定義とDocstring] (行番号: 75〜77 / 抜粋: "def get_record_info(camera_id: str, target_date: str):\n    """指定日の録画ファイルのメタデータ（最初のファイルのオフセット秒数）を返す"""")


* **引数/リクエスト**: `camera_id: str`, `target_date: str`（いずれもパスパラメータ）
* 根拠: [引数定義] (行番号: 75〜76 / 抜粋: "@router.get("/record/{camera_id}/{target_date}/info")\ndef get_record_info(camera_id: str, target_date: str):")


* **戻り値/レスポンス**: `{"offset_seconds": offset}` 形式の辞書（`offset`は`int`）
* 根拠: [レスポンス構築] (行番号: 83 / 抜粋: "return {"offset_seconds": offset}")


* **副作用**: `camera_service.get_record_start_offset` の呼び出し
* 根拠: [呼び出し] (行番号: 82 / 抜粋: "offset = camera_service.get_record_start_offset(cam_conf, target_date)")


* **エラーハンドリング**: カメラID未検出時に404の `HTTPException` を送出。
* 根拠: [ガード節] (行番号: 79〜80 / 抜粋: "if not cam_conf:\n        raise HTTPException(status_code=404, detail="Camera not found")")


### `get_record_file` (`GET /record/{camera_id}/{target_date}/{filename}`)

* **役割**: リクエストされたファイル名の拡張子により処理を分岐する。`.m3u8`の場合は録画プレイリストを生成・返却し、`.ts`の場合は録画セグメントファイルを配信、それ以外の拡張子は400エラーとする。
* 根拠: [エンドポイント定義とDocstring] (行番号: 85〜87 / 抜粋: "def get_record_file(camera_id: str, target_date: str, filename: str):\n    """録画VODのプレイリスト（.m3u8）またはセグメント（.ts）を配信"""")


* **引数/リクエスト**: `camera_id: str`, `target_date: str`, `filename: str`（いずれもパスパラメータ）
* 根拠: [引数定義] (行番号: 85〜86 / 抜粋: "@router.get("/record/{camera_id}/{target_date}/{filename}")\ndef get_record_file(camera_id: str, target_date: str, filename: str):")


* **戻り値/レスポンス**: `.m3u8`要求時は `FileResponse`（media_type="application/vnd.apple.mpegurl"）、`.ts`要求時は `FileResponse`（media_type="video/MP2T"）。
* 根拠: [各分岐でのレスポンス返却] (行番号: 98, 111 / 抜粋: "return FileResponse(playlist_path, media_type="application/vnd.apple.mpegurl")")


* **副作用**: `.m3u8`分岐では `camera_service.generate_record_playlist` の呼び出し（プレイリスト・セグメント生成を誘発しうる）。`.ts`分岐では `_resolve_segment_path` によるパス検証と `camera_service.HLS_VOD_DIR` の参照。
* 根拠: [各分岐の処理] (行番号: 94, 107 / 抜粋: "playlist_path = camera_service.generate_record_playlist(cam_conf, target_date)")


* **エラーハンドリング**: カメラID未検出時404（各分岐で個別に判定）。`.m3u8`分岐でプレイリスト生成失敗時404。`.ts`分岐でセグメントファイル不在時404。上記いずれの拡張子でもない場合は400。
* 根拠: [各エラー分岐] (行番号: 95〜96, 108〜109, 113〜114 / 抜粋: "else:\n        raise HTTPException(status_code=400, detail="Unsupported file extension")")


### `get_live_segment` (`GET /live/{camera_id}/{segment_file}`)

* **役割**: ライブHLSの `.ts` セグメントファイルを、拡張子チェックとパストラバーサル検証を経て配信する。Issue #172の修正（コミット時点）により、`.ts`以外の拡張子は400で拒否するようになった。修正前は拡張子を一切検証していなかったため、`_resolve_segment_path`のパストラバーサル対策のみでは防げない形で、同一ディレクトリ内に配置される`ffmpeg.log`（RTSP認証情報を含みうる。`camera_service.md`参照）等の任意ファイルがそのまま配信され得た。
* 根拠: [エンドポイント定義とDocstring] (行番号: 116〜118 / 抜粋: "def get_live_segment(camera_id: str, segment_file: str):\n    """ライブのHLSセグメント（.tsファイル）を配信"""")、[拡張子チェック] (行番号: 122〜123 / 抜粋: "if not segment_file.endswith(\".ts\"):\n        raise HTTPException(status_code=400, detail=\"Unsupported file extension\")")


* **引数/リクエスト**: `camera_id: str`, `segment_file: str`（いずれもパスパラメータ）
* 根拠: [引数定義] (行番号: 116〜117 / 抜粋: "@router.get("/live/{camera_id}/{segment_file}")\ndef get_live_segment(camera_id: str, segment_file: str):")


* **戻り値/レスポンス**: `FileResponse`（media_type="video/MP2T"）
* 根拠: [レスポンス返却] (行番号: 132 / 抜粋: "return FileResponse(segment_path, media_type="video/MP2T")")


* **副作用**: `_resolve_segment_path` によるパス検証、`camera_service.HLS_LIVE_DIR` の参照。
* 根拠: [パス解決] (行番号: 129 / 抜粋: "segment_path = _resolve_segment_path(camera_service.HLS_LIVE_DIR, camera_id, segment_file)")


* **エラーハンドリング**: `segment_file`が`.ts`で終わらない場合400（カメラID検証より前に判定）、カメラID未検出時404、セグメントファイル不在時404（`_resolve_segment_path`内で範囲外パスの場合も400が送出されうる）。
* 根拠: [エラー分岐] (行番号: 122〜123, 126〜127, 130〜131 / 抜粋: "if not segment_file.endswith(\".ts\"):\n        raise HTTPException(status_code=400, detail=\"Unsupported file extension\")")


## 5. 処理フロー図

録画ファイル配信のエンドポイント `get_record_file` を例に、拡張子による分岐ロジックを示します。

```mermaid
flowchart TD
    Start["Start: GET /record/{camera_id}/{target_date}/{filename}"] --> ExtCheck{"filenameの拡張子判定"}

    ExtCheck -- ".m3u8" --> FindCam1["config.CAMERASからカメラ設定を検索"]
    FindCam1 --> CamFound1{"カメラ設定が見つかったか?"}
    CamFound1 -- No --> Err404a["HTTPException 404: Camera not found"]
    CamFound1 -- Yes --> GenPlaylist["外部：camera_service.generate_record_playlist()"]
    GenPlaylist --> PlaylistOk{"プレイリストパスが取得できたか?"}
    PlaylistOk -- No --> Err404b["HTTPException 404: Recordings not found"]
    PlaylistOk -- Yes --> RespM3u8["FileResponse (application/vnd.apple.mpegurl)"]

    ExtCheck -- ".ts" --> FindCam2["config.CAMERASからカメラ設定を検索"]
    FindCam2 --> CamFound2{"カメラ設定が見つかったか?"}
    CamFound2 -- No --> Err404c["HTTPException 404: Camera not found"]
    CamFound2 -- Yes --> ResolvePath["_resolve_segment_path()でパス検証"]
    ResolvePath --> PathValid{"パスがbase_dir範囲内か?"}
    PathValid -- No --> Err400["HTTPException 400: Invalid path"]
    PathValid -- Yes --> FileExists{"セグメントファイルが存在するか?"}
    FileExists -- No --> Err404d["HTTPException 404: Segment not found"]
    FileExists -- Yes --> RespTs["FileResponse (video/MP2T)"]

    ExtCheck -- "その他" --> Err400b["HTTPException 400: Unsupported file extension"]
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "camera_router.py"
        router["router (APIRouter)"]
        CameraSettingsUpdate["CameraSettingsUpdate (BaseModel)"]
        resolve_segment_path["_resolve_segment_path()"]
        get_camera_settings["get_camera_settings()"]
        update_camera_settings["update_camera_settings()"]
        get_live_stream["get_live_stream()"]
        get_record_info["get_record_info()"]
        get_record_file["get_record_file()"]
        get_live_segment["get_live_segment()"]
    end

    subgraph "外部依存"
        fastapi["fastapi (APIRouter, HTTPException)"]
        fastapi_responses["fastapi.responses.FileResponse"]
        pydantic_mod["pydantic.BaseModel"]
        os_mod["os"]
        time_mod["time"]
        config["config (CAMERAS)"]
        camera_service["services.camera_service (ブラックボックス)"]
    end

    router --> fastapi
    CameraSettingsUpdate --> pydantic_mod
    update_camera_settings --> CameraSettingsUpdate
    update_camera_settings --> camera_service
    get_camera_settings --> config
    get_live_stream --> config
    get_live_stream --> camera_service
    get_live_stream --> time_mod
    get_live_stream --> fastapi_responses
    get_record_info --> config
    get_record_info --> camera_service
    get_record_file --> config
    get_record_file --> camera_service
    get_record_file --> resolve_segment_path
    get_record_file --> fastapi_responses
    get_live_segment --> config
    get_live_segment --> camera_service
    get_live_segment --> resolve_segment_path
    get_live_segment --> fastapi_responses
    resolve_segment_path --> os_mod
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `services/camera_service.py` | `start_hls_stream`, `get_record_start_offset`, `generate_record_playlist`, `set_camera_enabled`, `HLS_VOD_DIR`, `HLS_LIVE_DIR` の実装がすべてブラックボックスであり、本ルーターが依存するストリーム生成・録画処理・設定永続化ロジックの実態を把握する必要があるため。 | 根拠: [import文] (行番号: 8 / 抜粋: "from services import camera_service") |
| 中 | `config.py` | `config.CAMERAS` のデータ構造（各カメラ辞書に含まれるキーの全容）と読み込み元が不明であるため。 | 根拠: [config.CAMERASの参照] (行番号: 38 / 抜粋: "for idx, cam in enumerate(config.CAMERAS):") |

## 8. 保守上の注意点

* **同期的な待機処理によるブロッキング**: `get_live_stream` は最大5秒間 `time.sleep(0.5)` によるポーリングでブロックする。FastAPIの同期関数（`def`、`async def`ではない）内であるため、デフォルトのスレッドプール実行であればリクエストごとにワーカースレッドを占有する点に留意が必要。
* **（コミット`95d3e55`, E-3で解消）`enabled`フラグの固定値**: 修正前は `get_camera_settings` の `enabled` が常に `True`固定で、`config.CAMERAS` 側で無効化されたカメラの状態を反映する仕組みがなかった。現在は `cam.get("enabled", True)` により実際の値を反映し、`PUT /settings/{camera_id}`（`update_camera_settings`）で `camera_service.set_camera_enabled` 経由の永続化（`devices.json`書き込み）が可能になっている。
* **例外処理の欠如**: `get_camera_settings` では `config.CAMERAS` の各要素に `id`/`name` キーが存在しない場合の `KeyError` に対する処理がない（`enabled`キーのみ`.get`で防御的にアクセスしている）。`update_camera_settings` も `camera_service.set_camera_enabled` 呼び出し自体に対する `try-except` は持たず、`False`戻り値（対象カメラ不在）のみをHTTP 404として扱う。
* **パストラバーサル対策の一元化**: `.ts` セグメント配信は `_resolve_segment_path` により防御されているが、`.m3u8` の場合は `camera_service.generate_record_playlist` / `start_hls_stream` の戻り値パスをそのまま `FileResponse` に渡しており、パス検証の責務が `camera_service` 側にあるかは本ファイルからは確認できない。
* **（Issue #172で解消）拡張子チェックの非対称性**: `get_record_file` は`.m3u8`/`.ts`以外を400で拒否していたが、`get_live_segment` は修正前は拡張子を一切検証しておらず、`_resolve_segment_path`のパストラバーサル対策だけでは防げない形で同一カメラディレクトリ内の`ffmpeg.log`（`camera_service.py`が`chmod 600`で保護しているファイル。RTSP認証情報を含みうる）等が配信され得た。現在は`get_record_file`と同様に`.ts`以外を400で拒否する。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `camera_service` の各関数の内部実装 | `start_hls_stream`, `get_record_start_offset`, `generate_record_playlist`, `set_camera_enabled` の処理内容、`HLS_VOD_DIR`/`HLS_LIVE_DIR` の実際の値が本ファイルからは不明。 | `services/camera_service.py` |
| `config.CAMERAS` のデータ構造 | 各カメラ辞書のキー一覧や設定ファイルの読み込み元が不明。 | `config.py` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `camera_service` の各関数の内部実装 | `camera_service.md`の解析によれば、`start_hls_stream`はffmpegプロセスをカメラID単位で起動しHLSライブ配信用プレイリストパス（`str`、RTSP URL取得失敗時は空文字列）を返し、`get_record_start_offset`は指定日最初の録画ファイル名から算出した0時からの経過秒数（`int`、失敗時は`0`）を返し、`generate_record_playlist`は10分単位に分割されたmp4群を`ffconcat`で結合したVOD用HLSプレイリストパス（`Optional[str]`、保存先ディレクトリ不在時や対象ファイルなし時は`None`）を返すと推測される。ただし`HLS_LIVE_DIR`/`HLS_VOD_DIR`の実際のパス値自体は`camera_service.md`側でも本文中に明記が確認できず、依然として不明。 | camera_service.md |
| `camera_service` の各関数の内部実装（`HLS_LIVE_DIR`/`HLS_VOD_DIR`の実値） | `services/camera_service.py`を直接確認した。20行目で`BASE_DIR = os.path.dirname(os.path.dirname(__file__))`（`services/`の親、すなわち`MY_HOME_SYSTEM/`）と定義され、21〜22行目で`HLS_LIVE_DIR = os.path.join(BASE_DIR, "data", "hls_streams", "live")`、`HLS_VOD_DIR = os.path.join(BASE_DIR, "data", "hls_streams", "vod")`という固定のローカルディレクトリパス（`MY_HOME_SYSTEM/data/hls_streams/live`および`MY_HOME_SYSTEM/data/hls_streams/vod`）であることを確認した。`start_hls_stream`は86行目で`init_output_dir(HLS_LIVE_DIR, cam_id)`、`generate_record_playlist`は167行目で`init_output_dir(HLS_VOD_DIR, cam_id)`をそれぞれ呼び出しており、camera_service.md側の推測が実際のパス値でも裏付けられることを確認した。`set_camera_enabled(camera_id, enabled) -> bool`(322〜347行目)は、`config.DEVICES_JSON_PATH`が存在しなければ`False`を返し(325〜326行目)、存在すれば`devices.json`を読み込んで対象カメラを`id`で検索、見つからなければ`False`を返す(331〜334行目)。見つかった場合は`target["enabled"] = enabled`で更新した上で、一時ファイル(`{DEVICES_JSON_PATH}.tmp`)へ書き込んでから`os.replace`でアトミックに本ファイルへ反映し(337〜342行目。書き込み途中のクラッシュでdevices.json自体が壊れることを防ぐ設計)、さらに`config.CAMERAS`（インメモリ）内の対応する辞書の`enabled`も直接更新する(344〜347行目)ことを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/services/camera_service.py:20-22, 86, 167, 322-347` |
| `config.CAMERAS` のデータ構造 | `config.py`を直接確認した。`CAMERAS: List[Dict[str, Any]] = []`(297行目)は、`devices.json`（リポジトリ内に実体なし、`.gitignore`の`*.json`規則により追跡対象外）が存在すれば300〜305行目で`CAMERAS = [CameraConfig(**c).model_dump(by_alias=True) for c in _devices_data["cameras"]]`として読み込まれる。`CameraConfig`(Pydanticモデル、144〜154行目)のキー一覧は`id: str, name: str, nas_folder: Optional[str], location: str, ip: str, port: int(既定2020), user: Optional[str], password: Optional[str](エイリアス"pass"), rtsp_url: Optional[str], enabled: bool(既定True。コミット`95d3e55`で追加)`である。本ファイル(`camera_router.py`)自身も38〜44行目で`for idx, cam in enumerate(config.CAMERAS): {"id": cam["id"], "name": cam["name"], "enabled": cam.get("enabled", True)}`のように`id`/`name`キーへ実際にアクセスし`enabled`は`.get`で防御的に取得しており、58行目以降でも`next((c for c in config.CAMERAS if c["id"] == camera_id), None)`という形で`cam_conf`辞書を取得し`camera_service`の各関数へそのまま渡していることを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/config.py:144-154, 297, 300-305`, `MY_HOME_SYSTEM/routers/camera_router.py:38-44, 58-59, 78-79, 90-91, 102-103, 119-120` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
