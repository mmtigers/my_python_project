# MY_HOME_SYSTEM/core/cf_access.py
"""
Cloudflare Access (Zero Trust) が付与する Cf-Access-Jwt-Assertion ヘッダーの検証。

エッジ(Cloudflare)で認証済みのリクエストには、チームドメインの公開鍵(JWKS)で
署名されたJWTが付与される。オリジン側でこの署名・aud・iss・有効期限を検証する
ことで、「Cloudflare Accessを通過した本物のリクエストか」を担保する。
公開鍵は https://<team_domain>/cdn-cgi/access/certs から取得し、PyJWKClientが
キャッシュする(検証自体はローカル計算で、毎リクエストのネットワークアクセスは発生しない)。
"""
from typing import Any, Dict, Optional

import jwt
from jwt import PyJWKClient


class CloudflareAccessVerifier:
    """Cloudflare AccessのJWTを検証する。スレッドセーフ性はPyJWKClientのキャッシュに依存
    するため、呼び出し側は asyncio.to_thread 等で直列/並列いずれで呼んでも問題ない。"""

    def __init__(self, team_domain: str, audience: str):
        self.team_domain = team_domain
        self.audience = audience
        self._jwks_client: Optional[PyJWKClient] = None

    @property
    def configured(self) -> bool:
        return bool(self.team_domain and self.audience)

    def _get_signing_key(self, token: str) -> Any:
        # 遅延初期化: モジュールimport時にネットワーク設定へ依存しないようにする
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(
                f"https://{self.team_domain}/cdn-cgi/access/certs",
                cache_keys=True,
                lifespan=3600,
            )
        return self._jwks_client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> Dict[str, Any]:
        """検証に成功したらクレーム(email等を含む)を返す。失敗は jwt.PyJWTError を送出。
        JWKS取得失敗などのネットワーク起因エラーはその他のExceptionとして送出される。"""
        signing_key = self._get_signing_key(token)
        return jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=f"https://{self.team_domain}",
            options={"require": ["exp", "iat"]},
            leeway=30,
        )
