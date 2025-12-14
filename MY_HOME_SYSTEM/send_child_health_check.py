# MY_HOME_SYSTEM/send_child_health_check.py
import datetime
import pytz
import traceback
import argparse
import sys
import config
import common

# ロガー設定
logger = common.setup_logging("morning_check")

def parse_arguments():
    """コマンドライン引数の解析"""
    parser = argparse.ArgumentParser(description='朝の体調確認＆記念日通知スクリプト')
    parser.add_argument('--target', type=str, default='line', choices=['line', 'discord'],
                        help='通知先 (line, discord)')
    return parser.parse_args()

def get_age_or_years(date_str, today):
    """
    誕生日なら年齢、記念日なら経過年数を計算する
    date_str: "YYYY-MM-DD"
    """
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        years = today.year - dt.year
        # まだ誕生日/記念日が来ていない場合は-1
        if (today.month, today.day) < (dt.month, dt.day):
            years -= 1
        return years
    except ValueError:
        return None

def check_special_events(today):
    """
    今日が特別な日かどうかをチェックし、メッセージを返す
    """
    messages = []
    
    # 1. 登録済み記念日・誕生日のチェック
    for event in config.IMPORTANT_DATES:
        try:
            # 日付文字列のパース (YYYY-MM-DD 想定)
            evt_date = datetime.datetime.strptime(event["date"], "%Y-%m-%d")
            
            # 月日が一致するか
            if today.month == evt_date.month and today.day == evt_date.day:
                years = get_age_or_years(event["date"], today)
                name = event.get('name', '???')
                
                if event["type"] == "birthday":
                    msg = f"🎉 今日は **{name}の{years}歳のお誕生日** です！\nおめでとうございます🎂✨"
                elif event["type"] == "anniversary":
                    msg = f"💍 今日は **{name}から{years}周年** の記念日です！\nおめでとうございます🥂"
                else:
                    msg = f"✨ 今日は **{name}** の日です！"
                
                messages.append(msg)
                
        except Exception as e:
            logger.warning(f"日付データ解析エラー ({event}): {e}")
            continue

    # 2. ゾロ目の日チェック (configで有効な場合)
    if getattr(config, "CHECK_ZOROME", False):
        if today.month == today.day:
            messages.append(f"✨ 今日は **{today.month}月{today.day}日**、ゾロ目の日です！\n何かいいことあるかも？🍀")

    return "\n\n".join(messages)

def create_morning_message(special_msg):
    """
    朝の挨拶メッセージを作成する
    """
    base_msg = "☀️ おはようございます！\n"
    
    if special_msg:
        # 特別な日なら、最初にお祝いを
        base_msg += f"\n{special_msg}\n\n"
        base_msg += "素敵な一日になりますように✨\n"
        base_msg += "ところで、子供たちの体調はいかがですか？😊"
    else:
        # 通常運転
        base_msg += "子供たちの体調はいかがですか？\n変わりないか教えてください😊"
    
    return base_msg

def main():
    print(f"\n🚀 --- Morning Check Start: {datetime.datetime.now().strftime('%H:%M:%S')} ---")
    args = parse_arguments()
    
    try:
        # 今日の日付
        now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
        print(f"📅 Today: {now.strftime('%Y-%m-%d')}")
        
        # 1. 記念日チェック
        special_msg = check_special_events(now)
        if special_msg:
            print(f"✨ Special Event Detected:\n{special_msg}")
        else:
            print("⚪ No special event today.")

        # 2. メッセージ作成
        full_text = create_morning_message(special_msg)
        
        # 3. ボタン作成
        actions = []
        if config.CHILDREN_NAMES:
            for child in config.CHILDREN_NAMES:
                actions.append((f"👦👧 {child}", f"子供選択_{child}"))
        else:
            actions.append(("子供の記録", "子供選択_子供"))

        actions.append(("✨ みんな元気！", "子供記録_全員_元気"))
        
        items = [{"type": "action", "action": {"type": "message", "label": l, "text": t}} for l, t in actions]
        
        msg_payload = {
            "type": "text",
            "text": full_text,
            "quickReply": {"items": items}
        }
        
        # 4. 送信
        target = args.target
        if common.send_push(config.LINE_USER_ID, [msg_payload], target=target):
            print(f"✅ 送信成功 ({target})")
        else:
            logger.error(f"送信失敗 ({target})")
            sys.exit(1)

    except Exception as e:
        logger.error(f"エラー発生: {e}")
        logger.error(traceback.format_exc())
        common.send_push(config.LINE_USER_ID, 
                         [{"type": "text", "text": f"😰 **Morning Check Error**\n```{e}```"}], 
                         target="discord", channel="error")
        sys.exit(1)

if __name__ == "__main__":
    main()