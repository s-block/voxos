import re

E164_PHONE_NUMBER_PATTERN = re.compile(r"^\+[1-9]\d{1,14}$")


def normalize_e164_phone_number(
    value: str,
    *,
    field_name: str = "phone_number",
) -> str:
    """Trim and validate a phone number in unambiguous E.164 form."""
    normalized = value.strip()
    if E164_PHONE_NUMBER_PATTERN.fullmatch(normalized) is None:
        message = f"{field_name} must be E.164 formatted"
        raise ValueError(message)
    return normalized
