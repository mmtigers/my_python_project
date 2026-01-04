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
    """記念日・ゾロ目チェック"""
    messages = []
    # 1. 登録済み記念日
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

    # 2. ゾロ目
    if getattr(config, "CHECK_ZOROME", False) and today.month == today.day:
        messages.append(f"✨ 今日は **{today.month}月{today.day}日**、ゾロ目の日です！🍀")

    return "\n\n".join(messages)

def create_child_health_flex():
    """家族全員の体調入力カード(Carousel)を作成"""
    bubbles = []
    
    # 記録対象リスト（順序指定）
    target_members = ["智矢", "涼花", "将博", "春菜"]
    
    # スタイル定義
    styles = {
        "智矢": {"color": "#1E90FF", "age": "5歳", "icon": "👦"}, # Blue
        "涼花": {"color": "#FF69B4", "age": "2歳", "icon": "👧"}, # Pink
        "将博": {"color": "#2E8B57", "age": "35歳", "icon": "👨"}, # Green
        "春菜": {"color": "#FF8C00", "age": "ママ", "icon": "👩"}, # Orange
    }

    for name in target_members:
        # デフォルトスタイル
        st = styles.get(name, {"color": "#333333", "age": "", "icon": "🙂"})
        
        # Flex Bubble構築
        bubble = {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": st["color"],
                "contents": [
                    {"type": "text", "text": "健康チェック", "color": "#FFFFFF", "weight": "bold", "size": "xs"},
                    {"type": "text", "text": f"{st['icon']} {name}", "color": "#FFFFFF", "weight": "bold", "size": "xl", "margin": "md"}
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "今の体調を教えてください✨", "size": "sm", "color": "#666666"}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    # 1. 元気
                    {"type": "button", "style": "primary", "color": st["color"], "height": "sm",
                     "action": {"type": "postback", "label": "💮 元気いっぱい！", "data": f"action=child_check&child={name}&status=genki"}},
                    # 2. 不調系（熱/風邪）
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "🤒 お熱がある", "data": f"action=child_check&child={name}&status=fever"}},
                    # 3. その他/詳細
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "🤧 鼻水・咳・他", "data": f"action=child_check&child={name}&status=cold"}},
                    
                    # 区切り線
                    {"type": "separator", "margin": "md"},
                    
                    # 4. 履歴参照ボタン (NEW!)
                    {"type": "button", "style": "link", "height": "sm", "margin": "md",
                     "action": {"type": "postback", "label": "📊 最近の記録を見る", "data": f"action=get_history&child={name}"}}
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
            clean_msg = special_msg.replace("**", "")
            payloads.append({"type": "text", "text": f"☀️ おはようございます！\n\n{clean_msg}"})
        
        # 2. 体調入力Flex Message (全員分)
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