# MY_HOME_SYSTEM/handlers/alexa_handler.py
"""
Alexaカスタムスキル「ファミクエ」のリクエストハンドラ。

「アレクサ、ファミクエを開いて」は Alexa側では LaunchRequest として届く(専用の
IntentもInvocationも不要)ため、ここでは LaunchRequest だけを扱う。

既存の family-quest Web アプリ(React、/quest/ で配信)をEcho Show上にそのまま
表示するには APL WebView コンポーネントが要るが、これはAmazonへの個別申請が必要な
限定提供機能のため使わない。代わりに quest_service.game_system.get_all_view_data()
と同じデータソースから、APL(Alexa Presentation Language)でネイティブに
メイン画面相当(家族ごとのLv/EXP/所持金/承認待ち件数)を組み立てて表示する。
"""
import os
import json
from typing import Any, Dict, List, Optional

from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.utils import is_request_type
from ask_sdk_core.utils.predicate import is_intent_name
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response
from ask_sdk_model.interfaces.alexa.presentation.apl import RenderDocumentDirective

import config
from core.logger import setup_logging
from services.quest_service import game_system

logger = setup_logging("alexa_handler")

_APL_DOCUMENT_PATH = os.path.join(os.path.dirname(__file__), "..", "alexa", "apl", "main_screen.json")
_apl_document_cache: Optional[Dict[str, Any]] = None

# #452: Alexaの読み上げ(outputSpeech)は8000文字が上限。家族人数が増えて
# 全員分の詳細を連結すると将来的にこれへ抵触しうるため、安全マージンを見て
# この文字数を超える場合は詳細列挙をやめ要約に切り替える。
_ALEXA_SPEECH_SAFE_LIMIT = 7500


def _load_apl_document() -> Dict[str, Any]:
    global _apl_document_cache
    if _apl_document_cache is None:
        with open(_APL_DOCUMENT_PATH, "r", encoding="utf-8") as f:
            _apl_document_cache = json.load(f)
    return _apl_document_cache


def _build_family_datasource() -> Dict[str, Any]:
    """quest_service の集計データから、APL/読み上げ両方で使う軽量なビューモデルを作る。"""
    data = game_system.get_all_view_data()

    pending_by_user: Dict[str, int] = {}
    for p in data.get("pendingQuests", []):
        uid = p.get("user_id")
        if uid:
            pending_by_user[uid] = pending_by_user.get(uid, 0) + 1

    users: List[Dict[str, Any]] = []
    for u in data.get("users", []):
        next_level_exp = u.get("nextLevelExp") or 0
        exp = u.get("exp") or 0
        exp_percent = round(min(exp / next_level_exp, 1.0) * 100) if next_level_exp > 0 else 0
        users.append({
            "userId": u["user_id"],
            "name": u["name"],
            "avatar": u.get("avatar") or "🙂",
            "level": u["level"],
            "exp": exp,
            "nextLevelExp": next_level_exp,
            "expPercent": exp_percent,
            "gold": u["gold"],
            "pendingCount": pending_by_user.get(u["user_id"], 0),
        })

    return {
        "title": "ファミリークエスト",
        "users": users,
        "pendingTotal": len(data.get("pendingQuests", [])),
    }


def _supports_apl(handler_input: HandlerInput) -> bool:
    supported = handler_input.request_envelope.context.system.device.supported_interfaces
    return supported.alexa_presentation_apl is not None


class LaunchRequestHandler(AbstractRequestHandler):
    """「アレクサ、ファミクエを開いて」で発火する LaunchRequest を処理する。"""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        response_builder = handler_input.response_builder

        try:
            family_data = _build_family_datasource()
        except Exception:
            logger.exception("Failed to build family quest datasource for LaunchRequest")
            return (
                response_builder
                .speak("ファミリークエストのデータ取得に失敗しました。少し時間をおいて試してください。")
                .set_should_end_session(True)
                .response
            )

        pending_total = family_data["pendingTotal"]
        speech = "ファミリークエストを開きます。"
        if pending_total:
            speech += f"承認待ちのクエストが{pending_total}件あります。"

        if _supports_apl(handler_input):
            response_builder.add_directive(
                RenderDocumentDirective(
                    token="familyQuestMainScreen",
                    document=_load_apl_document(),
                    datasources={"payload": {"familyData": family_data}},
                )
            )
        else:
            # 画面がないデバイス向けのフォールバック: 家族ごとの状況を読み上げる
            details = "".join(
                f"{u['name']}さんはレベル{u['level']}、{u['gold']}ゴールドです。"
                for u in family_data["users"]
            )
            if len(speech) + len(details) > _ALEXA_SPEECH_SAFE_LIMIT:
                # #452: 上限に近づく場合は個別詳細を諦め、件数のみの要約に切り替える
                speech += f"{len(family_data['users'])}人分のデータがあります。詳細は画面でご確認ください。"
            else:
                speech += details

        response_builder.speak(speech).set_should_end_session(False)
        return response_builder.response


class HelpIntentHandler(AbstractRequestHandler):
    """Alexa認定に必須のビルトインインテント。「アレクサ、ヘルプ」で発火する。"""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = "「開いて」と言うと、家族みんなのレベルやゴールドを見られます。"
        return (
            handler_input.response_builder
            .speak(speech)
            .set_should_end_session(False)
            .response
        )


class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Alexa認定に必須のビルトインインテント。「アレクサ、やめて/ストップ」で発火する。"""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return (
            is_intent_name("AMAZON.CancelIntent")(handler_input)
            or is_intent_name("AMAZON.StopIntent")(handler_input)
        )

    def handle(self, handler_input: HandlerInput) -> Response:
        return (
            handler_input.response_builder
            .speak("またね。")
            .set_should_end_session(True)
            .response
        )


class FallbackIntentHandler(AbstractRequestHandler):
    """Alexa認定に必須のビルトインインテント。認識できない発話で発火する。"""

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        speech = "すみません、よくわかりませんでした。「開いて」と言ってみてください。"
        return (
            handler_input.response_builder
            .speak(speech)
            .set_should_end_session(False)
            .response
        )


class NavigateHomeIntentHandler(AbstractRequestHandler):
    """Echo Show等でユーザーが「ホームに戻って」と言ったときの必須ハンドラ。

    Amazonのマルチモーダル認定要件どおり、発話なしでセッションを終了し、
    Alexaのホーム画面への遷移をデバイス側に委ねる。
    """

    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.NavigateHomeIntent")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        return handler_input.response_builder.set_should_end_session(True).response


class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input: HandlerInput) -> Response:
        return handler_input.response_builder.response


class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input: HandlerInput, exception: Exception) -> bool:
        return True

    def handle(self, handler_input: HandlerInput, exception: Exception) -> Response:
        logger.error(f"Alexa skill unhandled error: {exception}", exc_info=exception)
        return (
            handler_input.response_builder
            .speak("すみません、うまく処理できませんでした。")
            .set_should_end_session(True)
            .response
        )


sb = CustomSkillBuilder()
if config.ALEXA_SKILL_ID:
    sb.skill_id = config.ALEXA_SKILL_ID
else:
    logger.warning("⚠️ ALEXA_SKILL_ID is not set — skill ID verification is DISABLED. Set the env var to enable it.")

sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(NavigateHomeIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

skill = sb.create()
