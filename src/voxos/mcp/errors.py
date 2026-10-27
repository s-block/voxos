class UserMcpError(Exception):
    """Base exception for user-configured MCP server operations."""


class McpServerNotFoundError(UserMcpError):
    def __init__(self, server_id: str) -> None:
        super().__init__(f"MCP server {server_id} was not found")
        self.server_id = server_id


class McpAuthRequiredError(UserMcpError):
    def __init__(self, server_id: str) -> None:
        super().__init__(f"MCP server {server_id} requires authentication")
        self.server_id = server_id


class McpInvalidAuthTypeError(UserMcpError):
    def __init__(self, server_id: str, auth_type: str) -> None:
        super().__init__(f"MCP server {server_id} does not support {auth_type} auth")
        self.server_id = server_id
        self.auth_type = auth_type


class McpOAuthConfigurationError(UserMcpError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class McpOAuthStateError(UserMcpError):
    def __init__(self) -> None:
        super().__init__("OAuth state is missing, expired, or already used")


class McpConnectionTestError(UserMcpError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class McpToolCallRejectedError(UserMcpError):
    def __init__(self, *, recovery_guidance: str | None = None) -> None:
        super().__init__("MCP server rejected the tool arguments")
        self.recovery_guidance = recovery_guidance


class McpPresetNotFoundError(UserMcpError):
    def __init__(self, preset_id: str) -> None:
        super().__init__(f"MCP preset {preset_id} was not found")
        self.preset_id = preset_id


class McpPresetConfigurationError(UserMcpError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class McpServerUrlPolicyError(UserMcpError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class McpServerLabelConflictError(UserMcpError):
    def __init__(self, label: str) -> None:
        super().__init__(f"MCP server label {label!r} is already in use")
        self.label = label
