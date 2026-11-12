# MY_HOME_SYSTEM/services/parking_occupancy_service.py
"""駐車場カメラの映像から自家用車の在否を判定し、外出(出庫→入庫)の区間にまとめる。

Phase 0(実現性検討)の実装で、DB にもダッシュボードにもまだつながっていない。
`tools/parking_occupancy_backfill.py` が NAS 上の NVR 録画を遡って読み、ここの関数で
特徴量と在否を求めて CSV に書き出す。しきい値は昼・夜 × 車あり・なしのスクリーンショット
4枚から決めた暫定値で、実機の録画で集めた特徴量を見て調整する前提。

判定の考え方(物体検出や学習モデルは使わない):

* 車は1台だけで、固定カメラの画面の右側およそ半分を毎回ほぼ同じ位置で占める。
* 車がない時だけ見える2つの領域(ROI)を見る。
    * 石畳: 目地のエッジが多い。昼も夜(IR)もエッジ密度がほぼ同じ値になる。
      車があるとルーフで隠れてエッジが減る。
    * 床(コンクリート): 明るさがほぼ一様。車があると窓・ライト・ボディの明暗が入り、
      中央値から大きく外れる画素が増える。
* 2つの判定が一致した時だけ在/不在とし、食い違えば「不明」にする。
* 1フレームの判定は乗り降りの人などで揺れるので、連続 N 回同じ結果になってから
  状態の変化として確定する(`build_events`)。
"""
from __future__ import annotations

import datetime
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Iterable, Iterator, List, NamedTuple, Optional

import cv2
import numpy as np

from core.logger import setup_logging

logger = setup_logging("parking_occupancy")

# === 解析に使うフレームの大きさ ===
# エッジ密度は解像度で値が変わるため、必ずこの大きさに揃えてから特徴量を計算する。
ANALYZE_WIDTH = 1280
ANALYZE_HEIGHT = 720


class Roi(NamedTuple):
    """フレームに対する比率(0.0〜1.0)で表した矩形。"""
    x0: float
    y0: float
    x1: float
    y1: float


# 車がない時は石畳の目地が見え、車がある時はルーフで隠れる領域。
FLAGSTONE_ROI = Roi(0.52, 0.36, 0.78, 0.47)
# 車がない時はコンクリートの床が見え、車がある時は車体の後ろ側が写る領域。
# 左の自転車置き場・奥の道路・右上の日時表示にはかからないように置いている。
FLOOR_ROI = Roi(0.55, 0.60, 0.85, 0.79)

# === 判定のしきい値(暫定。スクリーンショット4枚の実測から) ===
# 石畳のエッジ密度: 車なし 0.22(昼・夜とも) / 車あり 0.09(昼)・0.02(夜)
FLAGSTONE_EDGE_ABSENT_MIN = 0.17   # これ以上なら「車なし」
FLAGSTONE_EDGE_PRESENT_MAX = 0.13  # これ以下なら「車あり」
# 床の外れ画素の割合: 車なし 0.01〜0.02 / 車あり 0.16(昼)・0.33(夜)
FLOOR_OUTLIER_ABSENT_MAX = 0.06    # これ以下なら「車なし」
FLOOR_OUTLIER_PRESENT_MIN = 0.10   # これ以上なら「車あり」
FLOOR_OUTLIER_DELTA = 40           # 中央値からこれより離れた画素を「外れ」と数える
# 彩度の平均: IR(モノクロ)の夜は 4〜7 / カラーの昼は 25〜55
IR_SATURATION_MAX = 12.0
CANNY_LOW = 50
CANNY_HIGH = 150

STATE_PRESENT = "present"
STATE_ABSENT = "absent"
STATE_UNKNOWN = "unknown"

EVENT_DEPART = "depart"
EVENT_ARRIVE = "arrive"

# === 状態の確定 ===
DEFAULT_CONFIRM_COUNT = 3  # 連続でこの回数だけ同じ判定が続いたら状態の変化を確定する
# 判定できた観測どうしの間がこれより空いたら、その間に起きた出入りの時刻は「不確か」とする
# (録画の欠落・NAS の停止・「不明」が続いた場合)。
DEFAULT_MAX_GAP = datetime.timedelta(minutes=30)

# === フレームの取り出し ===
FFMPEG_NICE_LEVEL = 15
FFMPEG_THREADS = 2             # Issue #782 と同じ理由で、デコードのスレッド数を絞る
FFMPEG_TIMEOUT_SECONDS = 300   # 1ファイル(10分の録画)の処理に許す時間


@dataclass(frozen=True)
class ParkingFeatures:
    """1フレームから計算した特徴量。しきい値を決め直せるよう、判定に使わない値も残す。"""
    is_ir: bool
    saturation: float
    flagstone_edge: float
    flagstone_lap_var: float
    floor_mean: float
    floor_std: float
    floor_edge: float
    floor_outlier: float


@dataclass(frozen=True)
class Observation:
    """ある時刻の1フレームに対する判定。"""
    timestamp: datetime.datetime
    state: str
    features: Optional[ParkingFeatures] = None


