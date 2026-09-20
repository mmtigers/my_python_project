# MY_HOME_SYSTEM/core/security_posture.py
"""「設定が未設定なせいで保護が黙って無効になっている」状態を1か所で列挙する (Issue #799)。

## なぜ必要か

このリポジトリでは「コードはマージされ Issue もクローズされたのに、実機 `.env` への
反映が漏れていて保護が効いていない」という取りこぼしが**3回**起きている。

- #319 — `ALEXA_SKILL_ID` 未設定で Alexa のスキルID検証がフェイルオープン(現在も未対応)
- #723 — `CORS_EXTRA_ORIGINS` / `FRONTEND_URL` 未設定(2026-09-20 に対応)
- #799 — `AUTHORIZED_LINE_USER_IDS` 未設定で LINE 送信者 allowlist がフェイルオープン

個別の警告自体は既に存在した(`handlers/alexa_handler.py` の
「⚠️ ALEXA_SKILL_ID is not set」、`unified_server.py` の SwitchBot トークン警告)。
それでも気づかれなかったのは、

1. **散らばっていて「いま何が無効なのか」を一覧で答えられない**
2. **ログにしか出ない。** 起動ログを毎回読む運用ではないため、数か月気づかれない

の2点が理由である。したがってここでは「**判定を1か所に集め、起動レポート
(Discord) に載せる**」ことを目的にする。実際の通知は
`post_boot_health_check.py` が行う(このモジュールは副作用を持たない)。

## 対象にするもの・しないもの

対象は「**未設定だと保護が黙って無効になる(フェイルオープン)**」設定だけに絞る。

意図的に未設定のまま運用している設定は**対象にしない**。混ぜると警告が常時鳴り、
「いつものやつ」として無視されるようになり、1. の一覧性という目的自体が壊れる。
2026-09-20 時点で意図的に未設定なのは次のとおり:

- `NAS_IP` — 未設定なら ping をスキップしマウント/書き込みで判定する(#663 の設計)
- `SPEAKER_BLUETOOTH_MAC` — `ENABLE_BLUETOOTH=False` で運用休止中(#723)
- `YOUTUBE_COOKIES_FILE` — cookie 無し運用と決めた(#768)
- `DB_BACKUP_OFFSITE_REMOTE` — rclone の OAuth 設定待ちで一時無効化中(#721)

同様に「未設定だと**フェイルクローズ**する」ものも対象外である。
`SWITCHBOT_WEBHOOK_TOKEN` 単独の未設定は #648 で 503 拒否になったため、
保護が外れるのではなく機能が止まる。ここで報告するのは、移行用オプトイン
`ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK` が立っていて**無検証で受け付ける**場合だけ。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 呼び出し側が差し替えられるよう、既定値としてだけ import する。
import config as _default_config


@dataclass(frozen=True)
class PostureFinding:
    """保護が無効になっている設定1件。

    `key` は `.env` の設定名、`protection` は**いま何が効いていないか**、
    `remedy` は直し方、`ref` は経緯を追うための Issue 番号。
    メッセージを組み立てるのは呼び出し側(通知の文面はそこの都合で変わるため)。
    """

    key: str
    protection: str
    remedy: str
    ref: str


def _is_blank(value: Any) -> bool:
    """未設定とみなすか。空文字・None・空リストを同じ「未設定」として扱う。

    `AUTHORIZED_LINE_USER_IDS` は `List[str]`、`ALEXA_SKILL_ID` は `str | None` と
    型が揃っていないため、ここで吸収する。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def check_security_posture(cfg: Any = None) -> list[PostureFinding]:
    """フェイルオープンしている設定を列挙する。副作用は無く、ログも通知も出さない。

    Args:
        cfg: 検査対象の設定モジュール。既定は `config`。テストでは任意の
            オブジェクト(`SimpleNamespace` 等)を渡せる。

    Returns:
        見つかった `PostureFinding` のリスト。問題が無ければ空リスト。
    """
    if cfg is None:
        cfg = _default_config

    findings: list[PostureFinding] = []

    # 1. Alexa: スキルID検証 (#319)
    #    未設定だと `sb.skill_id` を設定しないため、**どのスキルからのリクエストでも
    #    受け付ける**。`handlers/alexa_handler.py` が import 時に警告を出している。
    if _is_blank(getattr(cfg, "ALEXA_SKILL_ID", None)):
        findings.append(PostureFinding(
            key="ALEXA_SKILL_ID",
            protection="Alexa のスキルID検証が無効(どのスキルからのリクエストも受理)",
            remedy="Amazon Developer Console で発行したスキルIDを .env に設定して再起動する",
            ref="#319",
        ))

    # 2. LINE: 送信者 allowlist (#620 / #799)
    #    空だと `_is_authorized_line_user` が後方互換で常に True を返す。LINE 公式
    #    アカウントは友だち追加すれば誰でも送信できるため、第三者が体調・食事記録の
    #    書き込みと AI 経由の DB 検索を行えてしまう。
    if _is_blank(getattr(cfg, "AUTHORIZED_LINE_USER_IDS", None)):
        findings.append(PostureFinding(
            key="AUTHORIZED_LINE_USER_IDS",
            protection="LINE 送信者の allowlist が無効(友だち追加した第三者も記録書き込み・AI検索が可能)",
            remedy="家族の LINE user_id をカンマ区切りで .env に設定して再起動する",
            ref="#799",
        ))

    # 3. SwitchBot: Webhook の無検証受け入れ (#648)
    #    トークン未設定**単独**はフェイルクローズ(503)なので報告しない。移行用の
    #    オプトインが立っている場合だけ、無検証で受け付ける=フェイルオープンになる。
    token_blank = _is_blank(getattr(cfg, "SWITCHBOT_WEBHOOK_TOKEN", None))
    opt_in = bool(getattr(cfg, "ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK", False))
    if token_blank and opt_in:
        findings.append(PostureFinding(
            key="ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK",
            protection="/webhook/switchbot が無検証でリクエストを受理(移行用オプトインが有効)",
            remedy="SWITCHBOT_WEBHOOK_TOKEN を設定し、このオプトインを .env から削除する",
            ref="#648",
        ))

    # 4. CORS 全開放
    #    緊急避難用のフラグ。恒常運用で立てっぱなしにしないための検出
    #    (config.py のコメントが「恒常運用では使わない」と明記している)。
    if bool(getattr(cfg, "ALLOW_ALL_ORIGINS", False)):
        findings.append(PostureFinding(
            key="ALLOW_ALL_ORIGINS",
            protection="CORS が全オリジン許可(緊急避難用の設定が有効なまま)",
            remedy="必要なオリジンを CORS_EXTRA_ORIGINS に列挙し、このフラグを false に戻す",
            ref="#723",
        ))

    return findings


def format_findings(findings: list[PostureFinding]) -> str:
    """`check_security_posture` の結果を1行サマリにする。

    通知の文面を呼び出し側で組み立てやすいよう、`PostureFinding` の整形だけを担う。
    """
    if not findings:
        return "保護が無効な設定はありません"
    parts = [f"{f.key}({f.ref})" for f in findings]
    return f"{len(findings)}件の保護が無効: " + ", ".join(parts)
