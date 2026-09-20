"""Gemini の一時的な失敗を再試行することのテスト (Issue #804)。

2026-09-20 15:58 に実機で 503 UNAVAILABLE を踏み、**1回も再試行されないまま**
「申し訳ございません。AIサービスで予期せぬエラーが発生しました。」が返った。
Google 自身が "Spikes in demand are usually temporary. Please try again later."
と案内する種類の失敗である。

再試行の判定(`_is_transient_error`)と文面の判定(`_is_quota_error`)を分けたのが
本 Issue の修正なので、**その線引き**を固定する。
"""
import pytest
from google.genai import errors as genai_errors
from services import ai_service


def _api_error(code: int) -> genai_errors.APIError:
    """指定 HTTP ステータスの APIError を作る。

    google-genai の APIError はレスポンス由来の dict から code を拾う。
    SDK の内部表現に依存しすぎないよう、生成後に code を直接確かめる。
    """
    err = genai_errors.APIError(code, {"error": {"code": code, "message": "test"}})
    assert getattr(err, "code", None) == code, "APIError の code 解釈が SDK 側で変わった"
    return err


class TestTransientClassification:
    @pytest.mark.parametrize("code", [429, 503])
    def test_transient_codes_are_retried(self, code):
        assert ai_service._is_transient_error(_api_error(code)) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422, 500])
    def test_permanent_codes_are_not_retried(self, code):
        """何度送っても同じ結果になるものは再試行しない。

        特に 404 はモデルの提供終了(Issue #801)で、再試行すると
        AI_REPLY_TIMEOUT_SEC(20秒)を無駄に消費してタイムアウト文面になる。

        500 はこのリポジトリが以前から「リトライしても回復しないAPIエラー」として
        扱ってきたもので(tests/test_ai_service.py の _fatal_api_error)、本 Issue では
        その判断を変えない。実際に踏んで「待てば直る」と確認できたものだけを足す。
        """
        assert ai_service._is_transient_error(_api_error(code)) is False

    def test_non_api_errors_are_not_retried(self):
        assert ai_service._is_transient_error(ValueError("boom")) is False


class TestQuotaIsOnlyAboutWording:
    """`_is_quota_error` は文面の判定専用で、再試行条件ではないこと (Issue #804)。"""

    def test_only_429_is_quota(self):
        assert ai_service._is_quota_error(_api_error(429)) is True
        assert ai_service._is_quota_error(_api_error(503)) is False

    def test_quota_is_a_subset_of_transient(self):
        """429 は文面が違うだけで、再試行の対象でもあること。"""
        err = _api_error(429)
        assert ai_service._is_quota_error(err) is True
        assert ai_service._is_transient_error(err) is True


class _FlakyChat:
    """指定回数だけ失敗し、その後成功するチャットセッションのスタブ。"""

    def __init__(self, fail_times: int, code: int):
        self.fail_times = fail_times
        self.code = code
        self.calls = 0

    async def send_message(self, _prompt):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _api_error(self.code)
        return "OK"


@pytest.fixture(autouse=True)
def _no_sleep():
    """tenacity の待機を潰し、テストを実時間で待たせない。

    `_call_gemini_api_with_retry` は async なので、tenacity は同期用の
    `tenacity.nap.sleep` ではなく **AsyncRetrying の `sleep`**(既定は
    `asyncio.sleep`)を使う。そちらを差し替えないと待機が実時間で走り、
    テストが十数秒かかるうえ「速いから効いている」と誤解しやすい。
    """
    retrying = ai_service._call_gemini_api_with_retry.retry
    original = retrying.sleep

    async def _instant(_seconds):
        return None

    retrying.sleep = _instant
    try:
        yield
    finally:
        retrying.sleep = original


class TestRetryActuallyHappens:
    async def test_503_is_retried_and_then_succeeds(self):
        """これが本 Issue そのもの。503 で即あきらめないこと。"""
        chat = _FlakyChat(fail_times=1, code=503)
        result = await ai_service._call_gemini_api_with_retry(chat, "hi")
        assert result == "OK"
        assert chat.calls == 2, "1回目の 503 で終わらず、2回目で成功していること"

    async def test_429_is_retried(self):
        chat = _FlakyChat(fail_times=1, code=429)
        assert await ai_service._call_gemini_api_with_retry(chat, "hi") == "OK"
        assert chat.calls == 2

    async def test_404_is_not_retried(self):
        """提供終了したモデル(Issue #801)を何度も叩かないこと。"""
        chat = _FlakyChat(fail_times=99, code=404)
        with pytest.raises(genai_errors.APIError):
            await ai_service._call_gemini_api_with_retry(chat, "hi")
        assert chat.calls == 1, "1回だけ呼んで即座に諦めること"

    async def test_gives_up_after_max_attempts(self):
        """一時的失敗が続く場合、無限には粘らないこと(reply token の失効を避ける)。"""
        chat = _FlakyChat(fail_times=99, code=503)
        with pytest.raises(genai_errors.APIError):
            await ai_service._call_gemini_api_with_retry(chat, "hi")
        assert chat.calls == ai_service.MAX_RETRIES
