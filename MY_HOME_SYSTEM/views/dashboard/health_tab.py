# MY_HOME_SYSTEM/views/dashboard/health_tab.py
import streamlit as st
import pandas as pd

from . import common as view_common

def render(df_child: pd.DataFrame, df_poop: pd.DataFrame, df_food: pd.DataFrame):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 🏥 子供")
        if not df_child.empty:
            view_common.render_table(df_child, {"timestamp": "時刻", "child_name": "名前", "condition": "様子"})
    with c2:
        st.markdown("##### 💩 排便")
        if not df_poop.empty:
            view_common.render_table(df_poop, {"timestamp": "時刻", "user_name": "名前", "condition": "様子"})
    st.markdown("##### 🍽️ 食事")
    if not df_food.empty:
        view_common.render_table(df_food, {"timestamp": "時刻", "menu_category": "メニュー"})