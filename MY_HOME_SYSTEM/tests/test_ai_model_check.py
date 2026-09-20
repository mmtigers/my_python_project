"""起動時の Gemini モデル検査のテスト (Issue #801)。

`gemini-2.0-flash` が Google 側で提供終了し、LINE Bot の対話機能が丸ごと停止したが、
ログ保持範囲(2026-09-09 以降)で Bot への受信が1通だけだったため **エラーが発生する
機会自体が無く**、誰も気づかなかった。このテストは「誰かが使う前に起動時点で気づける」
ことを固定する。

外部 API は一切叩かない(`requests.get` を差し替える)。
"""
import json

import pytest


class _Res:
    """`requests.get` の戻り値の最小スタブ。"""

    def __init__(self, status_code=200, payload=None, raises=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._payload


def _models(*names, method="generateContent"):
    return {"models": [
        {"name": f"models/{n}", "supportedGenerationMethods": [method]} for n in names
    ]}


@pytest.fixture
def checker(monkeypatch):
    """`PostBootHealthCheck` を副作用なしで1つ作る。

    `__init__` が何をしていても結果リストだけ空で差し替えたいので、
    インスタンスは `__new__` で作り `results` を自前で持たせる。
    """
    import post_boot_health_check as pbhc

    obj = pbhc.PostBootHealthCheck.__new__(pbhc.PostBootHealthCheck)
    obj.results = []
    return obj, pbhc


def _run(checker, monkeypatch, *, api_key="k", model="gemini-flash-latest", res=None, boom=None):
    obj, pbhc = checker
    monkeypatch.setattr(pbhc.config, "GEMINI_API_KEY", api_key, raising=False)
    monkeypatch.setattr(pbhc.config, "GEMINI_MODEL", model, raising=False)

    def fake_get(url, params=None, timeout=None, **kw):
        assert "generativelanguage.googleapis.com" in url
        # キーはクエリで渡す。URL に埋め込むとログに残りうるため。
        assert params and params.get("key") == api_key
        if boom:
            raise boom
        return res

    monkeypatch.setattr(pbhc.requests, "get", fake_get)
    obj.check_ai_model()
    assert len(obj.results) == 1
    return obj.results[0]


class TestConfiguredModelIsAvailable:
    def test_ok_when_model_is_listed(self, checker, monkeypatch):
        r = _run(checker, monkeypatch,
                 res=_Res(payload=_models("gemini-flash-latest", "gemini-3.8-flash")))
        assert r.status == "OK"
        assert "gemini-flash-latest" in r.message


class TestRetiredModelIsDetected:
    def test_error_when_model_is_missing(self, checker, monkeypatch):
        """これが本 Issue そのもの。提供終了を起動時に検出できること。"""
        r = _run(checker, monkeypatch, model="gemini-2.0-flash",
                 res=_Res(payload=_models("gemini-3.8-flash", "gemini-flash-latest")))
        assert r.status == "ERR"
        assert "gemini-2.0-flash" in r.message
        assert "GEMINI_MODEL" in r.message, "復旧方法(.env の差し替え)が読み取れること"

    def test_error_message_suggests_alternatives(self, checker, monkeypatch):
        r = _run(checker, monkeypatch, model="gemini-2.0-flash",
                 res=_Res(payload=_models("gemini-3.8-flash", "gemini-3.6-flash")))
        assert "gemini-3.6-flash" in r.message or "gemini-3.8-flash" in r.message

    def test_preview_models_are_not_suggested(self, checker, monkeypatch):
        """代替候補に preview を勧めない(本番の対話に据えるものではない)。"""
        r = _run(checker, monkeypatch, model="gemini-2.0-flash",
                 res=_Res(payload=_models("gemini-3-flash-preview", "gemini-3.8-flash")))
        assert "preview" not in r.message

    def test_model_without_generate_content_is_not_usable(self, checker, monkeypatch):
        """一覧に名前があっても generateContent 非対応なら使えない。"""
        r = _run(checker, monkeypatch, model="gemini-embed",
                 res=_Res(payload=_models("gemini-embed", method="embedContent")))
        assert r.status == "ERR"


class TestDoesNotBreakBootReport:
    """検査の失敗で起動レポートを落とさないこと。他のチェック結果は届けたい。"""

    def test_http_error_is_warn_not_error(self, checker, monkeypatch):
        r = _run(checker, monkeypatch, res=_Res(status_code=403))
        assert r.status == "WARN"
        assert "403" in r.message

    def test_network_exception_is_warn(self, checker, monkeypatch):
        r = _run(checker, monkeypatch, boom=OSError("network down"))
        assert r.status == "WARN"
        assert "確認できず" in r.message

    def test_malformed_json_is_warn(self, checker, monkeypatch):
        r = _run(checker, monkeypatch, res=_Res(raises=True))
        assert r.status == "WARN"


class TestSkippedWhenUnconfigured:
    """API キー未設定は障害ではない(NatureRemo 未設定時と同じ扱い)。"""

    @pytest.mark.parametrize("api_key, model", [
        (None, "gemini-flash-latest"),
        ("", "gemini-flash-latest"),
        ("k", None),
        ("k", ""),
    ])
    def test_skipped(self, checker, monkeypatch, api_key, model):
        obj, pbhc = checker
        monkeypatch.setattr(pbhc.config, "GEMINI_API_KEY", api_key, raising=False)
        monkeypatch.setattr(pbhc.config, "GEMINI_MODEL", model, raising=False)

        def boom(*a, **k):
            raise AssertionError("未設定なら API を叩いてはいけない")

        monkeypatch.setattr(pbhc.requests, "get", boom)
        obj.check_ai_model()
        assert obj.results[0].status == "OK"
        assert "スキップ" in obj.results[0].message


class TestApiKeyIsNotLeaked:
    def test_key_is_passed_as_params_not_in_url(self, checker, monkeypatch):
        """キーを URL に埋め込まないこと(例外メッセージやログに載りうるため)。"""
        obj, pbhc = checker
        monkeypatch.setattr(pbhc.config, "GEMINI_API_KEY", "SECRETKEY", raising=False)
        monkeypatch.setattr(pbhc.config, "GEMINI_MODEL", "gemini-flash-latest", raising=False)
        seen = {}

        def fake_get(url, params=None, timeout=None, **kw):
            seen["url"] = url
            seen["params"] = params
            return _Res(payload=_models("gemini-flash-latest"))

        monkeypatch.setattr(pbhc.requests, "get", fake_get)
        obj.check_ai_model()
        assert "SECRETKEY" not in seen["url"]
        assert seen["params"]["key"] == "SECRETKEY"
        assert "SECRETKEY" not in json.dumps([r.message for r in obj.results])


class TestModelComesFromConfig:
    """ai_service がハードコードではなく config.GEMINI_MODEL を使うこと (Issue #801)。"""

    def test_ai_service_reads_config(self, monkeypatch):
        """CI では conftest が認証情報を空にするため、キーを入れて読み直して検証する。

        ここを `GEMINI_API_KEY` の有無で skip すると、**CI では一度も検証されない**
        テストになり、ハードコードへの逆戻りを検出できなくなる。
        """
        import importlib

        import config
        import services.ai_service as ai

        monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-test")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
        importlib.reload(config)
        try:
            importlib.reload(ai)
            assert ai.MODEL_NAME == "gemini-3.8-flash", (
                "ai_service がモデル名をハードコードに戻していないこと"
            )
            assert ai.MODEL_NAME == config.GEMINI_MODEL
        finally:
            # 後片付けは autouse fixture が config を戻すが、ai_service 側も戻す。
            monkeypatch.undo()
            importlib.reload(config)
            importlib.reload(ai)

    def test_default_is_the_non_retiring_alias(self, monkeypatch):
        """既定値が個別バージョンではなくエイリアスであること。

        個別バージョンを既定にすると、提供終了のたびに本 Issue が再発する。
        """
        import importlib

        import config

        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        importlib.reload(config)
        assert config.GEMINI_MODEL == "gemini-flash-latest"

    def test_env_overrides_the_default(self, monkeypatch):
        import importlib

        import config

        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
        importlib.reload(config)
        assert config.GEMINI_MODEL == "gemini-3.8-flash"

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        """空文字を入れてモデル名なしで起動してしまわないこと。"""
        import importlib

        import config

        monkeypatch.setenv("GEMINI_MODEL", "   ")
        importlib.reload(config)
        assert config.GEMINI_MODEL == "gemini-flash-latest"


@pytest.fixture(autouse=True)
def _restore_config():
    """`importlib.reload(config)` を使うテストの後始末。

    config はモジュールレベル定数を多数持ち、他テストが参照するため、
    書き換えたまま次のテストへ渡さない。
    """
    yield
    import importlib

    import config

    importlib.reload(config)
