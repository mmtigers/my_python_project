"""`core/security_posture.py` のテスト (Issue #799)。

このモジュールの価値は「**何を報告し、何を報告しないか**」の線引きそのものなので、
検出できること(フェイルオープン)と同じ重みで、**意図的に未設定の設定を報告しないこと**
(フェイルクローズ・運用上オフにしている機能)も固定する。ここが緩むと警告が常時鳴り、
「いつものやつ」として無視され、一覧性という本来の目的が壊れる。
"""
from types import SimpleNamespace

import pytest
from core.security_posture import (
    PostureFinding,
    check_security_posture,
    format_findings,
)


def _cfg(**overrides):
    """すべての保護が有効な状態を既定にした設定オブジェクトを作る。

    テストごとに「1つだけ壊す」書き方ができるよう、健全な既定を1か所に置く。
    """
    base = {
        "ALEXA_SKILL_ID": "amzn1.ask.skill.dummy",
        "AUTHORIZED_LINE_USER_IDS": ["U" + "0" * 32],
        "SWITCHBOT_WEBHOOK_TOKEN": "tok",
        "ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK": False,
        "ALLOW_ALL_ORIGINS": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestHealthyBaseline:
    def test_no_findings_when_everything_is_configured(self):
        assert check_security_posture(_cfg()) == []

    def test_format_is_explicit_when_clean(self):
        assert format_findings([]) == "保護が無効な設定はありません"


class TestDetectsFailOpen:
    @pytest.mark.parametrize("overrides, expected_key", [
        ({"ALEXA_SKILL_ID": None}, "ALEXA_SKILL_ID"),
        ({"ALEXA_SKILL_ID": ""}, "ALEXA_SKILL_ID"),
        ({"ALEXA_SKILL_ID": "   "}, "ALEXA_SKILL_ID"),
        ({"AUTHORIZED_LINE_USER_IDS": []}, "AUTHORIZED_LINE_USER_IDS"),
        ({"AUTHORIZED_LINE_USER_IDS": None}, "AUTHORIZED_LINE_USER_IDS"),
        ({"ALLOW_ALL_ORIGINS": True}, "ALLOW_ALL_ORIGINS"),
    ])
    def test_single_fail_open_is_reported(self, overrides, expected_key):
        findings = check_security_posture(_cfg(**overrides))
        assert [f.key for f in findings] == [expected_key]

    def test_finding_carries_issue_ref_and_remedy(self):
        """通知を見た人が「何が無効で、どう直すか」を追える情報が載っていること。"""
        (finding,) = check_security_posture(_cfg(AUTHORIZED_LINE_USER_IDS=[]))
        assert isinstance(finding, PostureFinding)
        assert finding.ref == "#799"
        assert finding.protection
        assert finding.remedy

    def test_multiple_findings_are_all_reported(self):
        findings = check_security_posture(
            _cfg(ALEXA_SKILL_ID="", AUTHORIZED_LINE_USER_IDS=[], ALLOW_ALL_ORIGINS=True)
        )
        assert {f.key for f in findings} == {
            "ALEXA_SKILL_ID",
            "AUTHORIZED_LINE_USER_IDS",
            "ALLOW_ALL_ORIGINS",
        }


class TestSwitchbotNeedsBothConditions:
    """トークン未設定**単独**は #648 でフェイルクローズ(503)になったため報告しない。

    報告するのは移行用オプトインが立っていて、無検証で受け付ける場合だけ。
    ここを「トークンが無ければ警告」に緩めると、正しくフェイルクローズしている
    構成に対して毎回警告が出ることになる。
    """

    def test_token_missing_alone_is_fail_closed_and_not_reported(self):
        findings = check_security_posture(
            _cfg(SWITCHBOT_WEBHOOK_TOKEN="", ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK=False)
        )
        assert findings == []

    def test_opt_in_alone_with_token_set_is_not_reported(self):
        """トークンがあるならオプトインは経路に影響しない(unified_server の分岐と同じ)。"""
        findings = check_security_posture(
            _cfg(SWITCHBOT_WEBHOOK_TOKEN="tok", ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK=True)
        )
        assert findings == []

    def test_token_missing_with_opt_in_is_reported(self):
        (finding,) = check_security_posture(
            _cfg(SWITCHBOT_WEBHOOK_TOKEN="", ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK=True)
        )
        assert finding.key == "ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK"
        assert finding.ref == "#648"


class TestIntentionallyUnsetIsNotReported:
    """意図的に未設定のまま運用している設定を報告しないこと。

    2026-09-20 時点の実機はこの状態(NAS_IP・SPEAKER_BLUETOOTH_MAC・
    YOUTUBE_COOKIES_FILE・DB_BACKUP_OFFSITE_REMOTE がいずれも未設定)で、
    それでも Security は OK でなければならない。これらを対象に加えると
    警告が常時鳴り、本当に無効な保護が埋もれる。
    """

    def test_operationally_disabled_settings_do_not_produce_findings(self):
        cfg = _cfg(
            NAS_IP="",
            SPEAKER_BLUETOOTH_MAC="",
            YOUTUBE_COOKIES_FILE=None,
            DB_BACKUP_OFFSITE_REMOTE="",
            ENABLE_BLUETOOTH=False,
        )
        assert check_security_posture(cfg) == []


class TestMissingAttributesAreTreatedAsUnset:
    """設定名自体が config に無い場合も「未設定」として扱うこと。

    `getattr(cfg, name, None)` で読んでいるため、config.py から定数が消えた場合に
    AttributeError で起動レポートごと落ちるのではなく、フェイルオープンとして
    報告される側に倒れる。
    """

    def test_empty_config_reports_the_fail_open_settings(self):
        findings = check_security_posture(SimpleNamespace())
        keys = {f.key for f in findings}
        assert "ALEXA_SKILL_ID" in keys
        assert "AUTHORIZED_LINE_USER_IDS" in keys
        # 属性が無い=オプトインも False 扱いなので SwitchBot は報告されない
        assert "ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK" not in keys
        assert "ALLOW_ALL_ORIGINS" not in keys


class TestFormatFindings:
    def test_summary_lists_keys_and_refs(self):
        findings = check_security_posture(_cfg(ALEXA_SKILL_ID="", AUTHORIZED_LINE_USER_IDS=[]))
        summary = format_findings(findings)
        assert summary.startswith("2件の保護が無効: ")
        assert "ALEXA_SKILL_ID(#319)" in summary
        assert "AUTHORIZED_LINE_USER_IDS(#799)" in summary

    def test_summary_does_not_leak_values(self):
        """設定名と Issue 番号だけを出し、値(トークン等)は載せないこと。"""
        findings = check_security_posture(_cfg(SWITCHBOT_WEBHOOK_TOKEN="", ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK=True))
        assert "tok" not in format_findings(findings)
