import re
from dataclasses import dataclass
from typing import TypeAlias

from jsonschema import validators
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import BaseModel, ConfigDict, Field
from referencing.exceptions import Unresolvable

from voxos.agent_core.models import AgentRunContext
from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.models import JsonObject

JsonPath: TypeAlias = tuple[str, ...]
JSON_PATH_INDEX_WILDCARD = "*"

AUTHORITY_ARGUMENT_KEYS = frozenset(
    {
        "agent_id",
        "api_key",
        "authorization",
        "browser_account_id",
        "browser_profile_id",
        "browser_session_id",
        "chat_id",
        "context_id",
        "credential_id",
        "headers",
        "mcp_server_id",
        "owner_user_id",
        "profile_id",
        "run_id",
        "secret",
        "session_id",
        "token",
        "url",
        "user_id",
    },
)
AUTHORITY_ARGUMENT_ALIASES = frozenset(
    {
        "access_token",
        "apikey",
        "auth_header",
        "bearer_token",
        "credential",
        "credentials",
        "browser_account",
        "browser_context",
        "browser_profile",
        "header",
        "oauth_token",
        "refresh_token",
        "secret_ref",
        "secret_reference",
        "server_id",
    },
)
NORMALIZED_AUTHORITY_KEYS = AUTHORITY_ARGUMENT_KEYS | AUTHORITY_ARGUMENT_ALIASES
COMPACT_AUTHORITY_KEYS = frozenset(re.sub(r"[^a-z0-9]+", "", key) for key in NORMALIZED_AUTHORITY_KEYS)
AUTHORITY_KEY_SUFFIXES = (
    "_agent_id",
    "_browser_account_id",
    "_context_id",
    "_credential_id",
    "_profile_id",
    "_run_id",
    "_server_id",
    "_session_id",
    "_user_id",
)
KEY_SEPARATOR_PATTERN = re.compile(r"[^a-z0-9]+")
CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<!^)(?=[A-Z])")


class ToolExecutionSecurityError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ToolAuthorityArgumentError(ToolExecutionSecurityError):
    def __init__(self, path: JsonPath) -> None:
        joined_path = ".".join(path)
        message = f"Tool arguments may not include authority-bearing field: {joined_path}"
        super().__init__(message)
        self.path = path


class ToolArgumentSchemaError(ValueError):
    def __init__(
        self,
        *,
        path: JsonPath,
        validation_keyword: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.validation_keyword = validation_keyword


class ToolSchemaDefinitionError(ValueError):
    def __init__(self) -> None:
        super().__init__("Tool input schema is invalid")


class ToolExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    root_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    channel: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=128)
    effective_tool_policy_id: str | None = Field(default=None, min_length=1, max_length=36)

    @classmethod
    def from_run_context(
        cls,
        context: AgentRunContext,
        *,
        source: str,
        require_run: bool = False,
        effective_tool_policy_id: str | None = None,
        channel: str = "text",
    ) -> "ToolExecutionContext":
        if require_run and context.run_id is None:
            message = "Tool execution requires a persisted run context"
            raise ToolExecutionSecurityError(message)
        return cls(
            user_id=context.user_id,
            agent_id=context.agent_id,
            run_id=context.run_id,
            session_id=context.session_id,
            root_run_id=context.root_run_id,
            parent_run_id=context.parent_run_id,
            channel=channel,
            source=source,
            effective_tool_policy_id=effective_tool_policy_id or context.effective_tool_policy_id,
        )

    def to_agent_scope(self) -> AgentScope:
        return AgentScope(
            owner_user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            run_id=self.run_id,
            channel=self.channel,
            source=self.source,
        )


