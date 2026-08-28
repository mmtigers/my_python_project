# MY_HOME_SYSTEM/tests/test_cf_access_middleware.py
"""
access_control_middleware の Cloudflare Access JWT検証まわりのテスト。

方針:
- 信頼判定は実際のTCP接続元 (request.client.host) で行われるため、
  TestClient の client= パラメータで接続元を偽装してLAN/外部を切り替える。
- Cloudflare Tunnel経由のリクエストは「ループバック接続 + cf-connecting-ip
  ヘッダーあり」として届くため、その組み合わせで再現する。
- JWTは実際にRSA鍵で署名し、JWKS取得部分 (_get_signing_key) のみモックする。
  署名検証・aud・iss・exp の検証は PyJWT の本物のコードパスを通す。
"""
import os
import sys
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import unified_server

# 検証対象の設定値(config経由でミドルウェアに渡っているもの)をそのまま使う
TEAM_DOMAIN = unified_server.cf_access_verifier.team_domain
AUDIENCE = unified_server.cf_access_verifier.audience
ISSUER = f"https://{TEAM_DOMAIN}"

TUNNEL_HEADERS = {"cf-connecting-ip": "198.51.100.7"}


@pytest.fixture(scope="module")
def rsa_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def patched_signing_key(rsa_keys, monkeypatch):
    """JWKS取得をスキップし、テスト用RSA公開鍵を返すようにする"""
    _, public_key = rsa_keys
    monkeypatch.setattr(
        unified_server.cf_access_verifier, "_get_signing_key", lambda token: public_key
    )
    return public_key


def _make_token(private_key, *, aud=AUDIENCE, iss=ISSUER, expires_in=300, alg="RS256", **extra):
    now = int(time.time())
    claims = {
        "aud": aud,
        "iss": iss,
        "iat": now,
        "exp": now + expires_in,
        "email": "dad@example.com",
        **extra,
    }
    return jwt.encode(claims, private_key, algorithm=alg)


def _tunnel_client() -> TestClient:
    """cloudflared同居構成: ループバックから接続し cf-connecting-ip が付与される"""
    return TestClient(unified_server.app, client=("127.0.0.1", 50000))


def _public_client() -> TestClient:
    """トンネルを介さず公開アドレスから直接届いた想定。
    ※TEST-NET系(203.0.113.0/24等)はPythonのis_privateがTrueを返すため、
    実在のグローバルIPを使う必要がある。"""
    return TestClient(unified_server.app, client=("8.8.8.8", 50000))


class TestExternalRequestsRequireJwt:
    def test_tunnel_request_without_jwt_is_rejected(self):
        res = _tunnel_client().get("/health", headers=TUNNEL_HEADERS)
        assert res.status_code == 403

    def test_public_peer_without_jwt_is_rejected(self):
        res = _public_client().get("/health")
        assert res.status_code == 403

    def test_unparsable_peer_without_headers_is_rejected(self):
        """接続元IPが解釈できない場合は内部扱いせずfail-closedで拒否する"""
        res = TestClient(unified_server.app).get("/health")  # client=("testclient", 50000)
        assert res.status_code == 403

    def test_lan_peer_with_spoofed_cf_header_requires_jwt(self):
        """LAN内からでも cf-connecting-ip を自称するリクエストはトンネル経由とみなしJWTを要求する"""
        client = TestClient(unified_server.app, client=("192.168.1.50", 50000))
        res = client.get("/health", headers=TUNNEL_HEADERS)
        assert res.status_code == 403

    def test_webhook_paths_still_bypass_jwt_check(self):
        """Webhook例外パスはJWTなしでもミドルウェアを通過する(各ハンドラが署名検証する)"""
        res = _tunnel_client().post("/callback/line", content=b"{}", headers=TUNNEL_HEADERS)
        assert res.status_code != 403


class TestJwtVerification:
    def test_valid_jwt_is_accepted(self, rsa_keys, patched_signing_key):
        private_key, _ = rsa_keys
        token = _make_token(private_key)
        res = _tunnel_client().get(
            "/health", headers={**TUNNEL_HEADERS, "cf-access-jwt-assertion": token}
        )
        assert res.status_code == 200

    def test_expired_jwt_is_rejected(self, rsa_keys, patched_signing_key):
        private_key, _ = rsa_keys
        # leeway=30秒を考慮し、確実に期限切れとなる過去を指定
        token = _make_token(private_key, expires_in=-3600)
        res = _tunnel_client().get(
            "/health", headers={**TUNNEL_HEADERS, "cf-access-jwt-assertion": token}
        )
        assert res.status_code == 403

    def test_wrong_audience_is_rejected(self, rsa_keys, patched_signing_key):
        private_key, _ = rsa_keys
        token = _make_token(private_key, aud="another-application-aud-tag")
        res = _tunnel_client().get(
            "/health", headers={**TUNNEL_HEADERS, "cf-access-jwt-assertion": token}
        )
        assert res.status_code == 403

    def test_wrong_issuer_is_rejected(self, rsa_keys, patched_signing_key):
        private_key, _ = rsa_keys
        token = _make_token(private_key, iss="https://evil.cloudflareaccess.com")
        res = _tunnel_client().get(
            "/health", headers={**TUNNEL_HEADERS, "cf-access-jwt-assertion": token}
        )
        assert res.status_code == 403

    def test_wrong_signing_key_is_rejected(self, patched_signing_key):
        """正規と異なる鍵で署名されたトークン(署名偽造)は拒否される"""
        attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = _make_token(attacker_key)
        res = _tunnel_client().get(
            "/health", headers={**TUNNEL_HEADERS, "cf-access-jwt-assertion": token}
        )
        assert res.status_code == 403

    def test_garbage_token_is_rejected(self, patched_signing_key):
        res = _tunnel_client().get(
            "/health", headers={**TUNNEL_HEADERS, "cf-access-jwt-assertion": "not-a-jwt"}
        )
        assert res.status_code == 403

    def test_jwks_fetch_failure_returns_503(self, rsa_keys, monkeypatch):
        """公開鍵の取得に失敗した場合は攻撃と区別して503(fail-closed)"""
        private_key, _ = rsa_keys

        def _network_down(token):
            raise ConnectionError("JWKS endpoint unreachable")

        monkeypatch.setattr(
            unified_server.cf_access_verifier, "_get_signing_key", _network_down
        )
        token = _make_token(private_key)
        res = _tunnel_client().get(
            "/health", headers={**TUNNEL_HEADERS, "cf-access-jwt-assertion": token}
        )
        assert res.status_code == 503

    def test_unconfigured_verifier_rejects_tunnel_traffic(self, rsa_keys, patched_signing_key, monkeypatch):
        """CF_ACCESS_AUDが空に設定された場合、検証をスキップせず拒否する(fail-closed)"""
        private_key, _ = rsa_keys
        token = _make_token(private_key)
        monkeypatch.setattr(unified_server.cf_access_verifier, "audience", "")
        res = _tunnel_client().get(
            "/health", headers={**TUNNEL_HEADERS, "cf-access-jwt-assertion": token}
        )
        assert res.status_code == 403


class TestLanUnaffected:
    def test_lan_peer_without_cf_headers_passes(self):
        client = TestClient(unified_server.app, client=("192.168.1.50", 50000))
        res = client.get("/health")
        assert res.status_code == 200

    def test_loopback_without_cf_headers_passes(self):
        res = _tunnel_client().get("/health")
        assert res.status_code == 200
