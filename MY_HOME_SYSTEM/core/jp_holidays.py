# MY_HOME_SYSTEM/core/jp_holidays.py
"""日本の「国民の祝日」と、ファミクエにおける「休日」の判定。

これまでこのリポジトリには祝日という概念が無く、休日判定はどこも
`weekday() >= 5`(=土日)だけで行っていた。そのため祝日は完全に平日として扱われ、
ファミクエが祝日でも「朝の締切7:50」「会社勤務/小学校に行くクエストが出る」
「YouTubeごほうび券の上限が平日ぶん」といった平日の挙動のままになっていた。

外部の暦ライブラリ(jpholiday等)を入れずローカル計算にしているのは、
`services/quest/locks.py` に書かれていた当初の判断(個人用システムに依存を
増やしたくない)をそのまま踏襲したため。祝日法の規則は計算で閉じており、
春分・秋分も下の近似式(1980〜2099年で有効)で求まる。

判定の対象と精度:
    - 対象は現行(2020年以降)の祝日法。天皇誕生日=2/23、スポーツの日=10月第2月曜、
      山の日=8/11 といった現在の定義で計算する。
    - 2020・2021年に東京五輪のため一度だけ移動した海の日・スポーツの日・山の日、
      および2019年の即位関連の臨時の祝日は再現しない(過去日であり、この
      システムの判定対象は常に「今日」なので実害が無いため)。
    - 振替休日は2007年改正後の規則(日曜が祝日なら、その後の最も近い平日)で
      計算する。1980〜2006年の判定は近似になる。

公開API:
    get_national_holidays(year) : その年の {date: 祝日名} を返す
    get_holiday_name(d)         : その日の祝日名(家の休みなら"家の休み")、平日ならNone
    is_national_holiday(d)      : 国民の祝日(振替休日・国民の休日を含む)か
    is_offday(d)                : ファミクエ上の「休日」(土日 or 祝日 or 家の休み)か
    get_offday_reason(d)        : 休日である理由の表示名("土曜"/"敬老の日"等)、平日ならNone

`is_offday` が「土日」と「祝日」をまとめて返す1つの関数になっているのは、
呼び出し側(すごろくのスキップ判定・クエストの曜日判定・YouTubeの日次上限)が
どれも「今日は学校/会社がある日か」だけを知りたいためである。
"""
import datetime
import functools

import config

# weekday(): 月=0 ... 土=5, 日=6
WEEKEND_DAYS = frozenset({5, 6})

# 春分・秋分の近似式が有効な年の範囲。範囲外でも計算自体は行うが、
# 春分/秋分日が1日ずれる可能性がある(このシステムの寿命では到達しない)。
SUPPORTED_YEAR_MIN = 1980
SUPPORTED_YEAR_MAX = 2099

SUBSTITUTE_HOLIDAY_NAME = "振替休日"
CITIZENS_HOLIDAY_NAME = "国民の休日"
# config.EXTRA_HOLIDAY_DATES(年末年始・お盆・学校の振替休業日など、祝日ではないが
# 家庭の運用上は休日にしたい日)に付ける表示名。
EXTRA_HOLIDAY_NAME = "家の休み"

DateLike = datetime.date | datetime.datetime


def _as_date(value: DateLike) -> datetime.date:
    """date / aware・naive datetime のどちらを渡されても date に揃える。

    呼び出し側はJST固定の datetime(`core.utils.get_now_jst()` 由来)を持って
    いることが多いため、datetime をそのまま渡せるようにしている。
    """
    if isinstance(value, datetime.datetime):
        return value.date()
    return value


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> datetime.date:
    """その月の第nth・指定曜日の日付を返す(例: 9月第3月曜 = 敬老の日)。"""
    first = datetime.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + datetime.timedelta(days=offset + 7 * (nth - 1))


def _equinox_day(year: int, base: float) -> int:
    """春分/秋分の日(日付の"日"部分)を近似式で求める。

    baseは春分20.8431・秋分23.2488(1980〜2099年用の定数)。
    `int(...)` は切り捨てで、(year - 1980) が負になりうる範囲は
    SUPPORTED_YEAR_MIN 未満のみなので、`//` ではなく0方向への切り捨てにしている。
    """
    return int(base + 0.242194 * (year - 1980) - int((year - 1980) / 4))


