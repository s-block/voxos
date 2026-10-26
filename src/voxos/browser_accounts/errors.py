from voxos.browser_accounts.models import BrowserAccountErrorCategory

PROVIDER_ERROR_MESSAGES = {
    BrowserAccountErrorCategory.PROVIDER_AUTH: "Browser provider authentication failed",
    BrowserAccountErrorCategory.PROVIDER_REQUEST_INVALID: ("Browser provider rejected invalid connection settings"),
    BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE: "Browser provider is unavailable",
    BrowserAccountErrorCategory.PROVIDER_REJECTED: "Browser provider rejected the operation",
    BrowserAccountErrorCategory.PROFILE_MISSING: "Browser login profile is missing",
    BrowserAccountErrorCategory.SESSION_MISSING: "Browser session is missing",
    BrowserAccountErrorCategory.SESSION_TIMEOUT: ("Browser task timed out before the provider returned a result"),
    BrowserAccountErrorCategory.SETUP_INCOMPLETE: "Browser account setup is incomplete",
    BrowserAccountErrorCategory.CLEANUP_FAILED: "Browser session cleanup failed",
    BrowserAccountErrorCategory.UNKNOWN: "Browser provider operation failed",
}


class BrowserAccountError(Exception):
    """Base error for safe browser-account failures."""


class BrowserAccountsDisabledError(BrowserAccountError):
    def __init__(self) -> None:
        super().__init__("Browser Accounts are not enabled")


class BrowserAccountNotFoundError(BrowserAccountError):
    def __init__(self) -> None:
        super().__init__("Browser account was not found")


class BrowserAccountConflictError(BrowserAccountError):
    def __init__(self, message: str = "Browser account state changed") -> None:
        super().__init__(message)


class BrowserAccountValidationError(BrowserAccountError):
    def __init__(self, message: str = "Browser account request is invalid") -> None:
        super().__init__(message)


class BrowserAccountAuthorizationError(BrowserAccountError):
    def __init__(self, message: str = "Browser account approval is required") -> None:
        super().__init__(message)


class BrowserAccountProviderConnectionError(BrowserAccountError):
    def __init__(
        self,
        message: str = "Browser provider connection is unavailable",
    ) -> None:
        super().__init__(message)


class BrowserAccountProviderToolRequiredError(
    BrowserAccountProviderConnectionError,
):
    def __init__(self, *, provider_label: str, required_tool: str) -> None:
        super().__init__(
            f"{provider_label} must allow {required_tool} before it can be used for Browser Accounts",
        )


class BrowserAccountProviderError(BrowserAccountError):
    def __init__(self, category: BrowserAccountErrorCategory) -> None:
        super().__init__(PROVIDER_ERROR_MESSAGES[category])
        self.category = category
