# MY_HOME_SYSTEM/services/notification_service.py
import time
import requests
from typing import List, Optional, Any

# ▼▼▼ v3 Imports ▼▼▼
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
    Message
)
# ▲▲▲ ▲▲▲
import config
from core import discord as core_discord
from core.logger import setup_logging # 修正: core.loggerを使用

logger = setup_logging("service.notification") # 修正: 統一ロガーを使用

# v3 Configuration
line_configuration: Optional[Configuration] = None
if config.LINE_CHANNEL_ACCESS_TOKEN:
    line_configuration = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)

def _send_discord_webhook(messages: List[Any], image_data: Optional[bytes] = None, channel: str = "notify", filename: str = "snapshot.jpg") -> bool:
    """DiscordへのWebhook送信"""
    if channel == "error":
        url = config.DISCORD_WEBHOOK_ERROR
    elif channel == "report":
        url = config.DISCORD_WEBHOOK_REPORT
    else:
        url = config.DISCORD_WEBHOOK_NOTIFY or config.DISCORD_WEBHOOK_URL
    
    if not url:
        return False
    
    text_content = ""
    for msg in messages:
        # v3オブジェクトの場合は text 属性などを取得
        if hasattr(msg, "text"):
            text = msg.text
        elif hasattr(msg, "alt_text"):
            text = msg.alt_text
        elif isinstance(msg, dict):
            text = msg.get("text") or msg.get("altText") or "（画像またはスタンプ）"
        else:
            text = "（メッセージ）"
        text_content += f"{text}\n\n"
    
    # #361: Discord の content は 2000 文字上限。以前は切り詰めも分割もせず送っていたため、
    # 週間ログ分析レポート等の長文は 400 で丸ごと届かず、DDD 側ではブレーカーの誤作動も
    # 招いていた。上限内のチャンクに分割し、先頭チャンクにのみ画像を添付する。
    chunks = _split_discord_content(text_content)
    try:
        for idx, chunk in enumerate(chunks):
            if image_data and idx == 0:
                # MIMEタイプの指定を外し、Discord側にファイル拡張子で自動判定させる
                files = {'file': (filename, image_data)}
                res = _post_discord_with_retry(url, files=files, data={'content': chunk}, timeout=60)
            else:
                res = _post_discord_with_retry(url, json={"content": chunk}, timeout=10)

            # ステータスコードが成功(200, 204)以外の場合、エラー内容をログに出力して原因を特定する
            if res.status_code not in [200, 204]:
                logger.error(f"Discord API エラー: {res.status_code} - {res.text}")
                return False

        return True
    except Exception as e:
        logger.error(f"Discord送信失敗: {e}")
        return False


# #661: 実体は core/discord.py。既存の参照名は互換のため残す。
DISCORD_CONTENT_CHUNK_SIZE = core_discord.CONTENT_CHUNK_SIZE
DISCORD_RETRY_ATTEMPTS = core_discord.RETRY_ATTEMPTS
DISCORD_RETRY_MAX_WAIT_SECONDS = core_discord.RETRY_MAX_WAIT_SECONDS
_retry_sleep = time.sleep


# #661: 分割・リトライ・URL マスクの実体は core/discord.py に一本化した。
# ここでは既存の呼び出し元とテストが使っている名前をそのまま維持するための薄い委譲だけを置く
# (チャンネルの振り分けや LINE メッセージオブジェクトのテキスト化は上位の責務としてこのまま残す)。
def _split_discord_content(text: str, limit: int = DISCORD_CONTENT_CHUNK_SIZE) -> List[str]:
    """text を limit 文字以下のチャンクに分割する(core.discord.split_content への委譲)。"""
    return core_discord.split_content(text, limit)


def _post_discord_with_retry(url: str, **kwargs):
    """429/5xx を限定回数リトライする POST(core.discord.post_with_retry への委譲)。"""
    # テストが notification_service._retry_sleep を差し替える契約を維持するため、
    # 実際の待機関数も合わせて渡す(core.discord 側のモジュール属性を一時的に差し替える)。
    original_sleep = core_discord._retry_sleep
    core_discord._retry_sleep = _retry_sleep
    try:
        return core_discord.post_with_retry(url, **kwargs)
    finally:
        core_discord._retry_sleep = original_sleep


