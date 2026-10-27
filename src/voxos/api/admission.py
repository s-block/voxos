import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from voxos.api.settings import ApiSettings
from voxos.auth.models import AuthenticatedUser

ADMISSION_SCOPE_KEY = "voxos.api.admitted_user"
MAX_TRACKED_USERS = 10_000


@dataclass(slots=True)
class UserAdmission:
    tokens: float
    updated_at: float
    in_flight: int = 0


class ApiAdmissionController:
    """Bound per-process work using verified local user IDs and monotonic budgets."""

    def __init__(self, settings: ApiSettings, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._clock = clock
        self._users: OrderedDict[str, UserAdmission] = OrderedDict()
        self._in_flight = 0

    def acquire_request(self) -> bool:
        if self._in_flight >= self._settings.api_max_in_flight:
            return False
        self._in_flight += 1
        return True

    def release_request(self) -> None:
        self._in_flight -= 1

    def admit_user(self, request: Request, user_id: str) -> None:
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"} and not request.url.path.endswith("/stream"):
            return
        if ADMISSION_SCOPE_KEY in request.scope:
            return
        now = self._clock()
        admission = self._users.get(user_id)
        if admission is None:
            self._prune_idle_users(now)
            if len(self._users) >= MAX_TRACKED_USERS:
                raise HTTPException(503, "API admission capacity exhausted", headers={"Retry-After": "1"})
            admission = UserAdmission(float(self._settings.api_rate_limit_burst), now)
            self._users[user_id] = admission
        self._users.move_to_end(user_id)
        rate_per_second = self._settings.api_rate_limit_per_minute / 60
        admission.tokens = min(
            float(self._settings.api_rate_limit_burst),
            admission.tokens + max(0.0, now - admission.updated_at) * rate_per_second,
        )
        admission.updated_at = now
        if admission.in_flight >= self._settings.api_max_user_in_flight:
            raise HTTPException(429, "Too many concurrent requests", headers={"Retry-After": "1"})
        if admission.tokens < 1:
            retry_after = str(max(1, math.ceil((1 - admission.tokens) / rate_per_second)))
            raise HTTPException(429, "Request rate limit exceeded", headers={"Retry-After": retry_after})
        admission.tokens -= 1
        admission.in_flight += 1
        request.scope[ADMISSION_SCOPE_KEY] = user_id

    def release_user(self, scope: Scope) -> None:
        user_id = scope.pop(ADMISSION_SCOPE_KEY, None)
        if isinstance(user_id, str):
            self._users[user_id].in_flight -= 1

    def _prune_idle_users(self, now: float) -> None:
        if len(self._users) < MAX_TRACKED_USERS:
            return
        refill_seconds = self._settings.api_rate_limit_burst * 60 / self._settings.api_rate_limit_per_minute
        expired = [
            user_id
            for user_id, admission in self._users.items()
            if admission.in_flight == 0 and now - admission.updated_at >= refill_seconds
        ]
        for user_id in expired:
            del self._users[user_id]


@dataclass(frozen=True, slots=True)
class AdmissionAuthDependency:
    dependency: Callable[[Request], Awaitable[AuthenticatedUser]]
    controller: ApiAdmissionController

    async def __call__(self, request: Request) -> AuthenticatedUser:
        user = await self.dependency(request)
        self.controller.admit_user(request, user.user_id)
        return user


class ApiAdmissionMiddleware:
    def __init__(self, app: ASGIApp, controller: ApiAdmissionController) -> None:
        self._app = app
        self._controller = controller

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/health":
            await self._app(scope, receive, send)
            return
        if not self._controller.acquire_request():
            response = JSONResponse(
                {"detail": "API request capacity exhausted"},
                status_code=503,
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send)
        finally:
            self._controller.release_user(scope)
            self._controller.release_request()
