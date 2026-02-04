# MY_HOME_SYSTEM/financial_service.py
import pandas as pd
import numpy_financial as npf
import streamlit as st
import plotly.graph_objects as go
from datetime import date
from dateutil.relativedelta import relativedelta
import common

# ロガー設定
logger = common.setup_logging("FinancialService")

class LoanSimulator:
    def __init__(self):
        # --- 基本条件 (2024/06/27 開始) ---
        self.START_DATE = date(2024, 6, 27)
        self.TOTAL_AMOUNT = 54_000_000
        self.TOTAL_MONTHS = 416
        self.INITIAL_PAYMENT = 143_205
        
        # --- 確定している金利スケジュール ---
        # ★修正: 「2025/4以降ずっと0.975」という設定を削除し、計算ロジックに任せるように変更
        self.FIXED_RATES = [
            (date(2024, 6, 1), date(2024, 9, 30), 0.575),
            (date(2024, 10, 1), date(2025, 3, 31), 0.725),
            # ここにあった (date(2025, 4, 1), None, 0.975) を削除
        ]

    def _get_scheduled_rate(self, current_date, future_rise_rate=0.0, max_rate=2.0):
        """指定年月時点の金利を取得する"""
        
        # 1. 確定スケジュールの確認 (2025/3まで)
        for start, end, rate in self.FIXED_RATES:
            if start <= current_date:
                if end is None or current_date <= end:
                    return rate
        
        # 2. 変動予測 (2025/4/1 を基準に、毎年指定%ずつ上昇)
        base_rate = 0.975
        base_date = date(2025, 4, 1)
        
        if current_date >= base_date:
            # 経過年数 (2025=0年目, 2026=1年目...)
            years_passed = (current_date.year - base_date.year)
            
            # 上昇分を加算
            calculated_rate = base_rate + (years_passed * future_rise_rate)
            
            # 上限キャップ
            return min(calculated_rate, max_rate)
            
        return base_rate

    def calculate_schedule(self, future_rise_rate=0.05, max_future_rate=2.0):
        schedule = []
        balance = self.TOTAL_AMOUNT
        current_payment = self.INITIAL_PAYMENT
        payment_review_interval = 60 # 5年ごとに見直し
        dt = self.START_DATE
        
        for i in range(self.TOTAL_MONTHS):
            # 金利決定
            rate_percent = self._get_scheduled_rate(dt, future_rise_rate, max_future_rate)
            monthly_rate = rate_percent / 100 / 12
            
            # 利息計算
            interest = int(balance * monthly_rate)
            
            # 5年ルール（60ヶ月ごと）の見直し
            if i > 0 and i % payment_review_interval == 0:
                remaining_months = self.TOTAL_MONTHS - i
                if remaining_months > 0:
                    if monthly_rate > 0:
                        new_payment = npf.pmt(monthly_rate, remaining_months, -balance)
                    else:
                        new_payment = balance / remaining_months
                    
                    new_payment = int(new_payment)
                    upper_limit = int(current_payment * 1.25)
                    
                    # 125%ルール (激変緩和措置)
                    if new_payment > upper_limit:
                        new_payment = upper_limit
                    current_payment = new_payment

            principal_payment = current_payment - interest
            
            # 最終回または完済時の調整
            if i == self.TOTAL_MONTHS - 1 or balance + interest <= current_payment:
                current_payment = balance + interest
                principal_payment = balance
                balance = 0
            else:
                balance -= principal_payment
            
            schedule.append({
                "date": dt,
                "balance": balance,
                "payment": current_payment,
                "interest": interest,
                "principal": principal_payment,
                "rate": rate_percent
            })
            
            if balance <= 0:
                break
            dt = dt + relativedelta(months=1)
            
        return pd.DataFrame(schedule)

class AssetSimulator:
    @staticmethod
    def calculate_hybrid_growth(start_date, months, init_invest, init_cash, monthly_total_save, invest_ratio, annual_return):
        schedule = []
        current_invest = init_invest
        current_cash = init_cash
        
        monthly_rate = annual_return / 100 / 12
        monthly_invest_add = int(monthly_total_save * (invest_ratio / 100))
        monthly_cash_add = monthly_total_save - monthly_invest_add
        
        dt = start_date
        
        for i in range(months):
            # 投資資産（複利）
            profit = int(current_invest * monthly_rate)
            current_invest += profit + monthly_invest_add
            
            # 現金資産（単利/積立のみ）
            current_cash += monthly_cash_add
            
            total_asset = current_invest + current_cash
            
            schedule.append({
                "date": dt,
                "asset_balance": total_asset,
                "invest_balance": current_invest,
                "cash_balance": current_cash,
                "profit": profit
            })
            
            dt = dt + relativedelta(months=1)
            
        return pd.DataFrame(schedule)