def _send_line_push(user_id: str, messages: List[Any]) -> bool:
    """LINE Push API送信 (v3対応版)"""
    if not line_configuration:
        return False
    
    sdk_messages: List[Message] = []
    
    try:
        for msg in messages:
            # A. 既に v3 オブジェクトの場合
            if isinstance(msg, Message): 
                sdk_messages.append(msg)
            
            # B. 辞書型の場合 (互換性維持)
            elif isinstance(msg, dict):
                msg_type = msg.get("type")
                if msg_type == "text":
                    sdk_messages.append(TextMessage(text=msg.get("text", "")))
                elif msg_type == "flex":
                    # Issue #322: 以前はここが pass で、辞書形式のflexメッセージが
                    # 無言で破棄されていた(テキスト混在時は送信自体は成功するため
                    # 気づけないサイレント障害の芽)。handlers/line_logic.py と同じ
                    # FlexContainer.from_dict でv3オブジェクトへ変換し、変換に
                    # 失敗した場合も黙殺せず内容つきのエラーログを残す。
                    try:
                        sdk_messages.append(
                            FlexMessage(
                                altText=msg.get("altText") or msg.get("alt_text") or "通知",
                                contents=FlexContainer.from_dict(msg.get("contents") or {}),
                            )
                        )
                    except Exception as conv_err:
                        logger.error(
                            f"flex辞書メッセージのFlexMessage変換に失敗したため破棄します: {conv_err} / msg={msg}"
                        )
                else:
                    # ImageMessage 等の未対応型もサイレントに落とさずログに残す
                    logger.warning(f"未対応のメッセージ型のため破棄します: type={msg_type} / msg={msg}")

        if not sdk_messages:
            logger.warning("LINE送信対象のメッセージがありません")
            return False

        # v3 送信処理
        with ApiClient(line_configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=sdk_messages
                ),
                _request_timeout=config.LINE_API_REQUEST_TIMEOUT
            )
        return True

    except Exception as e:
        logger.error(f"LINE Push Error: {e}")
        return False

def send_push(
    messages: List[Any],
    *,
    target: str = "both",
    channel: str = "notify",
    user_id: Optional[str] = None,
    image_data: Optional[bytes] = None,
    filename: str = "snapshot.jpg",
) -> bool:
    """統合プッシュ通知関数。

    宛先の組み合わせ解決をこの関数に一元化する。`user_id` は LINE 送信が
    必要な場合(targetが"line"または"both")のみ使われ、省略時は
    `config.LINE_USER_ID` にフォールバックする。target="discord" のみの
    呼び出しでは user_id は一切不要(呼び出し元が意味のないLINE宛先を
    渡す必要がなくなる。Issue #289)。
    messages以外の引数はキーワード専用とし、位置引数の取り違え
    (Issue #167のようにtarget/channelが誤ってimage_data等に渡ってしまう事故)
    を型レベルで防ぐ。
    """
    success = True

    # 1. Discord送信
    if target in ["discord", "both"]:
        if not _send_discord_webhook(messages, image_data, channel, filename):
            logger.warning("Discordへの通知に失敗しました")
            success = False

    # 2. LINE送信 (image_dataはLINEには送らない簡易実装)
    if target in ["line", "both"]:
        resolved_user_id = user_id or getattr(config, "LINE_USER_ID", None)
        if not resolved_user_id:
            logger.error(f"LINE通知の送信先user_idが指定されていません(target={target})")
            success = False
        else:
            # 画像がある場合はテキストで注記を追加
            line_msgs = list(messages)
            if image_data:
                line_msgs.append(TextMessage(text="※画像はDiscordを確認してください"))

            if not _send_line_push(resolved_user_id, line_msgs):
                # LINE失敗時はDiscordのエラーチャンネルに通知
                logger.error("LINE送信失敗。Discordへフォールバック通知を行います。")
                fallback = [{"type": "text", "text": "⚠️ LINE送信失敗: (詳細ログ確認)"}]
                _send_discord_webhook(fallback, None, 'error')
                success = False

    return success

