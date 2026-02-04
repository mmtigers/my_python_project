# MY_HOME_SYSTEM/views/line_flex.py
from linebot.v3.messaging import FlexContainer
import config

def create_health_carousel() -> FlexContainer:
    """詳細入力用カルーセルを作成"""
    bubbles = []
    styles = config.FAMILY_SETTINGS["styles"]
    members = config.FAMILY_SETTINGS["members"]

    for name in members:
        st = styles.get(name, {"color": "#333333", "age": "", "icon": "🙂"})
        bubble = {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": st["color"],
                "contents": [
                    {"type": "text", "text": f"{st['icon']} {name}", "color": "#FFFFFF", "weight": "bold", "size": "xl"}
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [{"type": "text", "text": "体調を選択してください", "size": "sm", "color": "#666666"}]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "button", "style": "primary", "color": st["color"], "height": "sm",
                     "action": {"type": "postback", "label": "💮 元気！", "data": f"action=child_check&child={name}&status=genki"}},
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "🤒 熱あり", "data": f"action=child_check&child={name}&status=fever"}},
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "🤧 鼻水・他", "data": f"action=child_check&child={name}&status=cold"}},
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "✏️ その他（手入力）", "data": f"action=child_check&child={name}&status=other"}},
                    {"type": "separator", "margin": "md"},
                    {"type": "button", "style": "link", "height": "sm", "margin": "md",
                     "action": {"type": "postback", "label": "📊 今日の記録確認", "data": "action=check_status"}}
                ]
            }
        }
        bubbles.append(bubble)

    return FlexContainer.from_dict({"type": "carousel", "contents": bubbles})

def create_record_confirm_bubble(text: str, button_label: str = "📊 記録を確認") -> FlexContainer:
    """記録完了時の確認バブルを作成"""
    return FlexContainer.from_dict({
        "type": "bubble",
        "body": {
            "type": "box", "layout": "vertical",
            "contents": [{"type": "text", "text": text, "wrap": True}]
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "button", "action": {"type": "postback", "label": button_label, "data": "action=check_status"}}
            ]
        }
    })

def create_summary_bubble(date_str: str, summary_text: str) -> FlexContainer:
    """サマリ表示バブルを作成"""
    return FlexContainer.from_dict({
        "type": "bubble",
        "body": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": f"📅 {date_str} の記録", "weight": "bold", "size": "md"},
                {"type": "separator", "margin": "md"},
                {"type": "text", "text": summary_text, "wrap": True, "margin": "md", "lineSpacing": "6px"}
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "contents": [
                {
                    "type": "button", 
                    "style": "secondary", 
                    "action": {
                        "type": "postback", 
                        "label": "✏️ 修正する (入力パネル)", 
                        "data": "action=show_health_input"
                    }
                }
            ]
        }
    })