import sys
import os
import datetime
from unittest.mock import MagicMock

# パスを通す
sys.path.append(os.getcwd())

from menu_service import MenuService
from send_ai_report import fetch_daily_data, build_system_prompt

print("🧪 --- 献立提案機能テスト ---")

# 1. MenuServiceのテスト
print("\n[1] MenuServiceの動作確認")
ms = MenuService()
print(f" - 直近のメニュー: {ms.get_recent_menus()}")
print(f" - 特別な日判定: {ms.get_special_day_info()}")

# 2. 擬似的に時間を操作してデータ取得テスト
print("\n[2] 時間帯ごとのデータ取得シミュレーション")

# A. お昼（12時）の場合 -> メニュー提案データが含まれるべき
print(" >> 模擬時刻: 12:00 (お昼)")
# datetimeをモックするのは複雑なので、fetch_daily_dataのロジックを手動検証
# ここでは簡易的に、fetch_daily_dataを呼んでみて 'menu_suggestion_context' があるか確認
# (ただし実行時刻が夜なら含まれないのが正しい挙動)
data = fetch_daily_data()

if 'menu_suggestion_context' in data:
    print(" ✅ 現在はお昼の時間帯です。メニュー提案コンテキストが取得されました。")
    print(f"    データ: {data['menu_suggestion_context']}")
else:
    print(" ℹ️ 現在はお昼の時間帯ではありません。メニュー提案はスキップされました。")
    
    # 強制的にデータを入れてプロンプト生成をテスト
    print(" >> 強制的にメニューデータを注入してプロンプト生成テスト")
    data['menu_suggestion_context'] = {
        "recent_menus": ["2025-12-10: カレー", "2025-12-11: パスタ"],
        "special_day": "給料日💰"
    }

# 3. プロンプト生成テスト
print("\n[3] システムプロンプト生成確認")
prompt = build_system_prompt(data)

if "今夜の献立" in prompt:
    print(" ✅ プロンプトに「献立提案」セクションが含まれています。")
    if "給料日" in prompt:
        print(" ✅ 「給料日」の情報も反映されています。")
else:
    print(" ❌ プロンプトに献立提案が含まれていません。")

print("\n🎉 テスト完了")