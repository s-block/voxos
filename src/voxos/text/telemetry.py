import logging
from collections.abc import Mapping

import newrelic.agent
import orjson

logger = logging.getLogger(__name__)


def record_text_timing(
    event_type: str,
    attributes: Mapping[str, bool | float | int | str | None],
) -> None:
    """Record content-free text-chat timing in logs and application telemetry."""

    safe_attributes = {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in attributes.items()
        if value is not None
    }
    logger.info(
        "text chat timing %s",
        orjson.dumps(
            {
                "event_type": event_type,
                **safe_attributes,
            },
            option=orjson.OPT_SORT_KEYS,
        ).decode(),
    )
    try:
        newrelic.agent.record_custom_event(event_type, safe_attributes)
    except Exception:
        logger.debug("Text chat timing metric recording failed", exc_info=True)
