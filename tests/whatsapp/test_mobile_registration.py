import pytest

from voxos.whatsapp.mobile_registration import (
    extract_spoken_verification_code,
    extract_verification_code,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Your WhatsApp code is 123456", "123456"),
        ("Your WhatsApp code is 123-456", "123456"),
        ("Código de WhatsApp: 123 456", "123456"),
        ("Code 1234", "1234"),
        ("Verification: 1234 5678", "12345678"),
    ],
)
def test_extract_verification_code_accepts_common_formats(
    message: str,
    expected: str,
) -> None:
    assert extract_verification_code(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "Your WhatsApp verification is ready",
        "Call +44 7700 900123 for help",
        "Reference 123456789",
        "",
    ],
)
def test_extract_verification_code_rejects_non_codes(message: str) -> None:
    assert extract_verification_code(message) is None


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("Your code is one two three four five six", "123456"),
        ("The code is one, two, three. Four five six.", "123456"),
        ("Code: one two 3 four five 6", "123456"),
        ("Your code is 123 456", "123456"),
    ],
)
def test_extract_spoken_verification_code_accepts_voice_transcripts(
    transcript: str,
    expected: str,
) -> None:
    assert extract_spoken_verification_code(transcript) == expected


@pytest.mark.parametrize(
    "transcript",
    [
        "Your WhatsApp verification call is ready",
        "Call plus four four seven seven zero zero nine zero zero one two three",
        "Reference one two three four five six seven eight nine",
        "",
    ],
)
def test_extract_spoken_verification_code_rejects_non_codes(
    transcript: str,
) -> None:
    assert extract_spoken_verification_code(transcript) is None