@dataclass(frozen=True)
class ParkingEvent:
    """確定した出庫・入庫。`uncertain` は時刻が不確か(直前に観測の空白がある)ことを表す。"""
    kind: str
    at: datetime.datetime
    uncertain: bool = False


@dataclass(frozen=True)
class Trip:
    """外出1回分。`returned_at` が None ならまだ戻っていない(データの終わりで外出中)。"""
    departed_at: datetime.datetime
    returned_at: Optional[datetime.datetime]
    uncertain: bool = False

    @property
    def duration_minutes(self) -> Optional[float]:
        if self.returned_at is None:
            return None
        return (self.returned_at - self.departed_at).total_seconds() / 60


@dataclass(frozen=True)
class DailySummary:
    """出庫した日ごとの集計。外出時間は戻りが確定した外出だけを合計する。"""
    date: datetime.date
    trips: int
    away_minutes: float


def _crop(image: np.ndarray, roi: Roi) -> np.ndarray:
    height, width = image.shape[:2]
    return image[int(roi.y0 * height):int(roi.y1 * height), int(roi.x0 * width):int(roi.x1 * width)]


def _edge_density(gray: np.ndarray) -> float:
    return float(cv2.Canny(gray, CANNY_LOW, CANNY_HIGH).mean() / 255)


def compute_features(frame_bgr: np.ndarray) -> ParkingFeatures:
    """BGR のフレームから特徴量を計算する。大きさが違えば解析用の大きさに揃える。"""
    if frame_bgr.shape[:2] != (ANALYZE_HEIGHT, ANALYZE_WIDTH):
        frame_bgr = cv2.resize(frame_bgr, (ANALYZE_WIDTH, ANALYZE_HEIGHT), interpolation=cv2.INTER_AREA)

    saturation = float(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)[..., 1].mean())
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    flagstone = _crop(gray, FLAGSTONE_ROI)
    floor = _crop(gray, FLOOR_ROI)

    # 明るさそのものではなく「中央値からどれだけ外れた画素があるか」を見る。
    # 夜(IR)は床全体が暗く写るので、明るさのしきい値だと昼と夜で別の値が要る。
    floor_median = float(np.median(floor))
    floor_outlier = float((np.abs(floor.astype(np.int16) - floor_median) > FLOOR_OUTLIER_DELTA).mean())

    return ParkingFeatures(
        is_ir=saturation < IR_SATURATION_MAX,
        saturation=saturation,
        flagstone_edge=_edge_density(flagstone),
        flagstone_lap_var=float(cv2.Laplacian(flagstone, cv2.CV_64F).var()),
        floor_mean=float(floor.mean()),
        floor_std=float(floor.std()),
        floor_edge=_edge_density(floor),
        floor_outlier=floor_outlier,
    )


def _vote(value: float, present_when_low: bool, low: float, high: float) -> str:
    """値が low 以下・high 以上のどちらかなら在否を返し、その間なら不明を返す。"""
    if value <= low:
        return STATE_PRESENT if present_when_low else STATE_ABSENT
    if value >= high:
        return STATE_ABSENT if present_when_low else STATE_PRESENT
    return STATE_UNKNOWN


def classify(features: ParkingFeatures) -> str:
    """石畳と床の2つの判定が一致した時だけ在/不在を返す。それ以外は不明。"""
    flagstone_vote = _vote(
        features.flagstone_edge, True, FLAGSTONE_EDGE_PRESENT_MAX, FLAGSTONE_EDGE_ABSENT_MIN)
    floor_vote = _vote(
        features.floor_outlier, False, FLOOR_OUTLIER_ABSENT_MAX, FLOOR_OUTLIER_PRESENT_MIN)
    if flagstone_vote == floor_vote:
        return flagstone_vote
    return STATE_UNKNOWN


