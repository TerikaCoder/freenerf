"""Extensible tool registry with schema validation and execution safety.

Tools are plain objects: a JSON-schema-like ``parameters`` mapping, a handler
taking one argument dict, a permission policy and a timeout. The registry is
the only place where tools actually execute, so permission checks, argument
validation, timeouts and error shaping cannot be skipped by the UI.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Callable

from .permissions import describe, evaluate_permission, redact


MAX_OUTPUT_CHARS = 40_000

_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class ToolError(Exception):
    """Raised by a tool handler for an expected, user-readable failure."""


@dataclass
class ToolResult:
    name: str
    kind: str = "ok"  # ok | error | denied | invalid | timeout | cancelled
    output: str = ""
    error: str = ""
    elapsed: float = 0.0
    policy: str = "read"
    permission_reason: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "ok"

    def text(self) -> str:
        if self.kind == "ok":
            return self.output
        if self.kind == "denied":
            return f"Permission denied: {self.error}"
        if self.kind == "invalid":
            return f"Invalid tool arguments for {self.name}: {self.error}"
        if self.kind == "timeout":
            return f"Tool timed out after {self.elapsed:.0f}s: {self.name}"
        if self.kind == "cancelled":
            return "Cancelled by user"
        return f"Tool error ({self.name}): {self.error}"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "elapsed": self.elapsed,
            "policy": self.policy,
        }


def _type_matches(value, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    python_type = _JSON_TYPES.get(expected)
    if python_type is None:
        return True
    if expected == "boolean":
        return isinstance(value, bool)
    return isinstance(value, python_type)


def validate_arguments(schema: dict, arguments: Any) -> list[str]:
    """Minimal JSON-schema validation for tool arguments."""
    errors: list[str] = []

    if not isinstance(arguments, dict):
        return ["arguments must be a JSON object"]

    schema = schema or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []

    for name in required:
        if name not in arguments:
            errors.append(f"missing required argument '{name}'")

    for name, value in arguments.items():
        spec = properties.get(name)
        if spec is None:
            if schema.get("additionalProperties") is False:
                errors.append(f"unexpected argument '{name}'")
            continue

        expected = spec.get("type")
        if expected and not _type_matches(value, expected):
            errors.append(
                f"argument '{name}' must be of type {expected}"
            )
            continue

        if "enum" in spec and value not in spec["enum"]:
            allowed = ", ".join(str(item) for item in spec["enum"])
            errors.append(f"argument '{name}' must be one of: {allowed}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                errors.append(
                    f"argument '{name}' must be >= {spec['minimum']}"
                )
            if "maximum" in spec and value > spec["maximum"]:
                errors.append(
                    f"argument '{name}' must be <= {spec['maximum']}"
                )

        if expected == "array" and isinstance(value, list):
            item_spec = spec.get("items") or {}
            item_type = item_spec.get("type")
            if item_type:
                for position, item in enumerate(value):
                    if not _type_matches(item, item_type):
                        errors.append(
                            f"argument '{name}[{position}]' must be of type "
                            f"{item_type}"
                        )

        if expected == "object" and isinstance(value, dict):
            errors.extend(validate_arguments(spec, value))

    return errors


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], Any]
    policy: str = "read"
    timeout: float = 60.0

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, tools=None):
        self._tools: dict[str, Tool] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="freenerf-tool"
        )

        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> Tool:
        if not tool.name or not isinstance(tool.name, str):
            raise ValueError("Tool name must be a non-empty string")
        if tool.name in self._tools:
            self._tools.pop(tool.name)
        self._tools[tool.name] = tool
        return tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict]:
        return [tool.spec() for tool in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def execute(
        self,
        name: str,
        args: Any,
        *,
        permission_mode: str = "normal",
        approve: Callable[[str, str], bool] | None = None,
        cancel_event=None,
    ) -> ToolResult:
        started = time.monotonic()
        tool = self.get(name)

        if tool is None:
            return ToolResult(
                name=name,
                kind="denied",
                error="Tool is not registered",
                elapsed=time.monotonic() - started,
            )

        if not isinstance(args, dict):
            return ToolResult(
                name=name,
                kind="invalid",
                error="arguments must be a JSON object",
                policy=tool.policy,
                elapsed=time.monotonic() - started,
            )

        errors = validate_arguments(tool.parameters, args)
        if errors:
            return ToolResult(
                name=name,
                kind="invalid",
                error="; ".join(errors),
                policy=tool.policy,
                elapsed=time.monotonic() - started,
            )

        decision, reason = evaluate_permission(
            permission_mode, name, args, policy=tool.policy
        )

        if decision == "deny":
            return ToolResult(
                name=name,
                kind="denied",
                error=reason,
                policy=tool.policy,
                permission_reason=reason,
                elapsed=time.monotonic() - started,
            )

        if decision == "ask":
            if approve is None:
                return ToolResult(
                    name=name,
                    kind="denied",
                    error=(
                        f"{reason}; no approval handler is available "
                        "in this mode"
                    ),
                    policy=tool.policy,
                    permission_reason=reason,
                    elapsed=time.monotonic() - started,
                )

            try:
                answered = approve(name, describe(name, args))
            except Exception as exc:  # noqa: BLE001 - fail closed
                return ToolResult(
                    name=name,
                    kind="denied",
                    error=f"Approval handler failed: {exc}",
                    policy=tool.policy,
                    permission_reason=reason,
                    elapsed=time.monotonic() - started,
                )

            if cancel_event is not None and cancel_event.is_set():
                return ToolResult(
                    name=name,
                    kind="cancelled",
                    policy=tool.policy,
                    elapsed=time.monotonic() - started,
                )
            if not answered:
                return ToolResult(
                    name=name,
                    kind="denied",
                    error="User denied operation",
                    policy=tool.policy,
                    permission_reason=reason,
                    elapsed=time.monotonic() - started,
                )

        future = self._executor.submit(tool.handler, args)
        deadline = started + max(1.0, float(tool.timeout))

        while True:
            if cancel_event is not None and cancel_event.is_set():
                future.cancel()
                return ToolResult(
                    name=name,
                    kind="cancelled",
                    policy=tool.policy,
                    elapsed=time.monotonic() - started,
                )

            try:
                value = future.result(timeout=0.1)
                break
            except FutureTimeoutError:
                if time.monotonic() >= deadline:
                    future.cancel()
                    return ToolResult(
                        name=name,
                        kind="timeout",
                        policy=tool.policy,
                        elapsed=time.monotonic() - started,
                    )
            except ToolError as exc:
                return ToolResult(
                    name=name,
                    kind="error",
                    error=redact(str(exc)),
                    policy=tool.policy,
                    elapsed=time.monotonic() - started,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the agent
                return ToolResult(
                    name=name,
                    kind="error",
                    error=redact(f"{type(exc).__name__}: {exc}"),
                    policy=tool.policy,
                    elapsed=time.monotonic() - started,
                )

        elapsed = time.monotonic() - started

        if value is None:
            output = ""
        elif isinstance(value, str):
            output = value
        else:
            try:
                output = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                output = str(value)

        if len(output) > MAX_OUTPUT_CHARS:
            omitted = len(output) - MAX_OUTPUT_CHARS
            output = (
                output[:MAX_OUTPUT_CHARS]
                + f"\n[output truncated: {omitted} characters omitted]"
            )

        return ToolResult(
            name=name,
            kind="ok",
            output=output,
            policy=tool.policy,
            permission_reason=reason,
            elapsed=elapsed,
        )
