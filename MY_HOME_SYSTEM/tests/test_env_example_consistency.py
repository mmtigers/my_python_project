"""config.py と .env.example の突き合わせテスト。

CLAUDE.md の規約「新しい外部連携の認証情報/URLを config.py に追加したら
.env.example にもプレースホルダーのエントリを追加すること」を機械的に検証する。

2026-09 の残件調査で、config.py が読む43変数のうち42変数が .env.example に
載っておらず(SWITCHBOT_WEBHOOK_TOKEN 等)、逆に削除済み機能(financial_service)の
FINANCIAL_* 12変数だけが残っている状態が見つかったため、再発防止として追加した。

このテストは config.py を import せず、テキストとして解析する
(import すると .env や NAS への副作用があるため)。
"""

import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / "config.py"
ENV_EXAMPLE_PATH = BASE_DIR / ".env.example"

# os.getenv("KEY") / os.getenv('KEY', default) の第1引数を拾う
_GETENV_RE = re.compile(r"""os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)["']""")
# .env.example の非コメント行 "KEY=..." を拾う
_ENV_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)


def _config_env_keys() -> set[str]:
    return set(_GETENV_RE.findall(CONFIG_PATH.read_text(encoding="utf-8")))


def _example_env_keys() -> set[str]:
    return set(_ENV_LINE_RE.findall(ENV_EXAMPLE_PATH.read_text(encoding="utf-8")))


def test_config_getenv_keys_are_all_documented_in_env_example():
    """config.py が読む環境変数はすべて .env.example に載っていること。"""
    missing = sorted(_config_env_keys() - _example_env_keys())
    assert not missing, (
        "config.py が os.getenv() で読んでいるのに .env.example に載っていない変数があります: "
        f"{missing} — .env.example にプレースホルダー値のエントリを追加してください"
        "(実際の値・秘密情報は書かないこと)。"
    )


def test_env_example_has_no_stale_keys():
    """.env.example に config.py が読まない変数(削除済み機能の残骸など)が残っていないこと。"""
    stale = sorted(_example_env_keys() - _config_env_keys())
    assert not stale, (
        ".env.example に config.py が読んでいない変数が残っています: "
        f"{stale} — 機能ごと削除したなら .env.example からも削除してください。"
        "config.py 以外のモジュールで直接 os.getenv する設計に変えた場合は、"
        "このテストの走査対象を見直すこと。"
    )