def build_frame_command(path: str, interval_seconds: int) -> List[str]:
    """録画から `interval_seconds` ごとに1枚、解析用の大きさの BGR 生フレームを stdout へ流す ffmpeg コマンド。"""
    cmd = [
        "nice", "-n", str(FFMPEG_NICE_LEVEL),
        "ffmpeg", "-v", "error", "-nostdin",
        "-threads", str(FFMPEG_THREADS),
        # キーフレームだけをデコードする。H.265 を全フレーム復号すると Pi の CPU を使い切る。
        "-skip_frame", "nokey",
        "-i", path,
        "-vf", f"fps=1/{interval_seconds},scale={ANALYZE_WIDTH}:{ANALYZE_HEIGHT}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    if shutil.which("ionice"):
        cmd = ["ionice", "-c", "2", "-n", "7"] + cmd
    return cmd


def extract_frames(path: str, interval_seconds: int) -> Iterator[np.ndarray]:
    """録画ファイルから `interval_seconds` ごとのフレームを順に返す。

    ffmpeg が失敗した場合は、それまでに読めたフレームを返したうえで警告を残す
    (書き込み途中で壊れた録画が1本あっても、全体の解析は止めない)。
    """
    frame_size = ANALYZE_WIDTH * ANALYZE_HEIGHT * 3
    cmd = build_frame_command(path, interval_seconds)
    # stderr はパイプにせず一時ファイルへ流す。パイプだと、読まないまま 64KB たまった時点で
    # ffmpeg が止まり、stdout 側も進まなくなる。
    with tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_file)
        assert process.stdout is not None, "Popen に stdout=PIPE を指定している"
        try:
            while True:
                raw = process.stdout.read(frame_size)
                if len(raw) != frame_size:
                    break
                yield np.frombuffer(raw, dtype=np.uint8).reshape((ANALYZE_HEIGHT, ANALYZE_WIDTH, 3))
            try:
                returncode = process.wait(timeout=FFMPEG_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                returncode = process.wait()
            if returncode != 0:
                stderr_file.seek(0)
                tail = stderr_file.read()[-500:].decode("utf-8", errors="ignore").strip()
                logger.warning(f"⚠️ フレームの取り出しに失敗しました: {path} (returncode={returncode}) {tail}")
        finally:
            process.stdout.close()
            if process.poll() is None:
                # 呼び出し側が途中で読むのをやめた場合。ffmpeg を残さない。
                process.kill()
                process.wait()


def analyze_recording(
    path: str, start: datetime.datetime, interval_seconds: int,
) -> Iterator[tuple[Observation, np.ndarray]]:
    """録画1本を読み、(観測, そのフレーム) を順に返す。時刻は開始時刻からの等間隔で付ける。"""
    for index, frame in enumerate(extract_frames(path, interval_seconds)):
        features = compute_features(frame)
        timestamp = start + datetime.timedelta(seconds=index * interval_seconds)
        yield Observation(timestamp, classify(features), features), frame


def build_events(
    observations: Iterable[Observation],
    confirm_count: int = DEFAULT_CONFIRM_COUNT,
    max_gap: datetime.timedelta = DEFAULT_MAX_GAP,
) -> List[ParkingEvent]:
    """観測の並びから、確定した出庫・入庫を求める。

    * 「不明」の観測は読み飛ばす(連続回数を数え直さない)。
    * 最初の状態は、同じ判定が `confirm_count` 回続いた時点で決まる(イベントにはしない)。
    * 以降、今の状態と違う判定が `confirm_count` 回続いたら状態の変化を確定する。
      イベントの時刻は、その連続の最初の観測の時刻。
    * 今の状態を最後に確認できた時刻から `max_gap` より空いていたら `uncertain` を立てる。
    """
    if confirm_count < 1:
        raise ValueError("confirm_count は 1 以上にしてください")

    events: List[ParkingEvent] = []
    confirmed: Optional[str] = None
    last_confirmed_at: Optional[datetime.datetime] = None
    run: List[Observation] = []

    for obs in sorted(observations, key=lambda o: o.timestamp):
        if obs.state == STATE_UNKNOWN:
            continue
        if obs.state == confirmed:
            run = []
            last_confirmed_at = obs.timestamp
            continue
        if run and run[-1].state != obs.state:
            run = []
        run.append(obs)
        if len(run) < confirm_count:
            continue

        first_at = run[0].timestamp
        if confirmed is not None:
            uncertain = last_confirmed_at is not None and first_at - last_confirmed_at > max_gap
            kind = EVENT_ARRIVE if obs.state == STATE_PRESENT else EVENT_DEPART
            events.append(ParkingEvent(kind, first_at, uncertain))
        confirmed = obs.state
        last_confirmed_at = obs.timestamp
        run = []

    return events


def build_trips(events: Iterable[ParkingEvent]) -> List[Trip]:
    """出庫と、その次の入庫を組にして外出の区間にする。

    出庫より前の入庫(データの始まりで既に外出中だった分)は、出た時刻が分からないので捨てる。
    最後の出庫に対応する入庫がなければ、`returned_at=None` の外出として返す。
    """
    trips: List[Trip] = []
    open_departure: Optional[ParkingEvent] = None
    for event in events:
        if event.kind == EVENT_DEPART:
            open_departure = event
        elif open_departure is not None:
            trips.append(Trip(open_departure.at, event.at, open_departure.uncertain or event.uncertain))
            open_departure = None
    if open_departure is not None:
        trips.append(Trip(open_departure.at, None, open_departure.uncertain))
    return trips


def summarize_daily(trips: Iterable[Trip]) -> List[DailySummary]:
    """出庫した日ごとに、外出の回数と合計時間(分)をまとめる。日付の昇順で返す。"""
    counts: dict[datetime.date, int] = {}
    minutes: dict[datetime.date, float] = {}
    for trip in trips:
        day = trip.departed_at.date()
        counts[day] = counts.get(day, 0) + 1
        minutes[day] = minutes.get(day, 0.0) + (trip.duration_minutes or 0.0)
    return [DailySummary(day, counts[day], minutes[day]) for day in sorted(counts)]