@dataclass(frozen=True, slots=True)
class ToolArgumentAuthorityValidator:
    allowed_paths: frozenset[JsonPath] = frozenset()

    def validate(self, arguments_json: JsonObject) -> None:
        self._validate_value(arguments_json, ())

    def _validate_value(self, value: object, path: JsonPath) -> None:
        if isinstance(value, dict):
            for key, child_value in value.items():
                key_text = str(key)
                child_path = (*path, key_text)
                if not self._is_allowed_path(child_path) and is_authority_argument_key(
                    key_text,
                ):
                    raise ToolAuthorityArgumentError(child_path)
                self._validate_value(child_value, child_path)
            return
        if isinstance(value, list | tuple):
            for index, child_value in enumerate(value):
                self._validate_value(child_value, (*path, str(index)))

    def _is_allowed_path(self, path: JsonPath) -> bool:
        return any(
            len(candidate) == len(path)
            and all(
                expected == actual or (expected == JSON_PATH_INDEX_WILDCARD and actual.isdecimal())
                for expected, actual in zip(candidate, path, strict=True)
            )
            for candidate in self.allowed_paths
        )


def validate_llm_tool_arguments(
    arguments_json: JsonObject,
    *,
    allowed_paths: frozenset[JsonPath] = frozenset(),
) -> None:
    ToolArgumentAuthorityValidator(allowed_paths=allowed_paths).validate(arguments_json)


def validate_tool_arguments_schema(
    arguments_json: JsonObject,
    parameters_schema: JsonObject,
) -> None:
    if not parameters_schema:
        return
    validator_class = validators.validator_for(parameters_schema)
    try:
        validator_class.check_schema(parameters_schema)
        error = next(
            validator_class(parameters_schema).iter_errors(arguments_json),
            None,
        )
    except (SchemaError, Unresolvable) as exc:
        raise ToolSchemaDefinitionError from exc
    if error is None:
        return
    raise tool_argument_schema_error(error)


def prune_unsupported_tool_arguments(
    arguments_json: JsonObject,
    parameters_schema: JsonObject,
) -> JsonObject:
    """Return arguments with schema-declared unsupported fields removed."""
    if not parameters_schema:
        return dict(arguments_json)
    pruned = _prune_unsupported_schema_value(
        arguments_json,
        parameters_schema,
        root_schema=parameters_schema,
        resolved_references=frozenset(),
    )
    return pruned if isinstance(pruned, dict) else dict(arguments_json)


def _prune_unsupported_schema_value(
    value: object,
    schema: JsonObject,
    *,
    root_schema: JsonObject,
    resolved_references: frozenset[str],
) -> object:
    resolved_schema, references = _resolve_local_schema_reference(
        schema,
        root_schema=root_schema,
        resolved_references=resolved_references,
    )
    if isinstance(value, dict):
        properties = resolved_schema.get("properties")
        property_schemas = properties if isinstance(properties, dict) else {}
        additional_properties = resolved_schema.get("additionalProperties", True)
        pruned: JsonObject = {}
        for key, child in value.items():
            child_schema = property_schemas.get(key)
            if isinstance(child_schema, dict):
                pruned[key] = _prune_unsupported_schema_value(
                    child,
                    child_schema,
                    root_schema=root_schema,
                    resolved_references=references,
                )
                continue
            if additional_properties is False:
                continue
            if isinstance(additional_properties, dict):
                pruned[key] = _prune_unsupported_schema_value(
                    child,
                    additional_properties,
                    root_schema=root_schema,
                    resolved_references=references,
                )
                continue
            pruned[key] = child
        return pruned
    if isinstance(value, list):
        items = resolved_schema.get("items")
        if isinstance(items, dict):
            return [
                _prune_unsupported_schema_value(
                    item,
                    items,
                    root_schema=root_schema,
                    resolved_references=references,
                )
                for item in value
            ]
    return value


def _resolve_local_schema_reference(
    schema: JsonObject,
    *,
    root_schema: JsonObject,
    resolved_references: frozenset[str],
) -> tuple[JsonObject, frozenset[str]]:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/") or reference in resolved_references:
        return schema, resolved_references
    target = local_schema_reference_target(root_schema, reference)
    if target is None:
        return schema, resolved_references
    references = resolved_references | {reference}
    merged = dict(target)
    merged.update({key: value for key, value in schema.items() if key != "$ref"})
    return _resolve_local_schema_reference(
        merged,
        root_schema=root_schema,
        resolved_references=references,
    )


