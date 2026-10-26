import ipaddress
from urllib.parse import urlsplit

INVALID_ORIGIN = "origin is invalid"
ASCII_ORIGIN_REQUIRED = "origin must use an ASCII host without wildcards"
HTTPS_ORIGIN_REQUIRED = "origin must use https"
ORIGIN_USERINFO_FORBIDDEN = "origin must not contain user information"
ORIGIN_COMPONENTS_FORBIDDEN = "origin must not contain a path, query, or fragment"
ORIGIN_HOST_REQUIRED = "origin must contain a host"
PUBLIC_HOST_REQUIRED = "origin must use a fully qualified public hostname"
HOSTNAME_NOT_IP_REQUIRED = "origin must use a hostname, not an IP address"
ORIGIN_HOST_INVALID = "origin host is invalid"
NORMALIZED_ASCII_HOST_REQUIRED = "origin host must be normalized ASCII"
ORIGIN_PORT_INVALID = "origin port is invalid"
STANDARD_HTTPS_PORT_REQUIRED = "origin must use the standard HTTPS port"
NORMALIZED_ORIGIN_HOST_REQUIRED = "normalized origin is missing a hostname"


def normalize_browser_origin(value: str) -> str:
    stripped = value.strip()
    if not stripped or any(ord(character) < 32 for character in stripped):
        raise ValueError(INVALID_ORIGIN)
    if not stripped.isascii() or "*" in stripped:
        raise ValueError(ASCII_ORIGIN_REQUIRED)

    parsed = urlsplit(stripped)
    if parsed.scheme.lower() != "https":
        raise ValueError(HTTPS_ORIGIN_REQUIRED)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(ORIGIN_USERINFO_FORBIDDEN)
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError(ORIGIN_COMPONENTS_FORBIDDEN)

    hostname = parsed.hostname
    if hostname is None:
        raise ValueError(ORIGIN_HOST_REQUIRED)
    normalized_host = hostname.lower().rstrip(".")
    if normalized_host != hostname.lower() or "." not in normalized_host:
        raise ValueError(PUBLIC_HOST_REQUIRED)
    try:
        ipaddress.ip_address(normalized_host)
    except ValueError:
        pass
    else:
        raise ValueError(HOSTNAME_NOT_IP_REQUIRED)
    try:
        encoded_host = normalized_host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(ORIGIN_HOST_INVALID) from exc
    if encoded_host != normalized_host:
        raise ValueError(NORMALIZED_ASCII_HOST_REQUIRED)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(ORIGIN_PORT_INVALID) from exc
    if port not in {None, 443}:
        raise ValueError(STANDARD_HTTPS_PORT_REQUIRED)
    return f"https://{normalized_host}"


def origin_hostname(origin: str) -> str:
    hostname = urlsplit(origin).hostname
    if hostname is None:
        raise ValueError(NORMALIZED_ORIGIN_HOST_REQUIRED)
    return hostname
