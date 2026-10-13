# MY_HOME_SYSTEM/services/ui_event_service.py
"""画面タップログ(ui_tap_events)の保存。

クライアントがバッチで送ってきたタップを、1トランザクションの executemany で
追記する。`quest_users` には触れないため、`services/quest/locks.py` の残高ロックとは
無関係(CLAUDE.md の並行制御の前提に影響しない)。
"""
import datetime
import sqlite3

import config
from core.database import get_db_cursor
from core.logger import setup_logging
from core.utils import get_now_iso
from models.ui_log import UiTapBatch

logger = setup_logging("ui_event_service")

JST = datetime.timezone(datetime.timedelta(hours=9), "JST")

# オフライン中にためた分の再送を受け入れる最大の遅れ。これより古いタップは
# 端末の時計の異常とみなして保存しない(分析を汚さないため)。
MAX_EVENT_AGE = datetime.timedelta(days=30)
# 端末の時計が進んでいる場合の許容(これより未来のタップは保存しない)。
MAX_FUTURE_SKEW = datetime.timedelta(minutes=10)

_INSERT_SQL = """
    INSERT OR IGNORE INTO ui_tap_events (
        event_id, user_id, session_id, occurred_at, received_at, screen,
        element_id, element_tag, is_interactive, x_pct, y_pct, layout_mode
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class UiEventService:
    def record_batch(self, batch: UiTapBatch, now: datetime.datetime | None = None) -> dict[str, int]:
        """バッチを保存し、{accepted, duplicated, rejected} を返す。

        `UI_TAP_LOG_ENABLED` が false のときは何も保存せず全件を accepted 扱いで返す
        (クライアントに失敗と誤認させ、再送を繰り返させないため)。
        """
        if not config.UI_TAP_LOG_ENABLED:
            return {"accepted": len(batch.events), "duplicated": 0, "rejected": 0}

        now = now or datetime.datetime.now(JST)
        received_at = get_now_iso()
        oldest = now - MAX_EVENT_AGE
        latest = now + MAX_FUTURE_SKEW

        rows = []
        rejected = 0
        for ev in batch.events:
            try:
                occurred = datetime.datetime.fromtimestamp(ev.occurred_at_ms / 1000, tz=JST)
            except (OverflowError, OSError, ValueError):
                # 10**18 のような桁外れの値(端末の時計の異常や localStorage の改ざん)は
                # datetime に変換できない。放置すると500になり、クライアントは5xxを
                # 再試行扱いにして同じキューを永久に再送し、以降のタップが詰まる。
                # 当該イベントだけを rejected として落とし、バッチ全体は受け付ける。
                rejected += 1
                continue
            if not (oldest <= occurred <= latest):
                rejected += 1
                continue
            rows.append((
                ev.event_id, ev.user_id, batch.session_id, occurred.isoformat(), received_at,
                ev.screen, ev.element_id, ev.element_tag, 1 if ev.is_interactive else 0,
                ev.x_pct, ev.y_pct, ev.layout_mode,
            ))

        inserted = 0
        if rows:
            try:
                with get_db_cursor(commit=True) as cur:
                    cur.executemany(_INSERT_SQL, rows)
                    # executemany の rowcount は実際に挿入された行の合計(INSERT OR IGNORE で
                    # 無視された重複は含まれない)。
                    inserted = cur.rowcount
            except sqlite3.OperationalError as e:
                # Issue #971: Webhook 等の書き込みと重なった "database is locked" 等。
                # 以前は握らずに伝播して500になり、starlette/anyio の ExceptionGroup で
                # 同じトレースバックが journal に二重出力されてログを汚していた。
                # タップログは UX 分析用のベストエフォートな記録で、失っても実害がないため、
                # save_log_generic と同じくフェイルソフトにして警告ログにとどめる。
                # 2xx を返すのは、5xx だとクライアント(tapLogger)が再試行し、書き込みが
                # 集中している最中に負荷を足すため。保存できなかった分は rejected に数え、
                # accepted + duplicated + rejected が送信件数と一致するようにする。
                logger.warning(
                    f"タップログ: DB書き込みに失敗したため {len(rows)} 件を破棄しました "
                    f"(session={batch.session_id}): {e}"
                )
                return {"accepted": 0, "duplicated": 0, "rejected": rejected + len(rows)}

        if rejected:
            logger.warning(f"タップログ: 時刻が範囲外のため {rejected} 件を破棄しました (session={batch.session_id})")
        return {"accepted": inserted, "duplicated": len(rows) - inserted, "rejected": rejected}


ui_event_service = UiEventService()
