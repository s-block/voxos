from voxos.agent_core.response_profiles import (
    CommunicationMedium,
    communication_medium_for_channel,
    render_response_profile_instruction,
    response_profile,
    response_profile_prompt_snapshot,
)


def test_response_profiles_define_distinct_medium_contracts() -> None:
    web = response_profile(CommunicationMedium.WEB_CHAT)
    sms = response_profile(CommunicationMedium.SMS)
    whatsapp = response_profile(CommunicationMedium.WHATSAPP)
    voice = response_profile(CommunicationMedium.VOICE)

    assert web.supports_markdown is True
    assert web.max_output_tokens is not None
    assert sms.supports_markdown is False
    assert sms.max_output_tokens < web.max_output_tokens
    assert "one or two short sentences" in sms.instruction
    assert whatsapp.max_output_tokens > sms.max_output_tokens
    assert "one to three short paragraphs" in whatsapp.instruction
    assert "30 to 50 spoken words" in voice.instruction


def test_channel_mapping_defaults_unknown_channels_to_web_chat() -> None:
    assert communication_medium_for_channel("sms") == CommunicationMedium.SMS
    assert communication_medium_for_channel("whatsapp") == CommunicationMedium.WHATSAPP
    assert communication_medium_for_channel("livekit") == CommunicationMedium.VOICE
    assert communication_medium_for_channel("text") == CommunicationMedium.WEB_CHAT
    assert communication_medium_for_channel("custom") == CommunicationMedium.WEB_CHAT


def test_rendered_profile_instruction_includes_stable_version() -> None:
    instruction = render_response_profile_instruction(CommunicationMedium.SMS)

    assert instruction.endswith("Response profile: sms:v1.")


def test_rendered_profile_instruction_enforces_markdown_capability() -> None:
    web_instruction = render_response_profile_instruction(CommunicationMedium.WEB_CHAT)

    assert "Markdown is supported" in web_instruction
    assert "descriptive Markdown links with complete https:// URLs" in web_instruction
    for medium in (
        CommunicationMedium.SMS,
        CommunicationMedium.WHATSAPP,
        CommunicationMedium.VOICE,
    ):
        instruction = render_response_profile_instruction(medium)
        assert "Reply in plain text only" in instruction
        assert "Do not use Markdown syntax" in instruction
        assert "asterisks and underscores" in instruction


def test_prompt_snapshot_records_medium_profile_version() -> None:
    snapshot = response_profile_prompt_snapshot("Be helpful.", CommunicationMedium.SMS)

    assert snapshot == "Be helpful.\n\nResponse profile: sms:v1."
