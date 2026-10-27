import re
from collections.abc import Mapping
from typing import TypeGuard

from voxos.utils.phone_numbers import normalize_e164_phone_number

WHATSAPP_ONE_TO_ONE_JID_PATTERN = re.compile(
    r"^(?:[1-9][0-9]{5,19})(?::[0-9]{1,3})?@"
    r"(?:s\.whatsapp\.net|lid|hosted|hosted\.lid)$",
)
WHATSAPP_PN_JID_PATTERN = re.compile(
    r"^([1-9][0-9]{5,14})(?::[0-9]{1,3})?@"
    r"(s\.whatsapp\.net|hosted)$",
)
WHATSAPP_LID_JID_PATTERN = re.compile(
    r"^([1-9][0-9]{5,19})(?::[0-9]{1,3})?@"
    r"(lid|hosted\.lid)$",
)
WHATSAPP_GROUP_JID_PATTERN = re.compile(
    r"^(?:[1-9][0-9]{5,19})(?:-[0-9]{1,20})?@g\.us$",
)


def is_whatsapp_one_to_one_jid(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and WHATSAPP_ONE_TO_ONE_JID_PATTERN.fullmatch(value) is not None


def is_whatsapp_group_jid(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and WHATSAPP_GROUP_JID_PATTERN.fullmatch(value) is not None


def is_whatsapp_chat_jid(value: object) -> TypeGuard[str]:
    return is_whatsapp_one_to_one_jid(value) or is_whatsapp_group_jid(value)


def normalize_whatsapp_pn_jid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = WHATSAPP_PN_JID_PATTERN.fullmatch(value)
    if match is None:
        return None
    return f"{match.group(1)}@{match.group(2)}"


def normalize_whatsapp_lid_jid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = WHATSAPP_LID_JID_PATTERN.fullmatch(value)
    if match is None:
        return None
    return f"{match.group(1)}@{match.group(2)}"


def phone_number_from_whatsapp_pn_jid(value: object) -> str | None:
    normalized = normalize_whatsapp_pn_jid(value)
    if normalized is None:
        return None
    digits = normalized.split("@", maxsplit=1)[0]
    try:
        return normalize_e164_phone_number(f"+{digits}")
    except ValueError:
        return None


def whatsapp_pn_jid_from_phone_number(value: object) -> str:
    if not isinstance(value, str):
        message = "phone_number must be a string"
        raise ValueError(message)
    phone_number = normalize_e164_phone_number(value)
    return f"{phone_number.removeprefix('+')}@s.whatsapp.net"


def whatsapp_target_jid_from_payload(payload: Mapping[str, object]) -> str | None:
    chat_jid = payload.get("chat_jid", payload.get("remote_jid"))
    return chat_jid if is_whatsapp_chat_jid(chat_jid) else None


def whatsapp_conversation_peer_from_payload(payload: Mapping[str, object]) -> str | None:
    conversation_peer = payload.get("conversation_peer")
    return conversation_peer if is_whatsapp_chat_jid(conversation_peer) else None
