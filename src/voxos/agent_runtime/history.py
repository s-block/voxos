import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

import orjson
from sqlalchemy import and_, case, exists, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession as SqlAlchemySession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery
from sqlmodel import SQLModel, col
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.effective_tool_policy import effective_tool_policy_from_json
from voxos.agent_runtime.history_models import (
    EvidenceKind,
    EvidenceReference,
    HistoryItem,
    HistoryQuery,
    HistoryReadRequest,
    HistoryResult,
)
from voxos.agent_runtime.records import (
    AgentRunRecord,
    AgentSessionRecord,
    AgentToolGrantRecord,
)
from voxos.agent_runtime.run_models import AgentRunCancelledError, AgentRunKind
from voxos.agent_runtime.tool_execution import ToolExecutionContext
from voxos.agent_runtime.tool_policy import ToolAuthorizationService, ToolGrantScope, ToolReference, ToolRisk
from voxos.db.session import AsyncSessionFactory

HistoryColumn = ColumnElement[object] | ColumnElement[str] | ColumnElement[bool] | ColumnElement[None]

ACCOUNT_LEARNING_TOOLS = frozenset({"search_agent_history", "read_agent_history", "record_agent_improvement"})
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|\b(?:sk-|sk_live_|gh[pousr]_|xox[baprs]-)[\w.-]+|"
    r"\beyJ[\w-]+\.[\w-]+\.[\w-]+|"
    r"[\"']?(?:api[_ -]?key|password|secret|token|authorization)[\"']?\s*[:=]\s*"
    r"(?:\"[^\"]*(?:\"|$)|'[^']*(?:'|$)|[^\s,;]+))"
)
_URL = re.compile(r"https?://[^\s<>\"']+")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_+/=-]{40,}\b")


def safe_excerpt(value: str | None, limit: int = 1200) -> str:
    if not value:
        return ""
    if "-----BEGIN" in value:
        return "[sensitive content withheld]"
    # Work on bounded input, retaining URL origin only (no credentials or path).
    text = _SECRET.sub("[redacted]", value[:12000])

    def origin(match: re.Match[str]) -> str:
        try:
            parsed = urlsplit(match.group())
            return f"{parsed.scheme}://{parsed.hostname or '[redacted]'}"
        except ValueError:
            return "[redacted URL]"

    return _LONG_TOKEN.sub("[redacted]", _URL.sub(origin, text))[:limit]


