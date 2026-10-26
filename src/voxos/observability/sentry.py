"""Error and performance reporting without tenant payloads."""

import asyncio
import logging

import sentry_sdk
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sentry_sdk.integrations.asyncio import enable_asyncio_integration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.types import Event, Hint, SamplingContext


class SentrySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    dsn: str | None = Field(default=None, alias="SENTRY_DSN", repr=False)
    environment: str = Field(default="development", alias="SENTRY_ENVIRONMENT", min_length=1)
    release: str | None = Field(default=None, alias="SENTRY_RELEASE")
    deployment_revision: str | None = Field(default=None, alias="DEPLOYMENT_REVISION")
    traces_sample_rate: float = Field(default=0.01, ge=0, le=1, alias="SENTRY_TRACES_SAMPLE_RATE")

    @field_validator("dsn", "release", "deployment_revision", mode="before")
    @classmethod
    def strip_optional_value(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


def sanitize_event(event: Event, hint: Hint) -> Event:
    """Keep exception types, code locations and timing, never arbitrary payloads.

    Exception messages and breadcrumbs can contain provider responses, OAuth
    codes, chat text or tenant URLs even with the SDK's PII option disabled.
    """
    event.pop("request", None)
    event.pop("user", None)
    event.pop("extra", None)
    event.pop("breadcrumbs", None)
    event.pop("message", None)
    event.pop("server_name", None)
    if "logentry" in event:
        event["logentry"] = {"message": "An error was logged"}
    event["tags"] = {"voxos.service": event.get("tags", {}).get("voxos.service", "backend")}
    contexts = event.get("contexts", {})
    event["contexts"] = {key: value for key, value in contexts.items() if key in ("trace", "runtime")}
    trace = event["contexts"].get("trace", {})
    trace.pop("data", None)
    trace.pop("description", None)
    exceptions = event.get("exception", {}).get("values", [])
    if isinstance(exceptions, list):
        for exception in exceptions:
            exception.pop("value", None)
            mechanism = exception.get("mechanism", {})
            mechanism.pop("data", None)
            frames = exception.get("stacktrace", {}).get("frames", [])
            if isinstance(frames, list):
                for frame in frames:
                    frame.pop("vars", None)
    if "transaction" in event:
        event["transaction"] = "backend.request"
    spans = event.get("spans", [])
    if isinstance(spans, list):
        for span in spans:
            span["description"] = span.get("op", "operation")
            span.pop("data", None)
    else:
        event.pop("spans", None)
    return event


def initialize_sentry(service: str, settings: SentrySettings | None = None) -> None:
    if not sentry_sdk.is_initialized():
        resolved = settings or SentrySettings()
        if resolved.dsn is None:
            return

        def sample_trace(context: SamplingContext) -> float:
            scope = context.get("asgi_scope", {})
            if isinstance(scope, dict) and scope.get("path") == "/health":
                return 0.0
            return resolved.traces_sample_rate

        sentry_sdk.init(
            dsn=resolved.dsn,
            environment=resolved.environment,
            release=resolved.release or resolved.deployment_revision,
            sample_rate=1.0,
            traces_sample_rate=resolved.traces_sample_rate,
            traces_sampler=sample_trace,
            profiles_sample_rate=0.0,
            enable_logs=False,
            send_default_pii=False,
            include_local_variables=False,
            attach_stacktrace=True,
            max_request_body_size="never",
            max_breadcrumbs=0,
            auto_session_tracking=False,
            # Explicit integrations avoid automatic AI prompt/response collection.
            auto_enabling_integrations=False,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(transaction_style="endpoint"),
                LoggingIntegration(level=None, event_level=logging.ERROR),
            ],
            trace_propagation_targets=[],
            before_send=sanitize_event,
            before_send_transaction=sanitize_event,
        )
        sentry_sdk.set_tag("voxos.service", service)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    # SDK integration setup is process-wide; task factories must be enabled per loop.
    enable_asyncio_integration(task_spans=False)
