# MY_HOME_SYSTEM/core/alexa_verifier.py
"""
AlexaスキルをカスタムのWebサービスエンドポイントとしてホストする際に必須の
リクエスト検証(署名 + タイムスタンプ)。

ask-sdk-webservice-support 同梱の検証器は certvalidator -> oscrypto 経由で
libcrypto を動的ロードしようとするが、oscryptoはOpenSSL 3.x環境(Raspberry Pi OS
Bookworm等)でロードに失敗することがある(既知の未解決issue)。本番のPi上で
import時に落ちるリスクを避けるため、このプロジェクトでは Alexa公式ドキュメント
記載の検証手順を、既存依存の `cryptography` と `requests` だけで自前実装する。
https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-a-web-service.html

検証手順:
    1. SignatureCertChainUrl が Amazon純正のURL形式か(scheme/host/path/port)
    2. そのURLから証明書チェーンを取得し、先頭(リーフ)証明書を使う
    3. リーフ証明書が有効期限内であること
    4. リーフ証明書のSANに "echo-api.amazon.com" が含まれること
    5. Signatureヘッダ(base64)を、リーフ証明書の公開鍵 + SHA1withRSA でリクエスト
       生ボディに対して検証する
    6. リクエストJSON内の request.timestamp が現在時刻から一定範囲内であること
       (リプレイ攻撃対策)

なお、証明書チェーンの取得自体はHTTPS(s3.amazonaws.com、通常のCA検証あり)経由で
行われ、かつURLがAmazon管理下のパスに固定されるため、ルートCAまでの
チェーン構築(full path validation)は行っていない。
"""
import time
import base64
import logging
import posixpath
from datetime import datetime, timezone
from typing import Dict, Tuple
from urllib.parse import urlparse, unquote

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature

logger = logging.getLogger("core.alexa_verifier")

CERT_CHAIN_URL_SCHEME = "https"
CERT_CHAIN_URL_HOSTNAME = "s3.amazonaws.com"
CERT_CHAIN_URL_PATH_PREFIX = "/echo.api/"
CERT_CHAIN_URL_PORT = 443
REQUIRED_SAN = "echo-api.amazon.com"
TIMESTAMP_TOLERANCE_SECONDS = 150
CERT_CACHE_TTL_SECONDS = 60 * 60


class AlexaVerificationError(Exception):
    """署名・証明書・タイムスタンプの検証に失敗した"""


_cert_cache: Dict[str, Tuple[x509.Certificate, float]] = {}


def _validate_cert_chain_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != CERT_CHAIN_URL_SCHEME:
        raise AlexaVerificationError(f"Invalid SignatureCertChainUrl scheme: {parsed.scheme!r}")
    if (parsed.hostname or "").lower() != CERT_CHAIN_URL_HOSTNAME:
        raise AlexaVerificationError(f"Invalid SignatureCertChainUrl host: {parsed.hostname!r}")
    # #173: Amazon公式の検証手順は「URLパスを正規化した後に/echo.api/で始まること」を
    # 要求している(公式SDKの検証器も正規化を実施)。生のparsed.pathへのstartswith判定
    # のみだと、"/echo.api/../<別バケット>/cert.pem" のような".."を含むパスがこの
    # チェックを素通りしてしまう。posixpath.normpathで正規化してから判定する。
    # #223: urlparseが返すparsed.pathはパーセントデコードされない生文字列のため、
    # "/echo.api/%2e%2e/evil-bucket/cert.pem" のようなエンコード済みの".."は
    # normpathでも検知できず素通りしてしまう。一方、後段の_fetch_leaf_certificateが
    # 同じURL文字列をrequests.get()に渡すと、requestsは送信前に%2e%2eを..へデコード
    # するため、検証時と実際の取得先が食い違う。requestsの挙動に合わせ、normpathへ
    # 渡す前にunquoteで一度だけパーセントデコードする。
    normalized_path = posixpath.normpath(unquote(parsed.path))
    if not normalized_path.startswith(CERT_CHAIN_URL_PATH_PREFIX):
        raise AlexaVerificationError(f"Invalid SignatureCertChainUrl path: {parsed.path!r}")
    if (parsed.port or CERT_CHAIN_URL_PORT) != CERT_CHAIN_URL_PORT:
        raise AlexaVerificationError(f"Invalid SignatureCertChainUrl port: {parsed.port!r}")