# === UI Component ===

def render_simulation_tab():
    st.markdown("### ✨ 我が家の未来家計簿 (資産シミュレーション)")
    st.caption("現在のペースで貯金・投資を続けた場合、いつローンを追い越せるかを予測します。")

    # --- Sidebar ---
    st.sidebar.header("🛠️ シミュレーション条件")
    
    with st.sidebar.expander("📊 現在の資産内訳 (入力済)", expanded=False):
        s_cash = st.number_input("預金・現金・暗号資産", value=12341762, step=10000)
        s_stock = st.number_input("株式 (現物)", value=4790594, step=10000)
        s_trust = st.number_input("投資信託", value=15177758, step=10000)
        s_pension = st.number_input("年金 (DC/iDeCo等)", value=4109821, step=10000)
        s_point = st.number_input("ポイント・マイル", value=18192, step=1000)
        
        total_initial = s_cash + s_stock + s_trust + s_pension + s_point
        st.markdown(f"**合計: {total_initial:,} 円**")

    st.sidebar.markdown("**💰 積立・運用設定**")
    monthly_save = st.sidebar.number_input("毎月の総積立額 (円)", value=100000, step=10000, help="現金貯金と投資の合計")
    invest_ratio = st.sidebar.slider("積立の投資割合 (%)", 0, 100, 80, 5, help="積立額のうち何%を投資(NISA等)に回すか")
    asset_return = st.sidebar.slider("想定年利回り (%)", 0.0, 10.0, 5.0, 0.1, help="投資部分(株・投信・年金)の期待リターン")

    st.sidebar.markdown("**🏠 ローン変動金利**")
    future_rise = st.sidebar.slider("2026年以降の上昇率 (%/年)", 0.0, 2.0, 0.1, 0.01, help="毎年この%ずつ金利が上がると仮定します")
    max_rate_limit = st.sidebar.slider("金利上限 (%)", 1.0, 5.0, 2.5, 0.1)

    # --- Calculation ---
    init_invest = s_stock + s_trust + s_pension
    init_cash = s_cash + s_point

    loan_sim = LoanSimulator()
    df_loan = loan_sim.calculate_schedule(future_rise_rate=future_rise, max_future_rate=max_rate_limit)
    
    months_to_sim = len(df_loan) + 120
    
    df_asset = AssetSimulator.calculate_hybrid_growth(
        loan_sim.START_DATE, months_to_sim,
        init_invest, init_cash,
        monthly_save, invest_ratio, asset_return
    )

    df_merged = pd.merge(df_asset, df_loan, on="date", how="left")
    df_merged["balance"] = df_merged["balance"].fillna(0)

    # --- KPI & X-Day ---
    x_day_rows = df_merged[df_merged["asset_balance"] >= df_merged["balance"]]
    
    x_day_str = "未達"
    years_to_x = 0
    if not x_day_rows.empty:
        x_day_date = x_day_rows.iloc[0]["date"]
        x_day_str = x_day_date.strftime("%Y年%m月")
        years_to_x = (x_day_date - date.today()).days / 365

    # Display KPI Cards
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("🎉 ローンを超す日 (ゴール)", x_day_str, f"あと {years_to_x:.1f} 年" if years_to_x > 0 else None)
    kpi2.metric("💰 今の資産合計", f"{int(total_initial/10000):,}万円", "Start地点")
    kpi3.metric("🏠 ローンの残り", f"{int(loan_sim.TOTAL_AMOUNT/10000):,}万円", "現在(初期)")
    kpi4.metric("📈 10年後の貯蓄予想", f"{int(df_asset.iloc[120]['asset_balance']/10000):,}万円", f"利回り{asset_return}%")

    # --- Chart 1: Asset vs Loan Balance (万円単位) ---
    st.subheader("📊 資産とローンの推移 (単位: 万円)")
    df_chart = df_merged.copy()
    df_chart["cash_man"] = df_chart["cash_balance"] / 10000
    df_chart["invest_man"] = df_chart["invest_balance"] / 10000
    df_chart["loan_man"] = df_chart["balance"] / 10000

    fig = go.Figure()

    # 1. 資産 (積み上げ)
    fig.add_trace(go.Scatter(
        x=df_chart["date"], y=df_chart["cash_man"],
        mode='lines', name='💰 コツコツ貯金 (現金)',
        stackgroup='one', line=dict(width=0, color='#90caf9'),
        hovertemplate='%{y:,.0f}万円'
    ))
    fig.add_trace(go.Scatter(
        x=df_chart["date"], y=df_chart["invest_man"],
        mode='lines', name='📈 運用で増やすお金 (投資)',
        stackgroup='one', line=dict(width=0, color='#1e88e5'),
        hovertemplate='%{y:,.0f}万円'
    ))

    # 2. ローン (赤線)
    fig.add_trace(go.Scatter(
        x=df_chart["date"], y=df_chart["loan_man"],
        mode='lines', name='🏠 ローンの残り',
        line=dict(color='#d32f2f', width=4),
        hovertemplate='%{y:,.0f}万円'
    ))

    # X-Day Line
    if not x_day_rows.empty:
        x_ts = pd.Timestamp(x_day_rows.iloc[0]["date"]).timestamp() * 1000
        fig.add_vline(x=x_ts, line_width=1, line_dash="dash", line_color="green", annotation_text="ゴール！")

    fig.update_layout(
        title="",
        xaxis_title="年", 
        yaxis_title="金額 (万円)",
        yaxis=dict(tickformat=",d"),
        height=400, 
        margin=dict(t=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, use_container_width=True)

    # --- Chart 2: Monthly Payment Breakdown (万円単位) ---
    st.subheader("💳 毎月の返済額の内訳推移 (単位: 万円)")
    st.caption("金利が上がると、支払額(点線)が変わらなくても、赤い「利息」部分が増えて借金が減らなくなります。")

    # 万円単位に変換
    df_loan["principal_man"] = df_loan["principal"] / 10000
    df_loan["interest_man"] = df_loan["interest"] / 10000
    df_loan["payment_man"] = df_loan["payment"] / 10000

    fig_payment = go.Figure()
    
    # 元金 (Principal)
    fig_payment.add_trace(go.Scatter(
        x=df_loan["date"], y=df_loan["principal_man"],
        mode='lines', name='元金充当分 (借金が減る部分)',
        stackgroup='one', line=dict(width=0, color='#66bb6a'), # Green
        hovertemplate='%{y:,.1f}万円'
    ))
    
    # 利息 (Interest)
    fig_payment.add_trace(go.Scatter(
        x=df_loan["date"], y=df_loan["interest_man"],
        mode='lines', name='利息支払い分 (消えるお金)',
        stackgroup='one', line=dict(width=0, color='#ef5350'), # Red
        hovertemplate='%{y:,.1f}万円'
    ))
    
    # 返済額合計線
    fig_payment.add_trace(go.Scatter(
        x=df_loan["date"], y=df_loan["payment_man"],
        mode='lines', name='毎月の支払総額',
        line=dict(color='#333333', width=2, dash='dot'),
        hovertemplate='%{y:,.1f}万円'
    ))

    fig_payment.update_layout(
        title="",
        xaxis_title="年",
        yaxis_title="金額 (万円)",
        yaxis=dict(tickformat=",.1f"), # 小数点1位まで
        height=400,
        margin=dict(t=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_payment, use_container_width=True)

    # 詳細テーブル
    with st.expander("📋 年ごとの詳細を見る (数値)"):
        df_yearly = df_merged.iloc[::12, :].copy()
        df_yearly["date_str"] = df_yearly["date"].apply(lambda d: d.strftime("%Y/%m"))
        
        # 表示用データフレーム作成
        df_show = pd.DataFrame({
            "時期": df_yearly["date_str"],
            "総資産": df_yearly["asset_balance"],
            "うち投資": df_yearly["invest_balance"],
            "うち現金": df_yearly["cash_balance"],
            "ローン残高": df_yearly["balance"],
            "金利(%)": df_yearly["rate"],
            "毎月返済額": df_yearly["payment"],
            "うち利息": df_yearly["interest"]
        })

        st.dataframe(
            df_show.style.format({
                "総資産": "{:,.0f}",
                "うち投資": "{:,.0f}",
                "うち現金": "{:,.0f}",
                "ローン残高": "{:,.0f}",
                "金利(%)": "{:.3f}",
                "毎月返済額": "{:,.0f}",
                "うち利息": "{:,.0f}"
            }),
            use_container_width=True
        )