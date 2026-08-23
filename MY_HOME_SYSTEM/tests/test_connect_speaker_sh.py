# MY_HOME_SYSTEM/tests/test_connect_speaker_sh.py
"""
tools/connect_speaker.sh の send_discord() JSONペイロード生成の回帰テスト。

M-8-3: エスケープ済みのescaped_messageを作成しておきながら、curlへの
JSONペイロードには未エスケープの$messageを埋め込んでいたため、
メッセージに二重引用符や改行が含まれるとJSONが壊れていた
(エスケープ処理自体が死にコードになっていた)。

bashスクリプトを実際に実行するテストインフラ(bats等)は無いため、
send_discord() 関数だけをbashから抽出してcurlをモック(echoに差し替え)し、
実際に生成されるペイロード文字列が有効なJSONになることを検証する。
"""
import json
import os
import subprocess
import sys

import pytest

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "tools", "connect_speaker.sh")


def _run_send_discord(message: str, tmp_path) -> str:
    """
    send_discord()関数だけをスクリプトからsourceし、curlをモックして
    実際に生成されたJSONペイロード(-dへ渡された引数)をファイルへ書き出す
    小さなbashラッパーを実行し、そのファイルの内容を返す。
    send_discord()本体がcurlの標準出力を`>/dev/null`へリダイレクトするため、
    標準出力経由では捕捉できず、ファイル経由で受け渡す。
    """
    capture_file = tmp_path / "captured_payload.txt"
    wrapper = f"""
set -e
WEBHOOK_URL="https://example.invalid/webhook"
CAPTURE_FILE="{capture_file}"
curl() {{
    prev=""
    for arg in "$@"; do
        if [ "$prev" = "-d" ]; then
            printf '%s' "$arg" > "$CAPTURE_FILE"
        fi
        prev="$arg"
    done
}}
{_extract_send_discord_function()}
send_discord "$1"
"""
    result = subprocess.run(
        ["bash", "-c", wrapper, "_", message],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"bash wrapper failed: {result.stderr}")
    return capture_file.read_text(encoding="utf-8")


def _extract_send_discord_function() -> str:
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        src = f.read()
    start = src.index("send_discord() {")
    # 対応する閉じ括弧(関数本体末尾の "}") までを抽出する。
    # ネストした if/fi はあるが波括弧のネストは無いため、次の行頭の "}" で十分。
    end = src.index("\n}", start) + len("\n}")
    return src[start:end]


@pytest.mark.skipif(sys.platform == "win32", reason="bashスクリプトのテストのためLinux/macOS専用")
class TestConnectSpeakerShSendDiscord:
    def test_message_with_double_quotes_produces_valid_json(self, tmp_path):
        payload = _run_send_discord('He said "hi" to me', tmp_path)
        parsed = json.loads(payload)
        assert parsed["content"] == 'He said "hi" to me'

    def test_message_with_backslash_produces_valid_json(self, tmp_path):
        payload = _run_send_discord(r"path is C:\Users\test", tmp_path)
        parsed = json.loads(payload)
        assert parsed["content"] == r"path is C:\Users\test"

    def test_plain_message_produces_valid_json(self, tmp_path):
        payload = _run_send_discord("スピーカー接続に成功しました", tmp_path)
        parsed = json.loads(payload)
        assert parsed["content"] == "スピーカー接続に成功しました"
