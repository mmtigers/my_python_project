# DDD/test_batch_download_keyboard_interrupt_abort.py
"""
2回目の停止シグナル(_handle_signal が送出する KeyboardInterrupt)が
_download_segments_and_localize_manifest のセグメント並列取得中に発生した場合、
キュー済みの残りセグメントをキャンセルして即座に伝播すること(=Issue #104 の
BotDetectionError と同じ扱い)の回帰テスト。

KeyboardInterrupt は Exception の派生ではないため、以前の `except Exception:` を
素通りして with ブロック終了時の shutdown(wait=True)(cancel_futures なし)に落ち、
「即時強制中断」のはずが数千件のキュー済みセグメントを完走するまで止まらなかった。
"""
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import batch_download_discord as module  # noqa: E402

TOTAL_SEGMENTS = 30
WORKERS = module.ScrapingStrategy._FRAGMENT_DOWNLOAD_WORKERS


def _build_manifest(n: int) -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for i in range(n):
        lines.append("#EXTINF:2.0,")
        lines.append(f"https://example.test/seg_{i:03d}.ts")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines)


def test_keyboard_interrupt_cancels_queued_segments(tmp_path):
    started_count = 0
    lock = threading.Lock()

    def fake_download_segment(self, url: str, page_url: str) -> bytes:
        if "seg_000" in url:
            raise KeyboardInterrupt("second interrupt signal received; forcing immediate shutdown")
        nonlocal started_count
        with lock:
            started_count += 1
        time.sleep(0.3)
        return b"segment-bytes"

    strategy = module.ScrapingStrategy(save_base_dir=tmp_path, session=module.NetworkManager.create_session())
    with patch.object(module.ScrapingStrategy, "_download_segment", fake_download_segment):
        with pytest.raises(KeyboardInterrupt):
            strategy._download_segments_and_localize_manifest(_build_manifest(TOTAL_SEGMENTS), "https://example.test/page", tmp_path)

    assert started_count < TOTAL_SEGMENTS - 1, f"started_count={started_count}: キュー済みセグメントがキャンセルされていない"
    assert started_count <= WORKERS + 2
