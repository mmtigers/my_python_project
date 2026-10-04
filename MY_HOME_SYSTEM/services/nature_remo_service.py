# MY_HOME_SYSTEM/services/nature_remo_service.py
"""Nature Remo(クラウドAPI)でテレビのリモコン信号を送る。

テレビの電源操作はNature Remoで行っている。プラグの100Vを断つ前に、テレビが
ついていればリモコンで先に消すために使う(services/switchbot_service.py の
turn_off_tv_gracefully から呼ぶ)。

テレビの登録方法に応じて、次のどちらかを環境変数で指定する(両方あれば信号が優先)。
- TV_REMO_SIGNAL_ID: 学習させた電源の信号ID(`POST /1/signals/{id}/send`)
- TV_REMO_APPLIANCE_ID: 家電の種類が「TV」の家電ID(`POST /1/appliances/{id}/tv` の power ボタン)

どちらの電源ボタンもトグル(つく/消えるの切り替え)になっていることが多い。
呼び出し側(turn_off_tv_gracefully)は、消費電力でテレビがついていると確認できたときだけ
送るため、トグルでも「消す」動作になる。
"""
import requests

import config
from core.logger import setup_logging

logger = setup_logging("service.nature_remo")

NATURE_REMO_API_BASE = "https://api.nature.global/1"
_REQUEST_TIMEOUT_SECONDS = 10


def _get_token() -> str | None:
    """TV_NATURE_REMO_LOCATION("itami" / "takasago")に対応するアクセストークンを返す。"""
    location = config.TV_NATURE_REMO_LOCATION
    if location == "itami":
        return config.NATURE_REMO_ACCESS_TOKEN
    if location == "takasago":
        return config.NATURE_REMO_ACCESS_TOKEN_TAKASAGO
    return None


def is_tv_remote_configured() -> bool:
    """テレビのリモコン信号を送るのに必要な設定(拠点のトークンと信号/家電ID)が揃っているか。"""
    return bool(_get_token() and (config.TV_REMO_SIGNAL_ID or config.TV_REMO_APPLIANCE_ID))


def send_tv_power() -> bool:
    """テレビの電源信号をNature Remoで送る。送信できたらTrue。失敗(未設定含む)はFalse。

    例外は外へ出さない(Fail-Soft): 呼び出し側はこれに失敗してもプラグを切るため。
    """
    token = _get_token()
    if not token:
        logger.warning("📺 Nature Remo token for the TV location is not configured.")
        return False

    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    if config.TV_REMO_SIGNAL_ID:
        url = f"{NATURE_REMO_API_BASE}/signals/{config.TV_REMO_SIGNAL_ID}/send"
        data = None
    elif config.TV_REMO_APPLIANCE_ID:
        url = f"{NATURE_REMO_API_BASE}/appliances/{config.TV_REMO_APPLIANCE_ID}/tv"
        data = {"button": "power"}
    else:
        logger.warning("📺 TV_REMO_SIGNAL_ID / TV_REMO_APPLIANCE_ID is not configured.")
        return False

    try:
        res = requests.post(url, headers=headers, data=data, timeout=_REQUEST_TIMEOUT_SECONDS)
        res.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"❌ Nature Remo TV power signal failed: {e}")
        return False
