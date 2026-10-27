class WhatsAppIntegrationError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class WhatsAppIntegrationNotFoundError(WhatsAppIntegrationError):
    def __init__(self) -> None:
        super().__init__("WhatsApp integration was not found.")


class WhatsAppSourceResourceError(WhatsAppIntegrationError):
    def __init__(self) -> None:
        super().__init__(
            "Select an active Twilio or Telnyx phone number in E.164 format.",
        )


class WhatsAppIntegrationConflictError(WhatsAppIntegrationError):
    def __init__(
        self,
        message: str = "This user already has a WhatsApp integration.",
    ) -> None:
        super().__init__(message)


class WhatsAppChallengeNotReadyError(WhatsAppIntegrationError):
    def __init__(self) -> None:
        super().__init__("WhatsApp link code is not ready yet.")


class WhatsAppMobileRegistrationNotFoundError(WhatsAppIntegrationError):
    def __init__(self) -> None:
        super().__init__("WhatsApp mobile registration was not found.")


class WhatsAppMobileRegistrationConflictError(
    WhatsAppIntegrationConflictError,
):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class WhatsAppFeatureDisabledError(WhatsAppIntegrationError):
    def __init__(self) -> None:
        super().__init__("WhatsApp linked-device integrations are disabled.")


class WhatsAppInboundRetryableError(RuntimeError):
    """The inbound event is valid but its durable session is not ready yet."""


class WhatsAppInboundUnavailableError(RuntimeError):
    """The inbound event belongs to a session that is no longer available."""
