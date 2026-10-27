import pytest
from google.genai import types as google_types

from voxos.providers.base import RealtimeVoiceSessionOptions
from voxos.providers.turn_detection import (
    gemini_realtime_input_config,
    openai_compatible_turn_detection,
)


@pytest.mark.parametrize(
    ("telephony", "expected_threshold", "expected_silence_duration_ms"),
    (
        (False, 0.65, 500),
        (True, 0.7, 400),
    ),
)
def test_openai_compatible_turn_detection_uses_audio_source_profile(
    telephony: bool,
    expected_threshold: float,
    expected_silence_duration_ms: int,
) -> None:
    turn_detection = openai_compatible_turn_detection(
        RealtimeVoiceSessionOptions(telephony=telephony),
    )

    assert turn_detection.type == "server_vad"
    assert turn_detection.threshold == expected_threshold
    assert turn_detection.prefix_padding_ms == 300
    assert turn_detection.silence_duration_ms == expected_silence_duration_ms
    assert turn_detection.create_response is True
    assert turn_detection.interrupt_response is True


@pytest.mark.parametrize(
    ("telephony", "expected_prefix_padding_ms", "expected_silence_duration_ms"),
    (
        (False, 200, 500),
        (True, 250, 400),
    ),
)
def test_gemini_turn_detection_uses_audio_source_profile(
    telephony: bool,
    expected_prefix_padding_ms: int,
    expected_silence_duration_ms: int,
) -> None:
    realtime_input = gemini_realtime_input_config(
        RealtimeVoiceSessionOptions(telephony=telephony),
    )

    activity_detection = realtime_input.automatic_activity_detection
    assert activity_detection is not None
    assert activity_detection.disabled is False
    assert activity_detection.start_of_speech_sensitivity == google_types.StartSensitivity.START_SENSITIVITY_LOW
    assert activity_detection.end_of_speech_sensitivity == google_types.EndSensitivity.END_SENSITIVITY_LOW
    assert activity_detection.prefix_padding_ms == expected_prefix_padding_ms
    assert activity_detection.silence_duration_ms == expected_silence_duration_ms
    assert realtime_input.activity_handling == google_types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
