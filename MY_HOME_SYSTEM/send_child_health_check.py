# MY_HOME_SYSTEM/send_child_health_check.py
import datetime
import pytz
import traceback
import argparse
import sys
import config
import common
# Flex Message用ライブラリ
from linebot.models import FlexSendMessage, BubbleContainer, BoxComponent, TextComponent, ButtonComponent

# ロガー設定
logger = common.setup_logging("morning_check")

def parse_arguments():
    parser = argparse.ArgumentParser(description='朝の体調確認＆記念日通知スクリプト')
    parser.add_argument('--target', type=str, default='line', choices=['line', 'discord'],
                        help='通知先 (line, discord)')
    return parser.parse_args()

def check_special_events(today):
    """記念日・ゾロ目チェック"""
    messages = []
    for event in config.IMPORTANT_DATES:
        try:
            evt_date = datetime.datetime.strptime(event["date"], "%Y-%m-%d")
            if today.month == evt_date.month and today.day == evt_date.day:
                years = today.year - evt_date.year
                if (today.month, today.day) < (evt_date.month, evt_date.day): years -= 1
                
                name = event.get('name', '???')
                if event["type"] == "birthday":
                    msg = f"🎉 今日は **{name}の{years}歳のお誕生日** です！\nおめでとうございます🎂✨"
                elif event["type"] == "anniversary":
                    msg = f"💍 今日は **{name}から{years}周年** の記念日です！\nおめでとうございます🥂"
                else:
                    msg = f"✨ 今日は **{name}** の日です！"
                messages.append(msg)
        except Exception:
            continue

    if getattr(config, "CHECK_ZOROME", False) and today.month == today.day:
        messages.append(f"✨ 今日は **{today.month}月{today.day}日**、ゾロ目の日です！🍀")

    return "\n\n".join(messages)

def create_start_check_flex():
    """最初の確認カード（全員元気か？）を作成"""
    return {
        "type": "flex",
        "altText": "朝の体調確認をお願いします！",
        "contents": {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#f0f0f0",
                "contents": [
                    {"type": "text", "text": "☀️ 朝の健康チェック", "weight": "bold", "size": "md", "color": "#333333"}
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "おはようございます！\nみんなの体調はいかがですか？", "wrap": True, "size": "md", "color": "#666666"}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    # 1. 全員元気（一括登録）
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#1E90FF", # 明るい青
                        "height": "sm",
                        "action": {
                            "type": "postback",
                            "label": "✨ 全員元気！",
                            "data": "action=all_genki",
                            "displayText": "みんな元気です！"
                        }
                    },
                    # 2. 個別入力へ
                    {
                        "type": "button",
                        "style": "secondary",
                        "height": "sm",
                        "action": {
                            "type": "postback",
                            "label": "📝 不調・入力あり",
                            "data": "action=show_health_input",
                            "displayText": "詳しい体調を入力します。"
                        }
                    },
                    # 3. 状態確認
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {
                            "type": "postback",
                            "label": "📊 今日の記録を確認",
                            "data": "action=check_status"
                        }
                    }
                ]
            }
        }
    }

def main():
    print(f"\n🚀 --- Morning Check Start: {datetime.datetime.now().strftime('%H:%M:%S')} ---")
    args = parse_arguments()
    
    try:
        now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
        payloads = []
        
        # 1. 記念日メッセージ
        special_msg = check_special_events(now)
        if special_msg:
            clean_msg = special_msg.replace("**", "")
            payloads.append({"type": "text", "text": f"☀️ おはようございます！\n\n{clean_msg}"})
        
        # 2. 開始カード Flex Message
        payloads.append(create_start_check_flex())

        # 3. 送信
        target = args.target
        if common.send_push(config.LINE_USER_ID, payloads, target=target):
            print(f"✅ 送信成功 ({target})")
        else:
            logger.error(f"送信失敗 ({target})")
            sys.exit(1)

    except Exception as e:
        logger.error(f"エラー発生: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()