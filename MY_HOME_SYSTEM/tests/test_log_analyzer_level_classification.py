"""monitors/log_analyzer.py の重大度判定の回帰テスト(2026-09-19)。

以前は全行を ERROR_KEYWORDS の部分一致だけで判定していたため、本文に "error" や
"Exception" を含む WARNING 行がエラーとして数えられていた。実機の2日分のログでは
「エラー」728件のうち711件(97.7%)がこの誤検知で、health_watch の app_logs が常時
「異常」になり、同一異常の再通知抑制で本物の異常が埋もれる原因になっていた。
"""
import datetime
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors.log_analyzer import LogAnalyzer


@pytest.fixture
def analyzer():
    a = LogAnalyzer(days_back=7)
    a.now = datetime.datetime(2026, 9, 19, 12, 0, 0)
    a.start_date = datetime.datetime(2026, 9, 18, 0, 0, 0)
    return a


@pytest.mark.parametrize("line", [
    # 実機で誤ってエラー扱いされていた警告行(ONVIF再接続。本文に "error" / "NewConnectionError")
    "2026-09-19 12:16:22 [WARNING] camera: ⚠️ [庭カメラ] PullMessages failed 3 times in a row: "
    "Unknown error: HTTPConnectionPool(host='192.168.1.51', port=1025): Max retries exceeded "
    "(Caused by NewConnectionError(\"...[Errno 111] Connection refused\")). Breaking loop to reconnect.",
    # ffmpeg の stderr を載せた警告行(本文に "Error splitting ...")
    "2026-09-19 12:18:15 [WARNING] camera: ⚠️ [庭カメラ] FFmpeg extraction failed (rc=69, src=x.mp4, "
    "sseof=-3): [h264] Error splitting the input into NAL units. (Attempt 1/3)",
    # 本文に "Exception" を含む警告行
    "2026-09-19 12:00:00 [WARNING] scheduler: task raised Exception, will retry next cycle",
])
def test_warning_lines_mentioning_error_words_are_warnings(analyzer, line):
    assert analyzer._classify_line(line) == "warning"


@pytest.mark.parametrize("line", [
    "2026-09-19 12:00:00 [INFO] quest_service: Deleted obsolete quests (no Error)",
    "2026-09-19 12:00:00 [DEBUG] camera: 🔬 [RAW EVENTS] ... Exception ...",
    "INFO:     127.0.0.1:0 - \"GET /api/quest/data HTTP/1.1\" 200 OK",  # uvicorn(レベル表記の無い書式)
])
def test_info_and_debug_lines_are_not_counted(analyzer, line):
    assert analyzer._classify_line(line) is None


@pytest.mark.parametrize("line", [
    "2026-09-18 05:00:03 [ERROR] newface_monitor: Failed to load data from x.json",
    "2026-09-18 05:00:03 [CRITICAL] unified_server: crash loop detected",
    # logging の既定書式(ライブラリが basicConfig のまま出す行)
    "ERROR:zeep.xsd.types.simple:Error during xml -> python translation",
])
def test_error_and_critical_levels_are_errors(analyzer, line):
    assert analyzer._classify_line(line) == "error"


@pytest.mark.parametrize("line", [
    # レベル表記の無いトレースバック継続行は、従来どおりキーワードで判定する(#381 の挙動を維持)
    "Traceback (most recent call last):",
    "json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes",
    "Sep 19 12:00:00 raspi sshd[123]: Failed password for invalid user admin",
])
def test_lines_without_level_marker_fall_back_to_keywords(analyzer, line):
    assert analyzer._classify_line(line) == "error"


def test_analyze_file_counts_by_level(analyzer, tmp_path):
    log = tmp_path / "home_system.log"
    log.write_text(
        "2026-09-19 12:16:22 [WARNING] camera: PullMessages failed: Unknown error: NewConnectionError\n"
        "2026-09-19 12:16:23 [WARNING] camera: FFmpeg extraction failed: Error splitting\n"
        "2026-09-19 12:16:24 [INFO] camera: Connected\n"
        "2026-09-19 12:16:25 [ERROR] newface_monitor: Failed to load data\n"
        "Traceback (most recent call last):\n",
        encoding="utf-8",
    )
    analyzer._analyze_file(str(log))

    assert analyzer.report_data["home_system.log"]["errors"] == 2  # ERROR行 + トレースバック行
    assert analyzer.report_data["home_system.log"]["warnings"] == 2
