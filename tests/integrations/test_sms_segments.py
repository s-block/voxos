from voxos.integrations.sms_segments import (
    SmsEncoding,
    analyze_sms_segments,
)


def test_gsm_7_single_and_concatenated_boundaries() -> None:
    one_segment = analyze_sms_segments("a" * 160)
    two_segments = analyze_sms_segments("a" * 161)
    ten_segments = analyze_sms_segments("a" * 1530)
    too_many_segments = analyze_sms_segments("a" * 1531)

    assert one_segment.encoding == SmsEncoding.GSM_7
    assert one_segment.segments == 1
    assert two_segments.segments == 2
    assert ten_segments.segments == 10
    assert ten_segments.within_provider_limit is True
    assert too_many_segments.segments == 11
    assert too_many_segments.within_provider_limit is False


def test_gsm_extension_characters_count_as_two_units() -> None:
    analysis = analyze_sms_segments("^" * 80)

    assert analysis.encoding == SmsEncoding.GSM_7
    assert analysis.units == 160
    assert analysis.segments == 1


def test_unicode_boundaries_count_utf_16_code_units() -> None:
    one_segment = analyze_sms_segments("é" * 70 + "🙂")
    two_segments = analyze_sms_segments("漢" * 71)
    emoji = analyze_sms_segments("🙂" * 35)

    assert one_segment.encoding == SmsEncoding.UTF_16
    assert one_segment.units == 72
    assert one_segment.segments == 2
    assert two_segments.segments == 2
    assert emoji.units == 70
    assert emoji.segments == 1


def test_empty_sms_has_no_segments() -> None:
    analysis = analyze_sms_segments("")

    assert analysis.units == 0
    assert analysis.segments == 0
    assert analysis.within_provider_limit is True
