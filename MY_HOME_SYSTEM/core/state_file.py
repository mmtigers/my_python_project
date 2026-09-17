# MY_HOME_SYSTEM/core/state_file.py
"""監視スクリプトの状態ファイル(JSON)を安全に読み書きする共通ヘルパー。

Issue #661: 状態ファイルの読み書きは `monitors/` を中心に7箇所で個別に実装されており、
アトミック性・ロック・破損時の扱いがばらばらだった(素の `open('w')` で書くもの、
flock + tmp + `os.replace` まで行うもの)。素の書き込みは電源断・クラッシュで
不完全な JSON を残し、次回の読み込みが壊れた状態を「初期状態」と誤認する。

ここでは switchbot_power_monitor が実装していた最も堅い方式に揃える:

- 書き込み: 一時ファイルへ書く → `flush` + `fsync` → `os.replace` で原子的に差し替える。
  読み手は常に「旧の完全な内容」か「新の完全な内容」のどちらかを見る。
- 読み込み: 共有ロック(`LOCK_SH`)を取ってから読む。ファイルが無い・壊れている場合は
  呼び出し側が渡した `default` を返す(何を安全側とみなすかは用途で違うため、
  ここでは決めずに呼び出し側へ委ねる。例: nas_monitor は「障害継続」側に倒す)。

いずれも例外は送出せず、失敗は warning ログにして呼び出し元(長時間走る監視ループ)を
落とさない。
"""
from __future__ import annotations

import fcntl
import json
import os
from typing import Any, Optional

from core.logger import get_logger

logger = get_logger("state_file")


def read_json(path: str, default: Any = None) -> Any:
    """状態ファイルを読む。存在しない・壊れている・読めない場合は `default` を返す。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        logger.warning(f"⚠️ 状態ファイルの読み込みに失敗しました({path}): {e}")
        return default


def write_json_atomic(path: str, data: Any) -> bool:
    """状態ファイルを原子的に書く。成功したら True、失敗したら False(例外は出さない)。"""
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        logger.warning(f"⚠️ 状態ファイルの書き込みに失敗しました({path}): {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False


def read_text(path: str, default: Optional[str] = None) -> Optional[str]:
    """1行だけの状態(タイムスタンプ等)を読む。存在しない・読めない場合は `default`。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        logger.warning(f"⚠️ 状態ファイルの読み込みに失敗しました({path}): {e}")
        return default


def write_text_atomic(path: str, text: str) -> bool:
    """1行だけの状態を原子的に書く。"""
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        logger.warning(f"⚠️ 状態ファイルの書き込みに失敗しました({path}): {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False
