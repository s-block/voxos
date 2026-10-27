class IntegrationError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class IntegrationNotFoundError(IntegrationError):
    def __init__(self) -> None:
        super().__init__("Integration was not found.")


class IntegrationCredentialError(IntegrationError):
    def __init__(self, message: str = "Integration credentials are invalid.") -> None:
        super().__init__(message)


class IntegrationResourceError(IntegrationError):
    def __init__(self, message: str = "Integration resource is invalid.") -> None:
        super().__init__(message)


class IntegrationWebhookError(IntegrationError):
    def __init__(self, message: str = "Integration webhook is invalid.") -> None:
        super().__init__(message)


class IntegrationUpstreamError(IntegrationError):
    def __init__(self, message: str = "Integration provider is temporarily unavailable.") -> None:
        super().__init__(message)


class IntegrationToolPermissionError(IntegrationError):
    def __init__(self, message: str = "Integration tool is not allowed.") -> None:
        super().__init__(message)
