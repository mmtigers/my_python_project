# MY_HOME_SYSTEM/views/dashboard/health_tab.py
import streamlit as st
import pandas as pd

def render(df_child: pd.DataFrame, df_poop: pd.DataFrame, df_food: pd.DataFrame):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 🏥 子供")
        if not df_child.empty:
            st.dataframe(df_child[["timestamp", "child_name", "condition"]], width="stretch")
    with c2:
        st.markdown("##### 💩 排便")
        if not df_poop.empty:
            st.dataframe(df_poop[["timestamp", "user_name", "condition"]], width="stretch")
    st.markdown("##### 🍽️ 食事")
    if not df_food.empty:
        st.dataframe(df_food[["timestamp", "menu_category"]], width="stretch")