def local_schema_reference_target(
    root_schema: JsonObject,
    reference: str,
) -> JsonObject | None:
    target: object = root_schema
    for raw_segment in reference[2:].split("/"):
        segment = raw_segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or segment not in target:
            return None
        target = target[segment]
    return target if isinstance(target, dict) else None


def inline_local_schema_references(schema: JsonObject) -> JsonObject:
    inlined = _inline_local_schema_value(
        schema,
        root_schema=schema,
        resolving=frozenset(),
    )
    if not isinstance(inlined, dict):
        message = "A model input schema must be an object"
        raise ValueError(message)
    return inlined


def _inline_local_schema_value(
    value: object,
    *,
    root_schema: JsonObject,
    resolving: frozenset[str],
) -> object:
    if isinstance(value, list):
        return [
            _inline_local_schema_value(
                item,
                root_schema=root_schema,
                resolving=resolving,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        if reference in resolving:
            message = f"Recursive model input schema reference is not supported: {reference}"
            raise ValueError(message)
        target = local_schema_reference_target(root_schema, reference)
        if target is None:
            message = f"Model input schema reference does not exist: {reference}"
            raise ValueError(message)
        merged = {
            **target,
            **{key: child for key, child in value.items() if key != "$ref"},
        }
        return _inline_local_schema_value(
            merged,
            root_schema=root_schema,
            resolving=resolving | {reference},
        )

    return {
        key: _inline_local_schema_value(
            child,
            root_schema=root_schema,
            resolving=resolving,
        )
        for key, child in value.items()
        if key != "$defs"
    }


def tool_argument_schema_error(error: ValidationError) -> ToolArgumentSchemaError:
    path = tuple(str(segment) for segment in error.absolute_path)
    location = ".".join(path) or "arguments"
    validation_keyword = str(error.validator)
    if validation_keyword == "required":
        missing_property = missing_required_property(error)
        if missing_property is not None:
            required_location = ".".join((*path, missing_property))
            return ToolArgumentSchemaError(
                path=(*path, missing_property),
                validation_keyword=validation_keyword,
                message=f"Tool argument {required_location} is required",
            )
    if validation_keyword == "type":
        expected_type = error.validator_value
        if isinstance(expected_type, str):
            return ToolArgumentSchemaError(
                path=path,
                validation_keyword=validation_keyword,
                message=f"Tool argument {location} must be {type_description(expected_type)}",
            )
    if validation_keyword == "additionalProperties":
        return ToolArgumentSchemaError(
            path=path,
            validation_keyword=validation_keyword,
            message=f"Tool argument {location} contains unsupported fields",
        )
    return ToolArgumentSchemaError(
        path=path,
        validation_keyword=validation_keyword,
        message=f"Tool argument {location} does not match the tool schema",
    )


def missing_required_property(error: ValidationError) -> str | None:
    required_properties = error.validator_value
    instance = error.instance
    if not isinstance(required_properties, list | tuple) or not isinstance(instance, dict):
        return None
    return next(
        (
            property_name
            for property_name in required_properties
            if isinstance(property_name, str) and property_name not in instance
        ),
        None,
    )


def type_description(schema_type: str) -> str:
    descriptions = {
        "array": "an array",
        "boolean": "a boolean",
        "integer": "an integer",
        "null": "null",
        "number": "a number",
        "object": "an object",
        "string": "a string",
    }
    return descriptions.get(schema_type, f"type {schema_type}")


def is_authority_argument_key(key: str) -> bool:
    normalized = normalize_authority_key(key)
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    return (
        normalized in NORMALIZED_AUTHORITY_KEYS
        or compact in COMPACT_AUTHORITY_KEYS
        or normalized.endswith(AUTHORITY_KEY_SUFFIXES)
    )


def normalize_authority_key(key: str) -> str:
    camel_split = CAMEL_BOUNDARY_PATTERN.sub("_", key)
    return KEY_SEPARATOR_PATTERN.sub("_", camel_split.lower()).strip("_")
