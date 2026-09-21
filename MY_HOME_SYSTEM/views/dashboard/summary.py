# MY_HOME_SYSTEM/views/dashboard/summary.py
"""ホームタブのサマリー(ステータスカード)の描画。

各カードの判定ロジックとHTMLの組み立ては `services/home_status_service.py` に
ある。Streamlit を介さない軽量ページ(`/dashboard/m`)が同じ内容を出すため、
判定を2箇所に持たないようにした経緯は同モジュールのdocstringを参照。
ここに残るのは「Streamlit のキャッシュ付きローダで材料を集めて描く」ことだけ。
"""
from datetime import datetime

import pandas as pd
import streamlit as st
from services import home_status_service

from . import common as view_common

# === Render Function ===

def render_summary(
    now: datetime,
    df_sensor: pd.DataFrame,
    df_car: pd.DataFrame,
    df_bicycle: pd.DataFrame,
    nas_data: pd.Series | None,
):
    """トップ画面サマリー描画"""
    cards = home_status_service.build_status_cards(
        now,
        df_sensor,
        df_car,
        df_bicycle,
        nas_data,
        # スクレイピング・psutil・集計SQLはキャッシュ付きラッパー経由で取る
        # (素で呼ぶと1回の描画で取り直しになる。tests/test_dashboard_cache.py)
        jr_status=view_common.load_jr_traffic_status_cached(),
        memory=view_common.get_memory_usage_cached(),
        monthly_cost=view_common.get_monthly_cost_cached(),
        # 補足表示(値の下の小さい行)にだけ使う材料。取れなくてもカードは出る。
        last_month_cost=view_common.get_last_month_cost_cached(),
        disk=view_common.get_disk_usage_cached(),
    )

    # いま気にすべきものを先頭に1行で出す。9枚の並び自体は動かさない
    # (どの位置に何があるかで覚えている画面で順番が入れ替わると読み違えるため)。
    # 拾う条件は軽量ページと同じ `home_status_service.summarize_alerts` に任せる
    # ので、ここに判定は持たない。
    alerts = home_status_service.summarize_alerts(cards)
    if alerts:
        st.warning("⚠️ 気になること: " + "、".join(card.title for card in alerts))
    else:
        st.success("✅ 気になることはありません")

    # スマホ対応: 以前は st.columns(3) を3段重ねて9枚を並べていたが、
    # Streamlitの列は画面幅が足りなくても横並びを維持するため、スマートフォンでは
    # 1枚あたり約100pxまで潰れて値が読めなかった。列数の決定はCSS Grid側
    # (`views/dashboard/common.py` の `.status-grid`)に委ね、スマホ2列・PC3〜5列に
    # 自動で切り替わるようにする。
    view_common.render_status_grid(cards)
