# HOME_SYSTEM/send_child_health_check.py
import config
import common

logger = common.setup_logging("child_health")

def send_check():
    logger.info("子供体調確認の送信を開始...")
    
    if not config.CHILDREN_NAMES:
        logger.error("子供の名前が設定されていません。.envファイルを確認してください。")
        return

    # メッセージ作成 (主婦向けトーン)
    msg_text = "☀️ おはようございます！\n子供たちの体調はいかがですか？\n変わりないか教えてください😊"
    
    # ボタン作成
    actions = []
    # 1. 各子供のボタン
    for child in config.CHILDREN_NAMES:
        actions.append((f"👦👧 {child}", f"子供選択_{child}"))
    
    # 2. 一括元気ボタン
    actions.append(("✨ みんな元気！", "子供記録_全員_元気"))
    
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": t}} for l, t in actions]
    
    msg = {
        "type": "text",
        "text": msg_text,
        "quickReply": {"items": items}
    }
    
    # 送信
    if common.send_push(config.LINE_USER_ID, [msg]):
        logger.info("送信完了")
    else:
        logger.error("送信失敗")

if __name__ == "__main__":
    send_check()