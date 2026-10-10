# MY_HOME_SYSTEM/services/release_history_service.py
"""お家ダッシュボードの「アップデート」ページ用に、全アプリの更新履歴を集めて1本にまとめる。

データの持ち方(各アプリが自分の履歴を持ち、ここは読んで結合するだけ。二重管理しない):

| アプリ | 取得元 | 種別 |
| --- | --- | --- |
| ダッシュボード | `MY_HOME_SYSTEM/dashboard_releases.json`(手書き) | ローカルファイル |
| ファミクエ | `family-quest/src/lib/changelog.json`(アプリ内の履歴と同じファイル) | ローカルファイル |
| あさノート | `<ASA_NOTE_URL>releases.json`(アプリが公開する) | HTTP |
| よるノート | `<YORU_NOTE_URL>releases.json`(アプリが公開する) | HTTP |

外部(HTTP)の取得は、タイムアウト(`REMOTE_TIMEOUT_SEC`)・サイズ上限・件数上限・文字数上限を
付け、成功は `CACHE_TTL_SEC` キャッシュする。失敗したときは、直前に取れた内容があれば
それを「古い内容」として返し、無ければ空にする(失敗は `NEGATIVE_CACHE_TTL_SEC` だけ覚えて、
閲覧のたびに外部へ問い合わせて待たされないようにする)。**取得した内容は外部由来の
データなので、画面へ出すときは必ずエスケープする**(`dashboard_page_service`側)。

どの関数も例外を送出しない(ページ全体を落とさない)。
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urljoin

import config
import requests
from core.logger import setup_logging

logger = setup_logging("release_history")

KEY_DASHBOARD = "dashboard"
KEY_QUEST = "quest"
KEY_ASA = "asa"
KEY_YORU = "yoru"
# 表示名と並び順(同じ日付のときの順序にも使う)
APPS: tuple[tuple[str, str], ...] = (
    (KEY_DASHBOARD, "ダッシュボード"),
    (KEY_QUEST, "ファミクエ"),
    (KEY_ASA, "あさノート"),
    (KEY_YORU, "よるノート"),
)
APP_LABELS: dict[str, str] = dict(APPS)

REMOTE_TIMEOUT_SEC: float = 3.0
CACHE_TTL_SEC: float = 600.0
NEGATIVE_CACHE_TTL_SEC: float = 60.0
MAX_RESPONSE_BYTES: int = 1_000_000
MAX_ENTRIES_PER_SOURCE: int = 50
MAX_DETAILS_PER_ENTRY: int = 20
MAX_TEXT_LEN: int = 500
# 公開される履歴のファイル名(各アプリ側の `/releases.json`)
REMOTE_RELEASES_PATH = "releases.json"

_cache_lock = threading.Lock()
# key -> (取得時刻 monotonic, 成功したか, 正規化済みエントリ)
_cache: dict[str, tuple[float, bool, list[dict[str, Any]]]] = {}
# key -> 直前に成功したときの内容(失敗時に「古い内容」として出す)
_last_good: dict[str, list[dict[str, Any]]] = {}


def dashboard_releases_path() -> str:
    return os.path.join(config.BASE_DIR, "dashboard_releases.json")


def quest_changelog_path() -> str:
    """ファミクエの履歴。リポジトリ直下の `family-quest/src/lib/changelog.json`。"""
    return os.path.join(os.path.dirname(config.BASE_DIR), "family-quest", "src", "lib", "changelog.json")


def _text(value: Any) -> str:
    return value.strip()[:MAX_TEXT_LEN] if isinstance(value, str) else ""


def normalize_entries(raw: Any, app: str) -> list[dict[str, Any]]:
    """アプリごとの形式のまま読み込んだ履歴を、共通の形にそろえる。

    共通の形: `{app, app_label, version, date, title, summary, details[]}`(文字列はすべて
    長さを切り詰め済み)。ファミクエは `changes` を `details` として扱う。`date` が
    `YYYY-MM-DD` でない・本文が空のエントリは捨てる。リストでなければ空。
    """
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw[:MAX_ENTRIES_PER_SOURCE]:
        if not isinstance(item, dict):
            continue
        date = _text(item.get("date"))
        if len(date) != 10 or date[4] != "-" or date[7] != "-":
            continue
        details_raw = item.get("details") if "details" in item else item.get("changes")
        details = [t for t in (_text(d) for d in (details_raw if isinstance(details_raw, list) else [])) if t]
        entry = {
            "app": app,
            "app_label": APP_LABELS[app],
            "version": _text(item.get("version")),
            "date": date,
            "title": _text(item.get("title")),
            "summary": _text(item.get("summary")),
            "details": details[:MAX_DETAILS_PER_ENTRY],
        }
        if entry["title"] or entry["summary"] or entry["details"]:
            entries.append(entry)
    return entries


def _load_local(path: str, app: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return normalize_entries(json.load(f), app)


def _fetch_remote(base_url: str, app: str) -> list[dict[str, Any]]:
    """アプリが公開する `releases.json` を取得する。失敗は例外(呼び出し側が握る)。"""
    url = urljoin(base_url, REMOTE_RELEASES_PATH)
    with requests.get(url, timeout=REMOTE_TIMEOUT_SEC, stream=True) as res:
        res.raise_for_status()
        body = b""
        for chunk in res.iter_content(65536):
            body += chunk
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError(f"応答が大きすぎます(>{MAX_RESPONSE_BYTES}バイト)")
    return normalize_entries(json.loads(body.decode("utf-8")), app)


def _remote_url(app: str) -> str:
    return config.ASA_NOTE_URL if app == KEY_ASA else config.YORU_NOTE_URL


def _load_source(app: str) -> dict[str, Any]:
    """1アプリ分の履歴を読む。戻り値: `{app, label, entries, ok, stale, error}`。"""
    result: dict[str, Any] = {"app": app, "label": APP_LABELS[app], "entries": [], "ok": True, "stale": False, "error": ""}
    remote = app in (KEY_ASA, KEY_YORU)
    now = time.monotonic()
    if remote:
        with _cache_lock:
            cached = _cache.get(app)
        if cached is not None:
            fetched_at, ok, entries = cached
            ttl = CACHE_TTL_SEC if ok else NEGATIVE_CACHE_TTL_SEC
            if now - fetched_at < ttl:
                return _from_cached(result, app, ok, entries)
    try:
        if app == KEY_DASHBOARD:
            entries = _load_local(dashboard_releases_path(), app)
        elif app == KEY_QUEST:
            entries = _load_local(quest_changelog_path(), app)
        else:
            entries = _fetch_remote(_remote_url(app), app)
    except Exception as e:  # noqa: BLE001 (1アプリの失敗でページ全体を落とさない)
        logger.warning(f"{app} の更新履歴を取得できませんでした: {type(e).__name__}: {e}")
        if remote:
            with _cache_lock:
                _cache[app] = (time.monotonic(), False, [])
        return _failed(result, app)
    if remote:
        with _cache_lock:
            _cache[app] = (time.monotonic(), True, entries)
            _last_good[app] = entries
    result["entries"] = entries
    return result


def _from_cached(result: dict[str, Any], app: str, ok: bool, entries: list[dict[str, Any]]) -> dict[str, Any]:
    if ok:
        result["entries"] = entries
        return result
    return _failed(result, app)


def _failed(result: dict[str, Any], app: str) -> dict[str, Any]:
    """取得失敗。直前に取れた内容があれば古い内容として返し、無ければ空。"""
    with _cache_lock:
        previous = _last_good.get(app)
    result["ok"] = False
    result["error"] = "取得できませんでした"
    if previous:
        result["entries"] = previous
        result["stale"] = True
        result["error"] = "取得できませんでした(最後に取得できた内容を表示しています)"
    return result


def get_release_history() -> dict[str, Any]:
    """全アプリの更新履歴を、日付の新しい順(同じ日付は `APPS` の順)にまとめて返す。

    戻り値: `{"entries": [...], "sources": [{app, label, entries, ok, stale, error}, ...]}`。
    外部(HTTP)の2アプリは並列に取得するので、遅くても待ちは `REMOTE_TIMEOUT_SEC` 程度。
    """
    keys = [key for key, _ in APPS]
    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        sources = list(pool.map(_load_source, keys))
    order = {key: i for i, key in enumerate(keys)}
    entries = [e for s in sources for e in s["entries"]]
    entries.sort(key=lambda e: (e["date"], -order[e["app"]]), reverse=True)
    return {"entries": entries, "sources": sources}


def clear_cache() -> None:
    """キャッシュを空にする(テスト用)。"""
    with _cache_lock:
        _cache.clear()
        _last_good.clear()
