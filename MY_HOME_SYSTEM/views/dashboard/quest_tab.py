# MY_HOME_SYSTEM/views/dashboard/quest_tab.py
import streamlit as st
import pandas as pd
import plotly.express as px
from services.quest_service import game_system

def render():
    """Family Questの状況を表示するタブ"""
    st.title("⚔️ Family Quest 現在の状況")
    
    try:
        data = game_system.get_all_view_data()
        users = data.get('users', [])
        logs = data.get('logs', [])
        
        # 経験値降順ソート
        users.sort(key=lambda x: x['exp'], reverse=True)

        if not users:
            st.info("データがありません。")
            return

        cols = st.columns(len(users))
        for i, u in enumerate(users):
            with cols[i]:
                rank_icon = "👑" if i == 0 else "🛡️"
                st.metric(
                    label=f"{rank_icon} {u['name']} ({u['job_class']})",
                    value=f"{u['exp']} EXP",
                    delta=f"{u['gold']} G"
                )

        st.divider()

        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.subheader("📊 経験値ランキング")
            df_quest = pd.DataFrame(users)
            if not df_quest.empty:
                df_quest.rename(columns={"name": "名前", "exp": "経験値", "job_class": "職業"}, inplace=True)
                fig = px.bar(
                    df_quest, 
                    x="名前", 
                    y="経験値", 
                    color="職業", 
                    text="経験値",
                    title="現在のレベル状況"
                )
                fig.update_traces(textposition='outside')
                st.plotly_chart(fig, width="stretch")

        with col2:
            st.subheader("📜 最近の達成履歴")
            if logs:
                # logsは {'text':..., 'dateStr':...} のリスト
                # 直近5件を表示
                for log in logs[:5]:
                    st.markdown(f"**{log['text']}** \n<span style='color:grey; font-size:0.8em'>({log['timestamp']})</span>", unsafe_allow_html=True)
                    st.write("---")
            else:
                st.write("まだ冒険の記録がありません")

    except Exception as e:
        st.error(f"クエスト情報の読み込みに失敗しました: {e}")