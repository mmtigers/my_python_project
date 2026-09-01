# MY_HOME_SYSTEM/tests/test_alexa_verifier.py
"""
core/alexa_verifier.py の署名・証明書・タイムスタンプ検証ロジックのテスト。

ask-sdk-webservice-support 同梱の検証器(certvalidator/oscrypto依存)を使わず、
`cryptography` だけで自前実装した検証ロジックが、Amazon公式ドキュメント記載の
手順どおりに正しい署名を受理し、不正なもの(改ざん/不正なURL/期限切れ証明書/
リプレイ)を確実に拒否することを確認する。
"""
import os
import sys
import base64
import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core import alexa_verifier as av

VALID_CERT_URL = "https://s3.amazonaws.com/echo.api/cert.pem"


def _make_cert(key, san="echo-api.amazon.com", not_before_delta=-1, not_after_delta=1):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + datetime.timedelta(days=not_before_delta))
        .not_valid_after(now + datetime.timedelta(days=not_after_delta))
    )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False
        )
    return builder.sign(key, hashes.SHA256())


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _clear_cert_cache():
    av._cert_cache.clear()
    yield
    av._cert_cache.clear()


def _sign_and_encode(key, body: bytes) -> str:
    signature = key.sign(body, padding.PKCS1v15(), hashes.SHA1())
    return base64.b64encode(signature).decode()


def _mock_get(pem_bytes: bytes):
    resp = MagicMock()
    resp.content = pem_bytes
    resp.status_code = 200
    resp.raise_for_status = lambda: None
    return resp


class TestVerifySignature:
    def test_accepts_valid_signature(self, rsa_key):
        cert = _make_cert(rsa_key)
        pem = cert.public_bytes(serialization.Encoding.PEM)
        body = b'{"request":{"type":"LaunchRequest"}}'
        sig_b64 = _sign_and_encode(rsa_key, body)

        with patch("core.alexa_verifier.requests.get", return_value=_mock_get(pem)):
            av.verify_signature(body, sig_b64, VALID_CERT_URL)  # raises on failure

    def test_rejects_tampered_body(self, rsa_key):
        cert = _make_cert(rsa_key)
        pem = cert.public_bytes(serialization.Encoding.PEM)
        body = b'{"request":{"type":"LaunchRequest"}}'
        sig_b64 = _sign_and_encode(rsa_key, body)

        with patch("core.alexa_verifier.requests.get", return_value=_mock_get(pem)):
            with pytest.raises(av.AlexaVerificationError):
                av.verify_signature(b"tampered body", sig_b64, VALID_CERT_URL)

    def test_rejects_missing_headers(self):
        with pytest.raises(av.AlexaVerificationError):
            av.verify_signature(b"body", "", VALID_CERT_URL)
        with pytest.raises(av.AlexaVerificationError):
            av.verify_signature(b"body", "sig", "")

    @pytest.mark.parametrize("bad_url", [
        "https://evil.com/echo.api/cert.pem",
        "http://s3.amazonaws.com/echo.api/cert.pem",
        "https://s3.amazonaws.com/not-echo-api/cert.pem",
        "https://s3.amazonaws.com:8443/echo.api/cert.pem",
    ])
    def test_rejects_invalid_cert_chain_url(self, bad_url):
        with pytest.raises(av.AlexaVerificationError):
            av.verify_signature(b"body", "sig", bad_url)

    @pytest.mark.parametrize("traversal_url", [
        # Issue #173: 正規化前は "/echo.api/" で始まるためstartswithを素通りしてしまうが、
        # ".." を解決すると /echo.api/ の外側を指す
        "https://s3.amazonaws.com/echo.api/../evil-bucket/cert.pem",
        "https://s3.amazonaws.com/echo.api/../../etc/passwd",
        "https://s3.amazonaws.com/echo.api/a/../../evil-bucket/cert.pem",
        # Issue #223: パーセントエンコードされた".."はurlparse().pathの時点では
        # デコードされないため、normpathへ渡す前にデコードしないと素通りしてしまう。
        # requestsは送信前にこれらを実際に".."へデコードするため、検証と取得先が
        # 食い違ってしまう。
        "https://s3.amazonaws.com/echo.api/%2e%2e/evil-bucket/cert.pem",
        "https://s3.amazonaws.com/echo.api/%2E%2E/evil-bucket/cert.pem",
        "https://s3.amazonaws.com/echo.api/%2e%2e/%2e%2e/etc/passwd",
        "https://s3.amazonaws.com/echo.api/a/%2e%2e/%2e%2e/evil-bucket/cert.pem",
    ])
    def test_rejects_cert_chain_url_with_path_traversal(self, traversal_url):
        """Issue #173/#223の回帰テスト: Amazon公式の検証手順はURLパスを正規化した後に
        /echo.api/で始まることを要求するが、以前は生のparsed.pathへのstartswith判定
        のみだったため、".."を含むURLがチェックを素通りしていた(#173)。その後の修正でも
        parsed.pathはパーセントデコードされないままnormpathへ渡していたため、
        "%2e%2e"のようなエンコード済みの".."がチェックを素通りしていた(#223)。"""
        with pytest.raises(av.AlexaVerificationError):
            av.verify_signature(b"body", "sig", traversal_url)

    def test_rejects_expired_certificate(self, rsa_key):
        cert = _make_cert(rsa_key, not_before_delta=-10, not_after_delta=-1)
        pem = cert.public_bytes(serialization.Encoding.PEM)
        body = b'{"request":{"type":"LaunchRequest"}}'
        sig_b64 = _sign_and_encode(rsa_key, body)

        with patch("core.alexa_verifier.requests.get", return_value=_mock_get(pem)):
            with pytest.raises(av.AlexaVerificationError, match="expired"):
                av.verify_signature(body, sig_b64, VALID_CERT_URL)

    def test_rejects_certificate_missing_required_san(self, rsa_key):
        cert = _make_cert(rsa_key, san="not-alexa.example.com")
        pem = cert.public_bytes(serialization.Encoding.PEM)
        body = b'{"request":{"type":"LaunchRequest"}}'
        sig_b64 = _sign_and_encode(rsa_key, body)

        with patch("core.alexa_verifier.requests.get", return_value=_mock_get(pem)):
            with pytest.raises(av.AlexaVerificationError, match="SAN"):
                av.verify_signature(body, sig_b64, VALID_CERT_URL)

    def test_rejects_signature_from_wrong_key(self, rsa_key):
        cert = _make_cert(rsa_key)
        pem = cert.public_bytes(serialization.Encoding.PEM)
        body = b'{"request":{"type":"LaunchRequest"}}'
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        sig_b64 = _sign_and_encode(other_key, body)

        with patch("core.alexa_verifier.requests.get", return_value=_mock_get(pem)):
            with pytest.raises(av.AlexaVerificationError):
                av.verify_signature(body, sig_b64, VALID_CERT_URL)


