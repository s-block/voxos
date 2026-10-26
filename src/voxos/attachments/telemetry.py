import logging

import newrelic.agent

logger = logging.getLogger(__name__)

_KIB = 1024
_MIB = 1024 * _KIB


def record_upload_success(
    *,
    client_source: str,
    normalized_bytes: int,
    duration_ms: float,
) -> None:
    _record_custom_metric("Custom/Voxos/ChatImages/UploadSuccess", 1)
    _record_custom_event(
        "VoxosChatImageUpload",
        {
            "client_source": client_source,
            "duration_ms": round(duration_ms, 3),
            "normalized_byte_bucket": normalized_byte_bucket(normalized_bytes),
        },
    )


def record_validation_failure(*, client_source: str, code: str) -> None:
    _record_custom_metric("Custom/Voxos/ChatImages/ValidationFailure", 1)
    _record_custom_event(
        "VoxosChatImageValidationFailure",
        {
            "client_source": client_source,
            "failure_code": code,
        },
    )


def record_storage_latency(*, operation: str, duration_ms: float) -> None:
    _record_custom_metric(
        f"Custom/Voxos/ChatImages/Storage/{operation}Milliseconds",
        duration_ms,
    )


def record_provider_image_count(image_count: int) -> None:
    if image_count <= 0:
        return
    _record_custom_metric(
        "Custom/Voxos/ChatImages/ProviderImageCount",
        image_count,
    )


def normalized_byte_bucket(byte_size: int) -> str:
    if byte_size < 256 * _KIB:
        return "under_256_kib"
    if byte_size < _MIB:
        return "256_kib_to_1_mib"
    if byte_size < 5 * _MIB:
        return "1_mib_to_5_mib"
    return "5_mib_or_more"


def _record_custom_metric(name: str, value: int | float) -> None:
    try:
        newrelic.agent.record_custom_metric(name, value)
    except Exception:
        logger.debug("Chat image metric recording failed", exc_info=True)


def _record_custom_event(event_type: str, params: dict[str, object]) -> None:
    try:
        newrelic.agent.record_custom_event(event_type, params)
    except Exception:
        logger.debug("Chat image event recording failed", exc_info=True)
