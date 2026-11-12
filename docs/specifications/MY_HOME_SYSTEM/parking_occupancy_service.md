## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `MY_HOME_SYSTEM/services/parking_occupancy_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | 新規追加時点（駐車場の在否判定 Phase 0） |

## 関連ドキュメント

* [parking_occupancy_backfill.md](./parking_occupancy_backfill.md) - 本モジュールを呼ぶ唯一の経路。NAS の録画を遡って読み、結果を CSV に書き出す CLI
* [home_status_service.md](./home_status_service.md) - 今の「🚗 駐車場」カードの判定元。動体検知の最終時刻を出すだけで、車の在否は分からない
* [smart_timelapse_generator.md](./smart_timelapse_generator.md) - 同じく録画を ffmpeg の生フレームで読んで OpenCV にかける先行例。`nice`/`ionice`・スレッド数の絞り方を合わせている
* [record_playlist_service.md](./record_playlist_service.md) - 録画ファイルの置き場所と命名規則（`%Y%m%d_%H%M%S.mp4`）の定義元

## 2. ファイルの概要

駐車場カメラの映像から自家用車の在否を判定し、外出（出庫→入庫）の区間にまとめるサービス。**Phase 0（実現性検討）の実装で、DB にもダッシュボードにもつながっていない。** 物体検出や学習モデルは使わず、車がない時だけ見える2つの領域（石畳・床）の特徴量をしきい値で判定する。しきい値は昼・夜 × 車あり・なしのスクリーンショット4枚から決めた暫定値である。

* 根拠: モジュールdocstring (行番号: 2〜20 / 抜粋: "駐車場カメラの映像から自家用車の在否を判定し、外出(出庫→入庫)の区間にまとめる。")

処理は3段に分かれる。(1) 録画からフレームを取り出す（`extract_frames`）、(2) 1フレームの特徴量を計算して在否を判定する（`compute_features` / `classify`）、(3) 判定の並びから出庫・入庫を確定し、外出の区間と日ごとの集計にまとめる（`build_events` / `build_trips` / `summarize_daily`）。

* 根拠: `def analyze_recording(` (行番号: 253 / 抜粋: "def analyze_recording(")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `datetime` | 標準 | 観測の時刻、空白の判定 | 根拠: [インポート宣言] (行番号: 23 / 抜粋: "import datetime") |
| `shutil` | 標準 | `ionice` があるかの確認 | 根拠: [インポート宣言] (行番号: 24 / 抜粋: "import shutil") |
| `subprocess` | 標準 | ffmpeg の起動 | 根拠: [インポート宣言] (行番号: 25 / 抜粋: "import subprocess") |
| `tempfile` | 標準 | ffmpeg の stderr の受け皿 | 根拠: [インポート宣言] (行番号: 26 / 抜粋: "import tempfile") |
| `threading` | 標準 | 時間切れで ffmpeg を止める見張り(`Timer`) | 根拠: [インポート宣言] (行番号: 27 / 抜粋: "import threading") |
| `dataclasses.dataclass` | 標準 | 特徴量・観測・イベント・外出・日次集計の型 | 根拠: [インポート宣言] (行番号: 28 / 抜粋: "from dataclasses import dataclass") |
| `cv2` | サードパーティ | リサイズ・色変換・Canny・Laplacian | 根拠: [インポート宣言] (行番号: 31 / 抜粋: "import cv2") |
| `numpy` | サードパーティ | フレームの配列操作 | 根拠: [インポート宣言] (行番号: 32 / 抜粋: "import numpy as np") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `ffmpeg`（外部コマンド） | 録画のデコード結果は実行環境の ffmpeg と録画ファイルに依存する | 根拠: `def build_frame_command(path: str, interval_seconds: int) -> List[str]:` (行番号: 197) |
| 駐車場カメラの画角 | ROI の比率はカメラの向きが変わらない前提で決めている。画角が変わると判定できなくなる | 根拠: [定数定義] (行番号: 49 / 抜粋: "FLAGSTONE_ROI = Roi(0.52, 0.36, 0.78, 0.47)") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### 定数（ROI としきい値）

* **役割**: 判定に使う領域としきい値。エッジ密度は解像度で値が変わるため、フレームは必ず 1280×720 に揃えてから計算する。`FLAGSTONE_ROI` は車がない時に石畳の目地が見える領域、`FLOOR_ROI` は車がない時にコンクリートの床が見える領域で、自転車置き場・奥の道路・日時表示にはかからないように置いている。しきい値は「これ以下なら在」「これ以上なら不在」の2つを持ち、その間は不明とする。
* 根拠: [定数定義] (行番号: 52 / 抜粋: "FLOOR_ROI = Roi(0.55, 0.60, 0.85, 0.79)")
* 根拠: [定数定義] (行番号: 57 / 抜粋: "FLAGSTONE_EDGE_PRESENT_MAX = 0.13  # これ以下なら「車あり」")
* 根拠: [定数定義] (行番号: 60 / 抜粋: "FLOOR_OUTLIER_PRESENT_MIN = 0.10   # これ以上なら「車あり」")

