# MY_HOME_SYSTEM/services/log_download_service.py
"""
ダッシュボードのシステムページからログファイルをダウンロードするためのサービス。

用途は「スマホにログをダウンロードして Claude Code に渡し、調査する」こと。
そのため行の絞り込みや検索は持たず、ファイルをそのまま(機密情報だけ伏せて)返す。

安全のための設計:
- 対象は `config.LOG_DIR` 直下の `*.log` / `*.log.1`(logrotateで直前に退避された非圧縮分)と、
  DDDの `newface_monitor.log` だけ。リクエストの名前は、ここで列挙した一覧との**完全一致**で
  しか解決しない(パスは受け取らない。`..` や `/` を含む名前は一覧に存在しないので必ず拒否される)。
- シンボリックリンクと通常ファイル以外は対象にしない。
- 返却サイズは `config.LOG_DOWNLOAD_MAX_MB` が上限。超えたら末尾側だけを返し、先頭に注記する。
- 返却前に既知の形式の機密情報(Webhook URL・token/secret/password等の値・Bearerトークン・
  URL埋め込みの認証情報)をマスクする。**既知の形式だけ**なので、氏名・IPアドレス等は伏せない。
"""
import os
import re
from dataclasses import dataclass
from datetime import datetime

import config
from core import discord as core_discord
from core.logger import setup_logging

logger = setup_logging("service.log_download")

# DDDのログは MY_HOME_SYSTEM のログと名前が衝突しないよう、この接頭辞を付けて公開する。
_DDD_PREFIX = "DDD__"
_DDD_LOG_NAMES = ("newface_monitor.log",)

_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.log(\.1)?$")

_MASK = "<redacted>"
# `token=xxx` / `"password": "xxx"` / `Authorization: xxx` のように、キー名の直後の値を伏せる。
_KEYED_SECRET_RE = re.compile(
    r"""(?ix)
    (?P<key>\b(?:access[_-]?token|refresh[_-]?token|token|secret|password|passwd|api[_-]?key|apikey|
                authorization|signature|sign)\b["']?\s*[=:]\s*["']?)
    (?P<val>(?:Bearer\s+)?[^\s"'&,;}\]]+)
    """
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
# scheme://user:pass@host の user:pass 部分(RTSP URL等)
_URL_CREDENTIALS_RE = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")


@dataclass(frozen=True)
class LogFile:
    name: str
    path: str
    size: int
    mtime: datetime


def redact_secrets(text: str) -> str:
    """既知の形式の機密情報を伏せた文字列を返す。"""
    text = core_discord.redact_webhook_url(text)
    text = _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group('scheme')}{_MASK}@", text)
    text = _KEYED_SECRET_RE.sub(lambda m: f"{m.group('key')}{_MASK}", text)
    text = _BEARER_RE.sub(f"Bearer {_MASK}", text)
    return text


def _ddd_log_dir() -> str:
    return os.path.join(os.path.dirname(config.BASE_DIR), "DDD", "logs")


def _stat_regular_file(path: str) -> os.stat_result | None:
    """シンボリックリンクでない通常ファイルのときだけ stat を返す。"""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        return os.stat(path)
    except OSError:
        return None


def _candidates() -> list[tuple[str, str]]:
    """(公開名, 実パス) の候補を列挙する。"""
    candidates: list[tuple[str, str]] = []
    try:
        entries = sorted(os.listdir(config.LOG_DIR))
    except OSError as e:
        logger.warning(f"ログディレクトリを列挙できませんでした: {e}")
        entries = []
    for entry in entries:
        if _LOG_NAME_RE.match(entry):
            candidates.append((entry, os.path.join(config.LOG_DIR, entry)))
    ddd_dir = _ddd_log_dir()
    for entry in _DDD_LOG_NAMES:
        candidates.append((_DDD_PREFIX + entry, os.path.join(ddd_dir, entry)))
    return candidates


def list_log_files() -> list[LogFile]:
    """ダウンロードできるログの一覧(更新が新しい順)。"""
    files: list[LogFile] = []
    for name, path in _candidates():
        st = _stat_regular_file(path)
        if st is None:
            continue
        files.append(LogFile(name=name, path=path, size=st.st_size, mtime=datetime.fromtimestamp(st.st_mtime)))
    files.sort(key=lambda f: f.mtime, reverse=True)
    return files


def resolve_log_file(name: str) -> LogFile | None:
    """公開名から一覧内のファイルを返す。一覧に無い名前(パス文字列を含む)は None。"""
    for f in list_log_files():
        if f.name == name:
            return f
    return None


def read_log_for_download(log_file: LogFile) -> tuple[str, bool]:
    """(マスク済みの本文, 末尾だけに切り詰めたか) を返す。"""
    max_bytes = max(config.LOG_DOWNLOAD_MAX_MB, 1) * 1024 * 1024
    truncated = False
    with open(log_file.path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size > max_bytes:
            truncated = True
            fh.seek(size - max_bytes)
        raw = fh.read(max_bytes)
    if truncated:
        # 途中から読み始めたので、欠けた先頭行は捨てる
        nl = raw.find(b"\n")
        raw = raw[nl + 1:] if nl != -1 else raw
    text = redact_secrets(raw.decode("utf-8", errors="replace"))
    if truncated:
        text = (
            f"# [log_download] ファイルが上限({config.LOG_DOWNLOAD_MAX_MB}MB)を超えたため、"
            f"末尾側のみを返しています(元のサイズ: {size} バイト)\n" + text
        )
    return text, truncated


def download_filename(log_file: LogFile, now: datetime) -> str:
    """スマホで開きやすいよう末尾を .txt にし、取得日時を付けた保存名。"""
    return f"{log_file.name}.{now.strftime('%Y%m%d-%H%M')}.txt"
