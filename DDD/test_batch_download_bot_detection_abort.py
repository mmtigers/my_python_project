# DDD/test_batch_download_bot_detection_abort.py
"""
Issue #104の回帰テスト。

ScrapingStrategy._download_segments_and_localize_manifest は、HLSの全セグメントを
ThreadPoolExecutor(max_workers=5)へ一括submitしたのち、as_completed()で回収する。
いずれかのセグメント取得がボット検知(403/429/503によるBotDetectionError)で
失敗した場合、モジュールdocstring/仕様書が謳う「即時セッション中断」を実際に
機能させるには、まだ実行が始まっていない残りのキュー済みセグメントの取得を
キャンセルする必要がある。

修正前は、`with ThreadPoolExecutor(...) as executor:` ブロックの終了時に暗黙で
呼ばれる `executor.shutdown(wait=True)`(cancel_futuresなし)が、キュー済みの
残り全セグメントのHTTP GETが完走するまで例外の伝播をブロックしてしまい、
ブロック中のCDNへのアクセスが継続してしまっていた。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_batch_download_bot_detection_abort.py` のように直接指定して
実行する(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
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
# ScrapingStrategy._FRAGMENT_DOWNLOAD_WORKERS と同じ値を前提にする
# (テスト対象が変更された場合に追随できるよう、モジュール側の値を直接参照する)。
WORKERS = module.ScrapingStrategy._FRAGMENT_DOWNLOAD_WORKERS


def _build_manifest_and_targets(n: int) -> str:
    """seg_000.ts 〜 seg_{n-1}.ts のURIのみを持つ最小のHLSマニフェスト文字列を作る。"""
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for i in range(n):
        lines.append("#EXTINF:2.0,")
        lines.append(f"https://example.test/seg_{i:03d}.ts")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines)


def test_bot_detection_cancels_queued_segments_instead_of_draining_them(tmp_path):
    """
    セグメント0でBotDetectionErrorが発生した場合、まだ実行が始まっていない
    キュー済みのセグメント取得(WORKERSを超える分)はキャンセルされ、実際には
    走らないこと(=修正前は全件が実行されてしまっていた)を確認する。
    """
    started_count = 0
    lock = threading.Lock()

    def fake_download_segment(self, url: str, page_url: str) -> bytes:
        # 最初のセグメント(0番)だけボット検知を模したエラーを即座に送出する。
        if "seg_000" in url:
            raise module.BotDetectionError(f"{url}: HTTP 403（ボット検知/レート制限の可能性）")
        # 他のセグメントは「実際にダウンロードが開始された」ことを記録したうえで、
        # 5並列のworkerが取り合っている間に後続のキャンセルが間に合うよう、
        # 短い遅延を入れてから完了する。
        nonlocal started_count
        with lock:
            started_count += 1
        time.sleep(0.3)
        return b"segment-bytes"

    manifest = _build_manifest_and_targets(TOTAL_SEGMENTS)

    strategy = module.ScrapingStrategy(save_base_dir=tmp_path, session=module.NetworkManager.create_session())
    with patch.object(module.ScrapingStrategy, "_download_segment", fake_download_segment):
        with pytest.raises(module.BotDetectionError):
            strategy._download_segments_and_localize_manifest(manifest, "https://example.test/page", tmp_path)

    # 修正前(キャンセルなし)は、5並列のworkerが尽きるまでキュー済みの残り全件
    # (TOTAL_SEGMENTS - 1 = 29件)が実行されてしまう。修正後は、既に実行が
    # 始まっていた分(最大でもWORKERS件程度)しか走らないはず。
    assert started_count < TOTAL_SEGMENTS - 1, (
        f"started_count={started_count} 件が実行された。"
        f"キュー済みの残りセグメントがキャンセルされずに完走してしまっている可能性がある。"
    )
    # 実行中だった分(最大でも初期に走り出したWORKERS件程度)は許容する。
    assert started_count <= WORKERS + 2, (
        f"started_count={started_count} 件は、実行中だった分の許容範囲"
        f"(WORKERS={WORKERS} 前後)を大きく超えている。"
    )