### データ型

* **役割**: `ParkingFeatures`（1フレームの特徴量。しきい値を決め直せるよう、判定に使わない値も持つ）、`Observation`（時刻・判定・特徴量）、`ParkingEvent`（確定した出庫/入庫と、時刻が不確かかどうか）、`Trip`（外出1回分。`returned_at` が `None` ならまだ戻っていない）、`DailySummary`（出庫した日ごとの回数と合計時間）。いずれも `frozen=True` の dataclass。
* 根拠: `class ParkingFeatures:` (行番号: 91 / 抜粋: "class ParkingFeatures:")
* 根拠: `class Trip:` (行番号: 120 / 抜粋: "class Trip:")

### `compute_features`

* **役割**: BGR のフレームから特徴量を計算する。彩度の平均（IR かどうかの判別）、石畳 ROI のエッジ密度と Laplacian の分散、床 ROI の平均・標準偏差・エッジ密度・外れ画素の割合を返す。床は明るさそのものではなく「中央値から `FLOOR_OUTLIER_DELTA` より離れた画素の割合」を見る（夜は床全体が暗く写るため、明るさのしきい値だと昼と夜で別の値が要る）。
* 根拠: `def compute_features(frame_bgr: np.ndarray) -> ParkingFeatures:` (行番号: 150 / 抜粋: "def compute_features(")

* **引数/リクエスト**: `frame_bgr`（BGR の `np.ndarray`。大きさは問わない）
* 根拠: `def compute_features(frame_bgr: np.ndarray) -> ParkingFeatures:` (行番号: 150)

* **戻り値/レスポンス**: `ParkingFeatures`
* 根拠: `def compute_features(frame_bgr: np.ndarray) -> ParkingFeatures:` (行番号: 150)

* **副作用**: なし
* 根拠: `def compute_features(frame_bgr: np.ndarray) -> ParkingFeatures:` (行番号: 150)

* **エラーハンドリング**: なし。大きさが解析用と違えば `cv2.resize` で揃える
* 根拠: `def compute_features(frame_bgr: np.ndarray) -> ParkingFeatures:` (行番号: 150)

### `classify`

* **役割**: 石畳のエッジ密度と床の外れ画素の割合のそれぞれで在/不在/不明を出し（`_vote`）、**2つが一致した時だけ**その結果を返す。食い違えば不明。
* 根拠: `def classify(features: ParkingFeatures) -> str:` (行番号: 186 / 抜粋: "def classify(")

* **引数/リクエスト**: `features` (`ParkingFeatures`)
* 根拠: `def classify(features: ParkingFeatures) -> str:` (行番号: 186)

* **戻り値/レスポンス**: `STATE_PRESENT` / `STATE_ABSENT` / `STATE_UNKNOWN` のいずれか
* 根拠: `def classify(features: ParkingFeatures) -> str:` (行番号: 186)

* **副作用**: なし
* 根拠: `def classify(features: ParkingFeatures) -> str:` (行番号: 186)

* **エラーハンドリング**: なし
* 根拠: `def classify(features: ParkingFeatures) -> str:` (行番号: 186)

### `build_frame_command`

* **役割**: 録画から `interval_seconds` ごとに1枚、1280×720 の BGR 生フレームを stdout へ流す ffmpeg コマンドを組み立てる。`-skip_frame nokey` でキーフレームだけをデコードし、`nice`（あれば `ionice` も）とスレッド数の上限で負荷を抑える。
* 根拠: `def build_frame_command(path: str, interval_seconds: int) -> List[str]:` (行番号: 197 / 抜粋: "def build_frame_command(")

* **引数/リクエスト**: `path`（録画ファイル）、`interval_seconds`（何秒ごとに1枚か）
* 根拠: `def build_frame_command(path: str, interval_seconds: int) -> List[str]:` (行番号: 197)

* **戻り値/レスポンス**: コマンドの引数リスト
* 根拠: `def build_frame_command(path: str, interval_seconds: int) -> List[str]:` (行番号: 197)

* **副作用**: なし（`shutil.which` で `ionice` の有無を調べるのみ）
* 根拠: `def build_frame_command(path: str, interval_seconds: int) -> List[str]:` (行番号: 197)

* **エラーハンドリング**: なし
* 根拠: `def build_frame_command(path: str, interval_seconds: int) -> List[str]:` (行番号: 197)