def _fetch_leaf_certificate(cert_chain_url: str) -> x509.Certificate:
    now = time.time()
    cached = _cert_cache.get(cert_chain_url)
    if cached and cached[1] > now:
        return cached[0]

    # #179: requests.get/raise_for_status が送出する例外(Timeout/ConnectionError/
    # HTTPError等の requests.exceptions.RequestException)はAlexaVerificationErrorでは
    # ないため、router側の`except AlexaVerificationError`を素通りしFastAPIのグローバル
    # 例外ハンドラに届いて500になっていた(証明書キャッシュミス時にAmazon S3側が一時的に
    # 遅延・障害の場合に発生しうる)。ここで捕捉しAlexaVerificationErrorに変換することで
    # routerが本来意図している400を返せるようにする。
    try:
        resp = requests.get(cert_chain_url, timeout=5)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise AlexaVerificationError(f"Failed to fetch certificate chain: {exc}") from exc

    certs = x509.load_pem_x509_certificates(resp.content)
    if not certs:
        raise AlexaVerificationError("Certificate chain response is empty")

    leaf = certs[0]
    _cert_cache[cert_chain_url] = (leaf, now + CERT_CACHE_TTL_SECONDS)
    return leaf


def verify_signature(raw_body: bytes, signature_b64: str, cert_chain_url: str) -> None:
    """SignatureヘッダとSignatureCertChainUrlヘッダを使ってリクエストボディを検証する。

    検証失敗時は AlexaVerificationError を送出する。
    """
    if not signature_b64 or not cert_chain_url:
        raise AlexaVerificationError("Missing Signature or SignatureCertChainUrl header")

    _validate_cert_chain_url(cert_chain_url)
    leaf_cert = _fetch_leaf_certificate(cert_chain_url)

    now = datetime.now(timezone.utc)
    if not (leaf_cert.not_valid_before_utc <= now <= leaf_cert.not_valid_after_utc):
        raise AlexaVerificationError("Signing certificate is expired or not yet valid")

    san_ext = leaf_cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san_ext.value.get_values_for_type(x509.DNSName)
    if REQUIRED_SAN not in dns_names:
        raise AlexaVerificationError(f"Signing certificate SAN does not include {REQUIRED_SAN}")

    try:
        signature = base64.b64decode(signature_b64)
    except (ValueError, TypeError) as exc:
        raise AlexaVerificationError(f"Invalid base64 Signature header: {exc}") from exc

    public_key = leaf_cert.public_key()
    try:
        # SHA1withRSA はAmazon Alexaの署名アルゴリズム仕様で固定されており、
        # このプロジェクトが選択したものではない(選べない)。
        public_key.verify(signature, raw_body, padding.PKCS1v15(), hashes.SHA1())  # nosec B303
    except InvalidSignature as exc:
        raise AlexaVerificationError("Request signature does not match body") from exc


def verify_timestamp(request_timestamp: str, tolerance_seconds: int = TIMESTAMP_TOLERANCE_SECONDS) -> None:
    """リクエストJSON内の request.timestamp がリプレイ攻撃対策の許容範囲内であることを確認する。"""
    try:
        ts = datetime.fromisoformat(request_timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise AlexaVerificationError(f"Invalid request timestamp: {request_timestamp!r}") from exc

    if ts.tzinfo is None:
        # datetime.fromisoformat はタイムゾーン情報のないISO文字列(例: "2026-08-30T00:00:00")
        # もパース成功として受理してしまう(ValueErrorにならない)。Alexaのrequest.timestampは
        # 常にタイムゾーン付き(Z or +00:00)のISO 8601形式のため、これは仕様外の不正な形式として
        # AlexaVerificationErrorを送出する。ここでガードしないと、後続の
        # `now(aware) - ts(naive)` がTypeErrorを送出し、ルーターがAlexaVerificationErrorのみを
        # 捕捉するため500(本来返すべきは400)になっていた。
        raise AlexaVerificationError(f"Request timestamp missing timezone info: {request_timestamp!r}")

    now = datetime.now(timezone.utc)
    delta = abs((now - ts).total_seconds())
    if delta > tolerance_seconds:
        raise AlexaVerificationError(f"Request timestamp outside tolerance window ({delta:.0f}s)")
