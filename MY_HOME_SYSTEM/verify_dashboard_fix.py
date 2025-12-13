# verify_dashboard_fix.py
import sys
import os
import pandas as pd
import importlib

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import dashboard
import config

def run_test():
    print("🧪 [Test] ダッシュボード修正箇所の検証開始...")

    # 1. 電気代累積計算のテスト
    print("\n1. 電気代累積計算 (calculate_monthly_cost_cumulative)")
    try:
        cost = dashboard.calculate_monthly_cost_cumulative()
        print(f"   💰 今月の累積電気代: {cost} 円")
        if cost == 0:
            print("   ⚠️ 0円です。データがないか、計算期間にNature Remoのデータがありません。")
    except Exception as e:
        print(f"   ❌ 計算エラー: {e}")

    # 2. 高砂トイレ問題の確認
    print("\n2. デバイス設定の確認 (config.MONITOR_DEVICES)")
    has_taka_toilet = False
    for d in config.MONITOR_DEVICES:
        if d.get('location') == '高砂' and 'トイレ' in d.get('name', ''):
            print(f"   ✅ 高砂トイレ発見: {d['name']} (ID: {d['id']})")
            has_taka_toilet = True
            break
    
    if not has_taka_toilet:
        print("   ⚠️ 高砂のトイレデバイスは見つかりませんでした (0回表示の原因はこれです)")
        print("   👉 Dashboardでは「トイレ (伊丹)」と表示されるよう修正しました。")

    print("\n🎉 検証終了")

if __name__ == "__main__":
    run_test()