@dataclass(frozen=True, slots=True)
class TenantHistoryService:
    session_factory: AsyncSessionFactory
    authorization_service: ToolAuthorizationService

    async def validate_access_in_session(self, db: AsyncSession, context: ToolExecutionContext, tool_name: str) -> None:
        if context.parent_run_id is not None or context.session_id is None:
            message = "Account history requires an owner conversation"
            raise PermissionError(message)
        session = await db.get(AgentSessionRecord, context.session_id)
        if (
            session is None
            or session.user_id != context.user_id
            or session.deleted_at is not None
            or session.agent_id != context.agent_id
        ):
            message = "Account history is unavailable"
            raise PermissionError(message)
        if session.channel != "text" and not session.owner_authenticated:
            message = "Account history requires authenticated owner voice"
            raise PermissionError(message)
        policy_json = session.effective_tool_policy_json
        if context.run_id is not None:
            run = (
                await SqlAlchemySession.execute(
                    db, select(AgentRunRecord).where(col(AgentRunRecord.id) == context.run_id).with_for_update()
                )
            ).scalar_one_or_none()
            if (
                run is None
                or run.user_id != context.user_id
                or run.agent_id != context.agent_id
                or run.session_id != session.id
                or run.parent_run_id is not None
                or run.kind not in {AgentRunKind.TEXT_CHAT_TURN.value, AgentRunKind.REFLECTION.value}
                or run.redacted_at is not None
                or run.deleted_at is not None
            ):
                message = "Account history is unavailable for this run"
                raise PermissionError(message)
            if run.cancel_requested_at is not None or run.status in {"cancelling", "cancelled"}:
                raise AgentRunCancelledError(run.id)
            policy_json = run.effective_tool_policy_json
        elif session.channel == "text":
            message = "Account history requires a persisted owner run"
            raise PermissionError(message)
        policy = effective_tool_policy_from_json(policy_json)
        reference = ToolReference(tool_name=tool_name, tool_source=ToolGrantScope.INTERNAL, risk=ToolRisk.MEDIUM)
        if (
            policy is None
            or policy.user_id != context.user_id
            or policy.agent_id != context.agent_id
            or not policy.allows(reference)
        ):
            message = "Persisted policy denies account access"
            raise PermissionError(message)
        denied = (
            await SqlAlchemySession.execute(
                db,
                select(col(AgentToolGrantRecord.id))
                .where(
                    col(AgentToolGrantRecord.user_id) == context.user_id,
                    col(AgentToolGrantRecord.agent_id) == context.agent_id,
                    col(AgentToolGrantRecord.tool_name) == tool_name,
                    col(AgentToolGrantRecord.tool_source) == "internal",
                    col(AgentToolGrantRecord.status) == "denied",
                )
                .limit(1),
            )
        ).first()
        if denied is not None:
            message = "Account capability has been revoked"
            raise PermissionError(message)

    async def require_access(self, context: ToolExecutionContext, tool_name: str) -> None:
        async with self.session_factory() as db:
            await self.validate_access_in_session(db, context, tool_name)
        decision = await self.authorization_service.authorize_tool(
            scope=context.to_agent_scope(),
            reference=ToolReference(tool_name=tool_name, tool_source=ToolGrantScope.INTERNAL, risk=ToolRisk.MEDIUM),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)

    async def search(self, context: ToolExecutionContext, request: HistoryQuery) -> HistoryResult:
        await self.require_access(context, "search_agent_history")
        return await self.search_owned(user_id=context.user_id, request=request)

    async def search_owned(self, *, user_id: str, request: HistoryQuery) -> HistoryResult:
        cursor_data: dict[str, object] | None = None
        if request.cursor:
            try:
                cursor_data = cast(dict[str, object], orjson.loads(base64.urlsafe_b64decode(request.cursor)))
                if not isinstance(cursor_data, dict):
                    raise ValueError
                start = datetime.fromisoformat(str(cursor_data["start"]))
                end = datetime.fromisoformat(str(cursor_data["end"]))
                request = request.model_copy(update={"start": request.start or start, "end": request.end or end})
            except (ValueError, KeyError, orjson.JSONDecodeError) as exc:
                message = "History cursor is unavailable"
                raise ValueError(message) from exc
        start, end = request.window()
        query_hash = hashlib.sha256(
            orjson.dumps(
                {
                    "user": user_id,
                    **request.model_dump(mode="json", exclude={"cursor", "limit", "start", "end"}),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                }
            )
        ).hexdigest()
        sources = self._sources(user_id)
        statement = select(sources).where(sources.c.created_at >= start, sources.c.created_at < end)
        if request.category == "errors":
            statement = statement.where(
                sources.c.category.in_(
                    ("tool_failed", "run_failed", "run_lost", "session_failed", "runtime_error", "tool_denied")
                )
            )
        elif request.category != "all":
            kinds = {"transcripts": "transcript", "tools": "tool", "sessions": "session"}
            statement = statement.where(sources.c.kind == kinds[request.category])
        if request.channel:
            statement = statement.where(sources.c.channel == request.channel)
        if request.attribution:
            statement = statement.where(sources.c.attribution == request.attribution)
        if request.tool_name:
            statement = statement.where(sources.c.tool_name == request.tool_name)
        if cursor_data:
            try:
                if cursor_data["query"] != query_hash:
                    raise ValueError
                at = datetime.fromisoformat(str(cursor_data["at"]))
                kind, key = str(cursor_data["kind"]), str(cursor_data["key"])
                statement = statement.where(
                    or_(
                        sources.c.created_at > at,
                        and_(sources.c.created_at == at, sources.c.kind > kind),
                        and_(sources.c.created_at == at, sources.c.kind == kind, sources.c.key > key),
                    )
                )
            except (ValueError, KeyError) as exc:
                message = "History cursor is unavailable"
                raise ValueError(message) from exc
        statement = statement.order_by(sources.c.created_at, sources.c.kind, sources.c.key).limit(request.limit + 1)
        async with self.session_factory() as db:
            rows = (await SqlAlchemySession.execute(db, statement)).mappings().all()
        items = tuple(self._item(dict(row)) for row in rows[: request.limit])
        next_cursor = None
        if len(rows) > request.limit and items:
            last = items[-1]
            next_cursor = base64.urlsafe_b64encode(
                orjson.dumps(
                    {
                        "query": query_hash,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "at": last.created_at.isoformat(),
                        "kind": last.reference.kind.value,
                        "key": last.reference.key,
                    }
                )
            ).decode()
        return self.bound_result(
            HistoryResult(items=items, start=start, end=end, next_cursor=next_cursor, truncated=next_cursor is not None)
        )

    async def read(self, context: ToolExecutionContext, request: HistoryReadRequest) -> HistoryResult:
        await self.require_access(context, "read_agent_history")
        async with self.session_factory() as db:
            result = await self.resolve(db, user_id=context.user_id, references=request.references)
            items = list(result.items)
            context_truncated = False
            if request.include_context:
                sources = self._sources(context.user_id)
                conversations = {item.conversation for item in items}
                if conversations:
                    statement = (
                        select(sources)
                        .where(
                            sources.c.conversation.in_(conversations),
                            ~or_(
                                *(
                                    and_(
                                        sources.c.kind == item.reference.kind.value, sources.c.key == item.reference.key
                                    )
                                    for item in items
                                )
                            ),
                        )
                        .order_by(sources.c.created_at.desc(), sources.c.kind, sources.c.key)
                        .limit(101 - len(items))
                    )
                    rows = (await SqlAlchemySession.execute(db, statement)).mappings().all()
                    available = 100 - len(items)
                    context_truncated = len(rows) > available
                    items.extend(self._item(dict(row)) for row in rows[:available])
        return self.bound_result(
            result.model_copy(
                update={
                    "items": tuple(
                        sorted(items, key=lambda item: (item.created_at, item.reference.kind, item.reference.key))
                    ),
                    "truncated": context_truncated,
                }
            )
        )

    async def resolve(
        self, db: AsyncSession, *, user_id: str, references: tuple[EvidenceReference, ...]
    ) -> HistoryResult:
        sources = self._sources(user_id)
        statement = (
            select(sources)
            .where(or_(*(and_(sources.c.kind == ref.kind.value, sources.c.key == ref.key) for ref in references)))
            .limit(len(references))
        )
        rows = (await SqlAlchemySession.execute(db, statement)).mappings().all() if references else ()
        return self.bound_result(
            HistoryResult(
                items=tuple(self._item(dict(row)) for row in rows), unavailable=len(set(references)) - len(rows)
            )
        )

    @staticmethod
    def _sources(user_id: str) -> Subquery:
        s = SQLModel.metadata.tables["agent_sessions"]
        t = SQLModel.metadata.tables["agent_tool_events"]
        p = SQLModel.metadata.tables["agent_transcripts"]
        r = SQLModel.metadata.tables["agent_runs"]
        e = SQLModel.metadata.tables["agent_runtime_events"]
        c = SQLModel.metadata.tables["integration_call_sessions"]
        retained = and_(s.c.user_id == user_id, s.c.deleted_at.is_(None))
        call_count = (
            select(func.count())
            .select_from(c)
            .where(c.c.user_id == user_id, c.c.runtime_session_id == s.c.id)
            .scalar_subquery()
        )
        disclosed_call = exists(
            select(c.c.id).where(
                c.c.user_id == user_id, c.c.runtime_session_id == s.c.id, c.c.transcript_enabled.is_(True)
            )
        )
        redacted = exists(
            select(r.c.id)
            .where(r.c.user_id == user_id, r.c.session_id == s.c.id, r.c.redacted_at.is_not(None))
            .correlate(s)
        )
        speech = and_(
            ~redacted,
            or_(s.c.channel == "text", s.c.owner_authenticated.is_(True), and_(call_count == 1, disclosed_call)),
        )

        def projection(
            kind: str,
            key: HistoryColumn,
            at: HistoryColumn,
            category: HistoryColumn,
            summary: HistoryColumn,
            owner: HistoryColumn = literal(value=False),
            successful: HistoryColumn = literal(value=False),
            tool_name: HistoryColumn = literal(None),
        ) -> list[HistoryColumn]:
            return [
                literal(kind).label("kind"),
                key.label("key"),
                s.c.id.label("conversation"),
                s.c.agent_id.label("attribution"),
                s.c.channel,
                at.label("created_at"),
                category.label("category"),
                summary.label("summary"),
                owner.label("owner"),
                successful.label("successful"),
                (~speech).label("context_withheld"),
                tool_name.label("tool_name"),
            ]

        # Explicit column labels make one bounded SQL union rather than fetching each stream.
        tools = (
            select(
                *projection(
                    "tool",
                    t.c.id.label("key"),
                    t.c.created_at,
                    case((t.c.is_error, "tool_failed"), else_="tool_succeeded").label("category"),
                    case(
                        (
                            and_(speech, t.c.name.not_in(tuple(ACCOUNT_LEARNING_TOOLS))),
                            func.substr(t.c.output_text, 1, 1200),
                        ),
                        else_=literal("Tool outcome; conversation content withheld"),
                    ).label("summary"),
                    successful=(~t.c.is_error).label("successful"),
                    tool_name=t.c.name.label("tool_name"),
                )
            )
            .select_from(t.join(s, t.c.session_id == s.c.id))
            .where(retained, t.c.user_id == user_id)
        )
        transcripts = (
            select(
                *projection(
                    "transcript",
                    p.c.id,
                    p.c.created_at,
                    literal("transcript"),
                    func.substr(p.c.text, 1, 1200),
                    and_(
                        p.c.role == "user",
                        or_(p.c.owner_authenticated.is_(True), s.c.owner_authenticated.is_(True)),
                    ),
                    literal(value=False),
                )
            )
            .select_from(p.join(s, p.c.session_id == s.c.id))
            .where(retained, p.c.user_id == user_id, p.c.is_final.is_(True), speech)
        )
        runs = (
            select(
                *projection(
                    "run",
                    r.c.id,
                    func.coalesce(r.c.completed_at, r.c.created_at),
                    case((r.c.status == "failed", "run_failed"), (r.c.status == "lost", "run_lost"), else_=r.c.status),
                    case(
                        (speech, func.substr(r.c.error_message, 1, 1200)),
                        else_=literal("Run outcome; conversation content withheld"),
                    ),
                    successful=(r.c.status == "succeeded"),
                )
            )
            .select_from(r.join(s, r.c.session_id == s.c.id))
            .where(
                retained,
                r.c.user_id == user_id,
                r.c.redacted_at.is_(None),
                r.c.deleted_at.is_(None),
                r.c.kind != AgentRunKind.REFLECTION.value,
                r.c.status.in_(("succeeded", "failed", "cancelled", "lost")),
            )
        )
        sessions = select(
            *projection(
                "session",
                s.c.id,
                func.coalesce(s.c.ended_at, s.c.started_at),
                case((s.c.close_error_type.is_not(None), "session_failed"), else_=s.c.status),
                func.coalesce(s.c.close_error_type, s.c.status),
            )
        ).where(retained)
        # Mirrored tool/run errors and raw runtime payloads are deliberately excluded.
        errors = (
            select(
                *projection(
                    "error",
                    e.c.id,
                    e.c.created_at,
                    case((e.c.kind == "tool_denied", "tool_denied"), else_="runtime_error"),
                    e.c.kind,
                )
            )
            .select_from(e.join(s, e.c.session_id == s.c.id))
            .where(
                retained,
                e.c.user_id == user_id,
                e.c.kind.in_(("error", "tool_denied")),
            )
        )
        return union_all(tools, transcripts, runs, sessions, errors).subquery()

    @staticmethod
    def _item(row: dict[str, object]) -> HistoryItem:
        at = row["created_at"]
        if isinstance(at, str):
            at = datetime.fromisoformat(at)
        if isinstance(at, datetime):
            at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
        return HistoryItem(
            reference=EvidenceReference(kind=EvidenceKind(str(row["kind"])), key=str(row["key"])),
            conversation=str(row["conversation"]),
            attribution=cast(str | None, row["attribution"]),
            tool_name=safe_excerpt(cast(str | None, row["tool_name"]), 256) or None,
            channel=str(row["channel"]),
            created_at=cast(datetime, at),
            category=str(row["category"]),
            summary=safe_excerpt(cast(str | None, row["summary"])),
            owner_statement=bool(row["owner"]),
            successful=bool(row["successful"]),
            context_withheld=bool(row["context_withheld"]),
            excerpt_truncated=isinstance(row["summary"], str) and len(row["summary"]) >= 1200,
        )

    @staticmethod
    def bound_result(result: HistoryResult) -> HistoryResult:
        selected = result.items[:100]
        limit = min(1200, 12000 // max(len(selected), 1))
        items = tuple(
            item.model_copy(
                update={
                    "summary": item.summary[:limit],
                    "excerpt_truncated": item.excerpt_truncated or len(item.summary) > limit,
                }
            )
            for item in selected
        )
        return result.model_copy(
            update={
                "items": items,
                "truncated": result.truncated
                or len(selected) < len(result.items)
                or any(item.excerpt_truncated for item in items),
            }
        )