class TestFetchLeafCertificateNetworkErrors:
    """Issue #179の回帰テスト: requests.get/raise_for_statusが送出する
    requests.exceptions.RequestException(Timeout/ConnectionError/HTTPError等)は
    AlexaVerificationErrorではないため、router側のexcept AlexaVerificationErrorを
    素通りしグローバル例外ハンドラ経由で500になっていた。証明書チェーン取得失敗も
    AlexaVerificationErrorに変換し、routerが本来意図する400を返せるようにする。"""

    def test_connection_error_is_converted_to_verification_error(self):
        with patch(
            "core.alexa_verifier.requests.get",
            side_effect=requests.exceptions.ConnectionError("connection refused"),
        ):
            with pytest.raises(av.AlexaVerificationError):
                av.verify_signature(b"body", "sig", VALID_CERT_URL)

    def test_timeout_is_converted_to_verification_error(self):
        with patch(
            "core.alexa_verifier.requests.get",
            side_effect=requests.exceptions.Timeout("timed out"),
        ):
            with pytest.raises(av.AlexaVerificationError):
                av.verify_signature(b"body", "sig", VALID_CERT_URL)

    def test_http_error_status_is_converted_to_verification_error(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("404")

        with patch("core.alexa_verifier.requests.get", return_value=resp):
            with pytest.raises(av.AlexaVerificationError):
                av.verify_signature(b"body", "sig", VALID_CERT_URL)


class TestVerifyTimestamp:
    def test_accepts_current_timestamp(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        av.verify_timestamp(now.isoformat().replace("+00:00", "Z"))

    def test_rejects_stale_timestamp(self):
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=500)
        with pytest.raises(av.AlexaVerificationError):
            av.verify_timestamp(old.isoformat().replace("+00:00", "Z"))

    def test_rejects_future_timestamp_beyond_tolerance(self):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=500)
        with pytest.raises(av.AlexaVerificationError):
            av.verify_timestamp(future.isoformat().replace("+00:00", "Z"))

    def test_rejects_malformed_timestamp(self):
        with pytest.raises(av.AlexaVerificationError):
            av.verify_timestamp("not-a-timestamp")

    def test_rejects_timestamp_without_timezone_as_verification_error_not_typeerror(self):
        """Issue #110回帰防止: datetime.fromisoformat はタイムゾーン情報のない
        ISO文字列(例: "2026-08-30T00:00:00")もパース成功として受理してしまう
        (ValueError/AttributeErrorにならない)ため、以前はその後の
        `now(aware) - ts(naive)` がTypeErrorを送出していた。ルーターは
        AlexaVerificationErrorのみを捕捉するため、この経路だけ400ではなく
        500になっていた(グローバル例外ハンドラ経由)。"""
        naive_timestamp = datetime.datetime.now().isoformat()
        with pytest.raises(av.AlexaVerificationError):
            av.verify_timestamp(naive_timestamp)
