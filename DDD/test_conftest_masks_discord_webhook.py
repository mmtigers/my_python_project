# DDD/test_conftest_masks_discord_webhook.py
"""
Issue #103の回帰テスト。

newface_monitor.py は MY_HOME_SYSTEM を sys.path に追加して本物の
core.logger.get_logger() を import しており、これはモジュール import 時点
(=pytestのcollection時点)で config.DISCORD_WEBHOOK_ERROR を
DiscordErrorHandler に焼き込む。本番Raspberry Pi等、本物の認証情報が入った
.env のある環境で `pytest DDD/` を実行すると、ERRORログを出すテストケースが
実際にDiscordへWebhook POSTを送ってしまう経路が存在していた
(DDD/conftest.py が無く、MY_HOME_SYSTEM/tests/conftest.py と同様の
環境変数の無害化が行われていなかったため)。

同一プロセス内でのmonkeypatchでは「モジュールimport時に一度だけ焼き込まれる」
という挙動を正しく再現できない(既にimportされたnewface_monitorのlogger.handlers
はos.environの変更やconfigのmonkeypatchでは変わらない)ため、本テストは
実際に別プロセス(pytestサブプロセス)を起動し、ローカルに立てたダミーWebhook
サーバへの実POSTの有無で検証する。
"""
import http.server
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

DDD_DIR = Path(__file__).resolve().parent

_PROBE_TEST_SOURCE = textwrap.dedent(
    """\
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    import newface_monitor as m

    def test_probe_emits_error_log():
        # 破損ファイル検知等、ERRORレベルのログを出す既存テストケースを模した
        # 最小のプローブ。core.logger.DiscordErrorHandlerが焼き込まれていれば
        # ここでバックグラウンドスレッドからWebhookへのPOSTが飛ぶ。
        m.logger.error("regression-test-error-log")
        import time
        time.sleep(1.5)
    """
)


class _RequestRecorder(http.server.BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        _RequestRecorder.received.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandlerのシグネチャに合わせる
        pass


def _run_probe_and_count_webhook_posts(*, conftest_active: bool) -> list:
    """
    ダミーのDiscord Webhookサーバをローカルに立て、そのURLを
    DISCORD_WEBHOOK_ERROR に設定した状態で、サブプロセスのpytestで
    「ERRORログを1件出すだけ」のプローブテストを実行する。
    conftest_active=False の場合は DDD/conftest.py を一時的に退避し、
    「conftest.pyによる無害化が無ければ実際にPOSTが飛んでしまう」ことを
    確認する対照実験に使う。
    """
    _RequestRecorder.received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _RequestRecorder)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    conftest_path = DDD_DIR / "conftest.py"
    conftest_backup_path = DDD_DIR / "conftest.py._regression_test_backup"
    probe_path = DDD_DIR / "test_probe_zzz_webhook_regression.py"

    try:
        if not conftest_active:
            conftest_path.rename(conftest_backup_path)

        probe_path.write_text(_PROBE_TEST_SOURCE, encoding="utf-8")

        env = os.environ.copy()
        env["DISCORD_WEBHOOK_ERROR"] = f"http://127.0.0.1:{port}/fake-webhook"

        subprocess.run(
            [sys.executable, "-m", "pytest", str(probe_path), "-q"],
            cwd=str(DDD_DIR),
            env=env,
            # このサブプロセスはnewface_monitor経由でcore.logger/configの重い
            # importチェーンをコールドスタートで走らせる(モジュールimportの
            # キャッシュを外側のpytestプロセスと共有できないため)。ローカルの
            # コールドキャッシュ実測で約10秒だったが、CI初回実行(依存インストール
            # 直後でディスクキャッシュも無い状態)では30秒を超えて
            # TimeoutExpiredになることを確認したため、余裕を持たせる。
            timeout=120,
            capture_output=True,
        )
    finally:
        if probe_path.exists():
            probe_path.unlink()
        if not conftest_active and conftest_backup_path.exists():
            conftest_backup_path.rename(conftest_path)
        server.shutdown()
        thread.join(timeout=2)

    return list(_RequestRecorder.received)


def test_without_conftest_masking_a_real_webhook_url_would_actually_fire():
    """対照実験: DDD/conftest.pyによる無害化が無い場合、環境変数に本物のURLが
    入っていると実際にWebhookへPOSTが飛んでしまうことを確認する
    (=このテストが検出している問題を、conftest.pyが実際に防いでいるという裏取り)。"""
    received = _run_probe_and_count_webhook_posts(conftest_active=False)
    assert received, (
        "DDD/conftest.pyが無い状態でも実POSTが発生しなかった。"
        "本テスト自体の前提(newface_monitor経由の焼き込み)が崩れている可能性がある。"
    )


def test_conftest_masks_real_webhook_url_before_any_post_fires():
    """本修正の本体: DDD/conftest.pyがある状態では、環境変数に本物のURLが
    設定されていても、newface_monitor経由のERRORログでWebhookへの実POSTが
    発生しない。"""
    received = _run_probe_and_count_webhook_posts(conftest_active=True)
    assert not received, f"DDD/conftest.pyがあるにも関わらずWebhookへPOSTが発生した: {received}"