### `extract_frames`

* **役割**: ffmpeg を起動し、stdout から1フレーム分ずつ読んで `np.ndarray` として順に返すジェネレータ。stderr はパイプにせず一時ファイルへ流す（読まないパイプが満杯になって ffmpeg が止まるのを避ける）。
* 根拠: `def extract_frames(path: str, interval_seconds: int) -> Iterator[np.ndarray]:` (行番号: 214 / 抜粋: "def extract_frames(")

* **引数/リクエスト**: `path`、`interval_seconds`
* 根拠: `def extract_frames(path: str, interval_seconds: int) -> Iterator[np.ndarray]:` (行番号: 214)

* **戻り値/レスポンス**: `(720, 1280, 3)` の `np.ndarray` を返すイテレータ
* 根拠: `def extract_frames(path: str, interval_seconds: int) -> Iterator[np.ndarray]:` (行番号: 214)

* **副作用**: ffmpeg の子プロセスを起動する
* 根拠: `def extract_frames(path: str, interval_seconds: int) -> Iterator[np.ndarray]:` (行番号: 214)

* **エラーハンドリング**: ffmpeg が 0 以外で終わった場合は、それまでに読めたフレームを返した後で、stderr の末尾を付けた `FrameExtractionError` を送出する（呼び出し側が「最後まで読めていない録画」と分かるようにするため）。読み始める前に `threading.Timer` で `FFMPEG_TIMEOUT_SECONDS` 後の kill を予約しておくので、NAS が固まって ffmpeg が出力を止めても read は EOF で抜け、同じ例外になる。呼び出し側が途中で読むのをやめた場合も `finally` で kill し、プロセスを残さない
* 根拠: `def extract_frames(path: str, interval_seconds: int) -> Iterator[np.ndarray]:` (行番号: 214)

### `analyze_recording`

* **役割**: 録画1本を読み、(観測, そのフレーム) を順に返す。時刻は「開始時刻 + 何枚目か × 間隔」で付ける。
* 根拠: `def analyze_recording(` (行番号: 253 / 抜粋: "def analyze_recording(")

* **引数/リクエスト**: `path`、`start`（録画の開始時刻）、`interval_seconds`
* 根拠: `def analyze_recording(` (行番号: 253)

* **戻り値/レスポンス**: `(Observation, np.ndarray)` のイテレータ
* 根拠: `def analyze_recording(` (行番号: 253)

* **副作用**: `extract_frames` を通じて ffmpeg を起動する
* 根拠: `def analyze_recording(` (行番号: 253)

* **エラーハンドリング**: なし（`extract_frames` に任せる）
* 根拠: `def analyze_recording(` (行番号: 253)

### `build_events`

* **役割**: 観測の並びから、確定した出庫・入庫を求める。不明の観測は読み飛ばす。最初の状態は同じ判定が `confirm_count` 回続いた時点で決まり、イベントにはしない。以降、今の状態と違う判定が `confirm_count` 回続いたら変化を確定し、イベントの時刻はその連続の最初の観測の時刻とする。今の状態を最後に確認できた時刻から `max_gap` より空いていたら `uncertain` を立てる。
* 根拠: `def build_events(` (行番号: 263 / 抜粋: "def build_events(")

* **引数/リクエスト**: `observations`（順不同でよい。内部で時刻順に並べる）、`confirm_count`（既定 3）、`max_gap`（既定 30分）
* 根拠: `def build_events(` (行番号: 263)

* **戻り値/レスポンス**: `ParkingEvent` のリスト（時刻順）
* 根拠: `def build_events(` (行番号: 263)

* **副作用**: なし
* 根拠: `def build_events(` (行番号: 263)

* **エラーハンドリング**: `confirm_count` が 1 未満なら `ValueError`
* 根拠: `def build_events(` (行番号: 263)

### `build_trips`

* **役割**: 出庫と、その次の入庫を組にして外出の区間にする。出庫より前の入庫（データの始まりで既に外出中だった分）は捨てる。最後の出庫に対応する入庫がなければ `returned_at=None` の外出として返す。出庫か入庫のどちらかが不確かなら、外出も不確かとする。
* 根拠: `def build_trips(events: Iterable[ParkingEvent]) -> List[Trip]:` (行番号: 309 / 抜粋: "def build_trips(")

* **引数/リクエスト**: `events`（時刻順の `ParkingEvent`）
* 根拠: `def build_trips(events: Iterable[ParkingEvent]) -> List[Trip]:` (行番号: 309)

* **戻り値/レスポンス**: `Trip` のリスト
* 根拠: `def build_trips(events: Iterable[ParkingEvent]) -> List[Trip]:` (行番号: 309)