def _statutory_holidays(year: int) -> dict[datetime.date, str]:
    """振替休日・国民の休日を除いた、その年の祝日そのものを返す。"""
    return {
        datetime.date(year, 1, 1): "元日",
        _nth_weekday(year, 1, 0, 2): "成人の日",
        datetime.date(year, 2, 11): "建国記念の日",
        datetime.date(year, 2, 23): "天皇誕生日",
        datetime.date(year, 3, _equinox_day(year, 20.8431)): "春分の日",
        datetime.date(year, 4, 29): "昭和の日",
        datetime.date(year, 5, 3): "憲法記念日",
        datetime.date(year, 5, 4): "みどりの日",
        datetime.date(year, 5, 5): "こどもの日",
        _nth_weekday(year, 7, 0, 3): "海の日",
        datetime.date(year, 8, 11): "山の日",
        _nth_weekday(year, 9, 0, 3): "敬老の日",
        datetime.date(year, 9, _equinox_day(year, 23.2488)): "秋分の日",
        _nth_weekday(year, 10, 0, 2): "スポーツの日",
        datetime.date(year, 11, 3): "文化の日",
        datetime.date(year, 11, 23): "勤労感謝の日",
    }


@functools.lru_cache(maxsize=8)
def _holidays_for_year(year: int) -> dict[datetime.date, str]:
    """その年の {date: 祝日名}(振替休日・国民の休日を含む)。

    lru_cacheで年単位にキャッシュする(1日の判定ごとに16日ぶんの日付計算を
    やり直さないため)。戻り値は内部キャッシュそのものなので、外部へ渡す
    `get_national_holidays` 側でコピーする。
    """
    statutory = _statutory_holidays(year)
    holidays: dict[datetime.date, str] = dict(statutory)

    # 振替休日: 祝日が日曜と重なったら、その後で最も近い「祝日でない日」が休日になる
    # (2007年改正後の規則。5/3が日曜なら5/4・5/5を飛ばして5/6が振替休日)。
    for holiday_date in sorted(statutory):
        if holiday_date.weekday() != 6:
            continue
        substitute = holiday_date + datetime.timedelta(days=1)
        while substitute in holidays:
            substitute += datetime.timedelta(days=1)
        holidays[substitute] = SUBSTITUTE_HOLIDAY_NAME

    # 国民の休日: 前後を祝日に挟まれた平日(日曜・祝日自身は対象外)。現行法では
    # 敬老の日(9月第3月曜)と秋分の日が1日あいだを空けて並ぶ年のシルバーウィーク
    # (例: 2026-09-22)だけが該当する。
    for holiday_date in sorted(statutory):
        sandwiched = holiday_date + datetime.timedelta(days=1)
        if sandwiched in holidays or sandwiched.weekday() == 6:
            continue
        if sandwiched + datetime.timedelta(days=1) in statutory:
            holidays[sandwiched] = CITIZENS_HOLIDAY_NAME

    return holidays


def get_national_holidays(year: int) -> dict[datetime.date, str]:
    """その年の国民の祝日を {date: 祝日名} で返す(呼び出し側が変更してよいコピー)。"""
    return dict(_holidays_for_year(year))


def is_national_holiday(value: DateLike) -> bool:
    """国民の祝日(振替休日・国民の休日を含む)か。土日は含まない。"""
    target = _as_date(value)
    return target in _holidays_for_year(target.year)


def _extra_holiday_dates() -> frozenset:
    """config側で設定された「家の休み」。読むのは呼び出し時(テストの差し替え用)。"""
    return getattr(config, "EXTRA_HOLIDAY_DATES", frozenset())


def get_holiday_name(value: DateLike) -> str | None:
    """祝日名(家の休みなら"家の休み")を返す。祝日でも家の休みでもなければNone。"""
    target = _as_date(value)
    if target in _extra_holiday_dates():
        return EXTRA_HOLIDAY_NAME
    return _holidays_for_year(target.year).get(target)


def is_offday(value: DateLike) -> bool:
    """ファミクエ上の「休日」か(土日 / 国民の祝日 / 家の休み)。

    すごろくのスキップ・チェックポイント時刻、デイリークエストの曜日判定、
    YouTubeごほうび券の日次上限は、いずれもこの1つの判定を共有する。
    """
    target = _as_date(value)
    if target.weekday() in WEEKEND_DAYS:
        return True
    return get_holiday_name(target) is not None


def get_offday_reason(value: DateLike) -> str | None:
    """休日である理由の表示名("土曜"/"日曜"/"敬老の日"/"家の休み")。平日ならNone。

    祝日が土日と重なっている場合は祝日名を優先する(表示・ログ用)。
    """
    target = _as_date(value)
    name = get_holiday_name(target)
    if name:
        return name
    if target.weekday() == 5:
        return "土曜"
    if target.weekday() == 6:
        return "日曜"
    return None
