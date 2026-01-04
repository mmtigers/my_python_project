# MY_HOME_SYSTEM/send_child_health_check.py
import datetime
import pytz
import traceback
import argparse
import sys
import config
import common
from linebot.models import FlexSendMessage, BubbleContainer, BoxComponent, TextComponent, ButtonComponent

# ロガー設定
logger = common.setup_logging("morning_check")

def parse_arguments():
    parser = argparse.ArgumentParser(description='朝の体調確認＆記念日通知スクリプト')
    parser.add_argument('--target', type=str, default='line', choices=['line', 'discord'],
                        help='通知先 (line, discord)')
    return parser.parse_args()

def check_special_events(today):
    """記念日・ゾロ目チェック (既存ロジック維持)"""
    messages = []
    # 1. 登録済み記念日
    for event in config.IMPORTANT_DATES:
        try:
            evt_date = datetime.datetime.strptime(event["date"], "%Y-%m-%d")
            if today.month == evt_date.month and today.day == evt_date.day:
                # 年数計算簡略化
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

    # 2. ゾロ目
    if getattr(config, "CHECK_ZOROME", False) and today.month == today.day:
        messages.append(f"✨ 今日は **{today.month}月{today.day}日**、ゾロ目の日です！🍀")

    return "\n\n".join(messages)

def create_child_health_flex():
    """子供ごとの体調入力カード(Carousel)を作成"""
    bubbles = []
    children = config.CHILDREN_NAMES if config.CHILDREN_NAMES else ["子供"]
    
    # お子様ごとのテーマカラー設定
    child_styles = {
        "智矢": {"color": "#1E90FF", "age": "5歳", "icon": "👦"}, # Blue
        "涼花": {"color": "#FF69B4", "age": "2歳", "icon": "👧"}, # Pink
    }

    for child in children:
        style = child_styles.get(child, {"color": "#333333", "age": "", "icon": "👶"})
        
        bubble = {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": style["color"],
                "contents": [
                    {"type": "text", "text": "朝の健康チェック", "color": "#FFFFFF", "weight": "bold", "size": "xs"},
                    {"type": "text", "text": f"{style['icon']} {child} ({style['age']})", "color": "#FFFFFF", "weight": "bold", "size": "xl", "margin": "md"}
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "おはようございます！\n今の体調を教えてください✨", "wrap": True, "size": "sm", "color": "#666666"}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    # 1. 元気
                    {"type": "button", "style": "primary", "color": style["color"], "height": "sm",
                     "action": {"type": "postback", "label": "💮 元気いっぱい！", "data": f"action=child_check&child={child}&status=genki"}},
                    # 2. 熱
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "🤒 お熱がある", "data": f"action=child_check&child={child}&status=fever"}},
                    # 3. 鼻水・咳
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "🤧 鼻水・咳", "data": f"action=child_check&child={child}&status=cold"}},
                    # 4. その他（手入力へ誘導）
                    {"type": "button", "style": "link", "height": "sm",
                     "action": {"type": "postback", "label": "その他の不調・記録", "data": f"action=child_check&child={child}&status=other"}}
                ]
            }
        }
        bubbles.append(bubble)

    return {
        "type": "flex",
        "altText": "朝の体調確認をお願いします！",
        "contents": {"type": "carousel", "contents": bubbles}
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
            # Discord用のMarkdown(**)を除去してLINE用に
            clean_msg = special_msg.replace("**", "")
            payloads.append({"type": "text", "text": f"☀️ おはようございます！\n\n{clean_msg}"})
        
        # 2. 体調入力Flex Message
        payloads.append(create_child_health_flex())

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