* **副作用**: なし
* 根拠: `def build_trips(events: Iterable[ParkingEvent]) -> List[Trip]:` (行番号: 309)

* **エラーハンドリング**: なし
* 根拠: `def build_trips(events: Iterable[ParkingEvent]) -> List[Trip]:` (行番号: 309)

### `summarize_daily`

* **役割**: 出庫した日ごとに、外出の回数と合計時間（分）をまとめる。まだ戻っていない外出は回数には数え、時間には足さない。
* 根拠: `def summarize_daily(trips: Iterable[Trip]) -> List[DailySummary]:` (行番号: 328 / 抜粋: "def summarize_daily(")

* **引数/リクエスト**: `trips`
* 根拠: `def summarize_daily(trips: Iterable[Trip]) -> List[DailySummary]:` (行番号: 328)

* **戻り値/レスポンス**: `DailySummary` のリスト（日付の昇順）
* 根拠: `def summarize_daily(trips: Iterable[Trip]) -> List[DailySummary]:` (行番号: 328)

* **副作用**: なし
* 根拠: `def summarize_daily(trips: Iterable[Trip]) -> List[DailySummary]:` (行番号: 328)

* **エラーハンドリング**: なし
* 根拠: `def summarize_daily(trips: Iterable[Trip]) -> List[DailySummary]:` (行番号: 328)

## 5. 処理フロー図

```mermaid
flowchart TD
    A["analyze_recording(path, start, interval)"] --> B["extract_frames<br/>ffmpeg でキーフレームだけをデコードし<br/>interval 秒ごとに1枚"]
    B --> C["compute_features<br/>1280x720 に揃えて特徴量を計算"]
    C --> D["classify"]
    D --> E{"石畳の判定と床の判定が一致するか"}
    E -- "一致" --> F["在 または 不在"]
    E -- "食い違い / しきい値の間" --> G["不明"]
    F --> H["Observation"]
    G --> H
    H --> I["build_events<br/>不明は読み飛ばし、同じ判定が<br/>confirm_count 回続いたら確定"]
    I --> J["build_trips<br/>出庫と次の入庫を組にする"]
    J --> K["summarize_daily<br/>出庫した日ごとの回数と合計時間"]
```

## 6. 依存関係図

```mermaid
graph TD
    Svc["services/parking_occupancy_service.py"]

    subgraph Callers
        Cli["tools/parking_occupancy_backfill.py"]
    end

    subgraph Third_Party
        Cv2["cv2"]
        Np["numpy"]
    end

    subgraph External
        Ffmpeg["ffmpeg(外部コマンド)"]
    end

    Cli -->|"analyze_recording / build_events / build_trips / summarize_daily"| Svc
    Svc --> Cv2
    Svc --> Np
    Svc -->|"subprocess.Popen"| Ffmpeg
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `tools/parking_occupancy_backfill.py` | 本モジュールを呼ぶ唯一の経路 | モジュールdocstring (行番号: 2〜20) |
| 中 | `services/home_status_service.py` | 判定が固まった後に「🚗 駐車場」カードを在否の表示へ置き換える先 | モジュールdocstring (行番号: 2〜20) |

## 8. 保守上の注意点

* **しきい値は暫定値**: 昼・夜 × 車あり・なしのスクリーンショット各1枚から決めた。雨で濡れた石畳、人が石畳に立っている場面、停める位置の前後のずれ、夕方の IR/カラー切り替えは未検証。実機の録画で集めた特徴量（`observations.csv`）を見て決め直すこと。
* **カメラの画角が変わると判定できなくなる**: ROI はフレームに対する比率で固定している。カメラを動かしたら `FLAGSTONE_ROI` / `FLOOR_ROI` を置き直す。
* **エッジ密度は解像度に依存する**: `ANALYZE_WIDTH` / `ANALYZE_HEIGHT` を変えたら、しきい値も測り直す。
* **`extract_frames` は録画1本を丸ごと読む**: `-skip_frame nokey` でデコードは軽いが、ファイル自体は NAS から最後まで読む。
* **テストに実際のカメラ画像は使わない**: 自宅の映像をリポジトリに入れないため、`tests/test_parking_occupancy_service.py` は合成したフレームで判定を確かめている。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| 実機の録画での判定精度（不明の割合、出入りの検出漏れ・誤検出） | しきい値をスクリーンショット4枚だけで決めている | 実機での `tools/parking_occupancy_backfill.py` の実行結果と、手で記録した出入りの正解 |
| 実機（Raspberry Pi）での録画1本あたりの処理時間 | CPU と NAS の読み出し速度に依存する | 実機での計測 |
| 録画のキーフレーム間隔 | カメラ側の設定に依存する。`interval_seconds` より長いとフレームが間引かれる | 実機の録画ファイル |
