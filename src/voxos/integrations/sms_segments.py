from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

GSM_7_BASIC_CHARACTERS = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà",
)
GSM_7_EXTENSION_CHARACTERS = frozenset("^{}\\[~]|€")
SINGLE_SEGMENT_GSM_UNITS = 160
CONCATENATED_GSM_UNITS = 153
SINGLE_SEGMENT_UNICODE_UNITS = 70
CONCATENATED_UNICODE_UNITS = 67
MAX_SMS_SEGMENTS = 10


class SmsEncoding(StrEnum):
    GSM_7 = "gsm_7"
    UTF_16 = "utf_16"


class SmsSegmentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    encoding: SmsEncoding
    units: int = Field(ge=0)
    segments: int = Field(ge=0)
    units_per_segment: int = Field(ge=1)
    remaining_units: int = Field(ge=0)
    within_provider_limit: bool


def analyze_sms_segments(text: str) -> SmsSegmentAnalysis:
    gsm_units = gsm_7_units(text)
    if gsm_units is not None:
        return segment_analysis(
            encoding=SmsEncoding.GSM_7,
            units=gsm_units,
            single_segment_units=SINGLE_SEGMENT_GSM_UNITS,
            concatenated_segment_units=CONCATENATED_GSM_UNITS,
        )
    unicode_units = len(text.encode("utf-16-be")) // 2
    return segment_analysis(
        encoding=SmsEncoding.UTF_16,
        units=unicode_units,
        single_segment_units=SINGLE_SEGMENT_UNICODE_UNITS,
        concatenated_segment_units=CONCATENATED_UNICODE_UNITS,
    )


def gsm_7_units(text: str) -> int | None:
    units = 0
    for character in text:
        if character in GSM_7_BASIC_CHARACTERS:
            units += 1
            continue
        if character in GSM_7_EXTENSION_CHARACTERS:
            units += 2
            continue
        return None
    return units


def segment_analysis(
    *,
    encoding: SmsEncoding,
    units: int,
    single_segment_units: int,
    concatenated_segment_units: int,
) -> SmsSegmentAnalysis:
    if units == 0:
        segments = 0
        units_per_segment = single_segment_units
        capacity = single_segment_units
    elif units <= single_segment_units:
        segments = 1
        units_per_segment = single_segment_units
        capacity = single_segment_units
    else:
        segments = (units + concatenated_segment_units - 1) // concatenated_segment_units
        units_per_segment = concatenated_segment_units
        capacity = segments * concatenated_segment_units
    within_provider_limit = segments <= MAX_SMS_SEGMENTS
    remaining_units = max(capacity - units, 0)
    return SmsSegmentAnalysis(
        encoding=encoding,
        units=units,
        segments=segments,
        units_per_segment=units_per_segment,
        remaining_units=remaining_units,
        within_provider_limit=within_provider_limit,
    )
