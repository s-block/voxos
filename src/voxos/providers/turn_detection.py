from google.genai import types as google_types
from openai.types.beta.realtime.session import TurnDetection

from voxos.providers.base import RealtimeVoiceSessionOptions

_DEFAULT_VAD_THRESHOLD = 0.65
_TELEPHONY_VAD_THRESHOLD = 0.7
_DEFAULT_PREFIX_PADDING_MS = 300
_DEFAULT_SILENCE_DURATION_MS = 500
_TELEPHONY_SILENCE_DURATION_MS = 400
_DEFAULT_GEMINI_PREFIX_PADDING_MS = 200
_TELEPHONY_GEMINI_PREFIX_PADDING_MS = 250


def openai_compatible_turn_detection(
    session_options: RealtimeVoiceSessionOptions,
) -> TurnDetection:
    return TurnDetection(
        type="server_vad",
        threshold=(_TELEPHONY_VAD_THRESHOLD if session_options.telephony else _DEFAULT_VAD_THRESHOLD),
        prefix_padding_ms=_DEFAULT_PREFIX_PADDING_MS,
        silence_duration_ms=(
            _TELEPHONY_SILENCE_DURATION_MS if session_options.telephony else _DEFAULT_SILENCE_DURATION_MS
        ),
        create_response=True,
        interrupt_response=True,
    )


def gemini_realtime_input_config(
    session_options: RealtimeVoiceSessionOptions,
) -> google_types.RealtimeInputConfig:
    return google_types.RealtimeInputConfig(
        automatic_activity_detection=google_types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=google_types.StartSensitivity.START_SENSITIVITY_LOW,
            end_of_speech_sensitivity=google_types.EndSensitivity.END_SENSITIVITY_LOW,
            prefix_padding_ms=(
                _TELEPHONY_GEMINI_PREFIX_PADDING_MS if session_options.telephony else _DEFAULT_GEMINI_PREFIX_PADDING_MS
            ),
            silence_duration_ms=(
                _TELEPHONY_SILENCE_DURATION_MS if session_options.telephony else _DEFAULT_SILENCE_DURATION_MS
            ),
        ),
        activity_handling=google_types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
    )
