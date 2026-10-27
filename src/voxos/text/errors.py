class TextPreferencesNotFoundError(LookupError):
    def __init__(self, user_id: str) -> None:
        super().__init__(f"Text preferences were not found for user {user_id}")


class MissingTextCredentialError(LookupError):
    def __init__(self, credential_ref: str) -> None:
        super().__init__(f"Text provider credential was not found: {credential_ref}")


class InvalidTextConfigurationError(ValueError):
    @classmethod
    def invalid_preferences(cls, user_id: str) -> "InvalidTextConfigurationError":
        return cls(f"Text preferences are invalid for user {user_id}")
