# MY_HOME_SYSTEM/views/dashboard/misc_tab.py
"""「見守り」タブのカメラ関連(ギャラリーと防犯ログ)の描画。

かつては電車の運行情報・ルート検索・駐輪場の待機数もここにあり、それらを
まとめた「おでかけ」タブの描画モジュールだった。いずれも使われなくなったため
退役させ(オーナー判断)、カメラまわりだけが残っている。
"""
import streamlit as st
import pandas as pd
import os
import glob

import config
from . import common as view_common


def render_photos(df_security_log: pd.DataFrame):
    st.subheader("🖼️ カメラ・ギャラリー")
    img_dir = os.path.join(config.ASSETS_DIR, "snapshots")
    images = sorted(glob.glob(os.path.join(img_dir, "*.jpg")), reverse=True)
    if images:
        # スマホ対応: モバイルCSSは st.columns を一律で縦積みにするため、
        # ここだけは2枚ずつ横に並べる(`.st-key-camera_gallery`)。
        # 4枚が全幅で縦に積まれると、この下の防犯ログまで数画面ぶんスクロールが要る。
        with st.container(key="camera_gallery"):
            cols_img = st.columns(4)
            for i, path in enumerate(images[:4]):
                cols_img[i].image(path, caption=os.path.basename(path), width="stretch")
        if view_common.lazy_section("📂 過去の写真", key="past_photos"):
            with st.container(key="camera_gallery_past"):
                cols_past = st.columns(4)
                for i, path in enumerate(images[4:20]):
                    cols_past[i % 4].image(path, caption=os.path.basename(path), width="stretch")
    else:
        st.info("写真なし")

    st.subheader("🛡️ 防犯ログ (検知分類)")
    if not df_security_log.empty:
        # スマホ対応: 以前は image_path(NAS上のフルパス)も列に入れており、
        # 画面幅を大きく超えて横スクロールしないと検知時刻すら読めなかった。
        # 画像そのものは上のギャラリーで見られるので、表からは落とす。
        view_common.render_table(
            df_security_log,
            {"timestamp": "検知時刻", "friendly_name": "デバイス", "classification": "検知種別"},
            relative_time=True,
        )
    else:
        st.info("不審な検知はありません")
