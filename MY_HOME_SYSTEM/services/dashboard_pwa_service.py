# MY_HOME_SYSTEM/services/dashboard_pwa_service.py
"""
ダッシュボード(`routers/dashboard_router.py`)をスマートフォンのホーム画面に
追加するための付帯物(PWAマニフェスト・アイコン)。

#829: 以前は Streamlit 版ダッシュボード(`dashboard.py`)への逆プロキシを担う
`services/dashboard_proxy_service.py` の一部だったが、Streamlit版を廃止し
`routers/dashboard_router.py` 配下のページ(この`unified_server`が直接返す
HTML)だけになったため、プロキシ機構(httpx/websocketsによるHTTP・WebSocket中継)は
不要になった。マニフェスト・アイコン生成だけを独立したこのモジュールへ残す。
"""
import io
from functools import lru_cache

import config

# ホーム画面に追加したときの表示名・配色。
DASHBOARD_APP_NAME = "My Home Dashboard"
DASHBOARD_APP_SHORT_NAME = "おうち"
DASHBOARD_THEME_COLOR = "#0d47a1"
DASHBOARD_BACKGROUND_COLOR = "#ffffff"

# PWAマニフェストとapple-touch-iconで参照するアイコンのサイズ(px)。
DASHBOARD_ICON_SIZES = (180, 192, 512)


def build_dashboard_manifest() -> dict:
    """ホーム画面に追加するためのWebアプリマニフェストを組み立てる。

    `start_url` / `scope` を `config.DASHBOARD_BASE_PATH` 配下に閉じることで、
    ホーム画面から開いたときにダッシュボードだけがアプリとして開く
    (`/quest` のPWAとは別アイコン・別スコープになる)。
    """
    base = config.DASHBOARD_BASE_PATH
    return {
        "name": DASHBOARD_APP_NAME,
        "short_name": DASHBOARD_APP_SHORT_NAME,
        "lang": "ja",
        "start_url": f"{base}/",
        "scope": f"{base}/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": DASHBOARD_BACKGROUND_COLOR,
        "theme_color": DASHBOARD_THEME_COLOR,
        "icons": [
            {
                "src": f"{base}/icon-{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any",
            }
            for size in DASHBOARD_ICON_SIZES
        ],
    }


@lru_cache(maxsize=len(DASHBOARD_ICON_SIZES))
def render_dashboard_icon_png(size: int) -> bytes:
    """ホーム画面アイコンのPNGを生成する(サイズごとに1回だけ描画してキャッシュ)。

    絵文字やフォントに頼ると、実機のフォント事情で崩れたり豆腐になったりする。
    図形(屋根の三角・本体の四角・窓)だけで描く。
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), DASHBOARD_THEME_COLOR)
    draw = ImageDraw.Draw(image)
    unit = size / 16

    # 屋根
    draw.polygon(
        [(unit * 8, unit * 3), (unit * 14, unit * 8), (unit * 2, unit * 8)],
        fill="#ffffff",
    )
    # 本体
    draw.rectangle([unit * 4, unit * 8, unit * 12, unit * 13], fill="#ffffff")
    # 窓(本体をくり抜いて見せる)
    draw.rectangle([unit * 7, unit * 10, unit * 9, unit * 13], fill=DASHBOARD_THEME_COLOR)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
