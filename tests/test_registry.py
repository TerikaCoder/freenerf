import threading
import time

import pytest

from freenerf.agents.registry import (
    Tool,
    ToolError,
    ToolRegistry,
    validate_arguments,
)


SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "count": {"type": "integer", "minimum": 1, "maximum": 5},
        "mode": {"type": "string", "enum": ["a", "b"]},
    },
    "required": ["text"],
}


def make_tool(handler=None, policy="read", timeout=5):
    return Tool(
        name="echo",
        description="Echo text",
        parameters=SCHEMA,
        handler=handler or (lambda args: args["text"]),
        policy=policy,
        timeout=timeout,
    )


def test_validate_arguments_accepts_valid_payload():
    assert validate_arguments(SCHEMA, {"text": "hi", "count": 2, "mode": "a"}) == []


def test_validate_arguments_reports_problems():
    errors = validate_arguments(SCHEMA, {"count": "x", "mode": "c"})
    joined = "; ".join(errors)

    assert "missing required argument 'text'" in joined
    assert "count" in joined
    assert "one of" in joined


def test_registry_executes_tool():
    registry = ToolRegistry([make_tool()])
    result = registry.execute("echo", {"text": "hello"})

    assert result.ok
    assert result.text() == "hello"
    assert result.elapsed >= 0


def test_registry_rejects_unknown_tool():
    registry = ToolRegistry()
    result = registry.execute("missing", {})

    assert result.kind == "denied"
    assert "not registered" in result.text()


def test_registry_rejects_invalid_arguments():
    registry = ToolRegistry([make_tool()])
    result = registry.execute("echo", {"count": 2})

    assert result.kind == "invalid"
    assert "Invalid tool arguments" in result.text()


def test_registry_denies_in_denied_mode():
    registry = ToolRegistry([make_tool()])
    result = registry.execute("echo", {"text": "x"}, permission_mode="denied")

    assert result.kind == "denied"
    assert result.text().startswith("Permission denied:")


def test_registry_requires_approval_in_normal_mode():
    registry = ToolRegistry([make_tool(policy="write")])

    denied = registry.execute("echo", {"text": "x"}, permission_mode="normal")
    assert denied.kind == "denied"
    assert "no approval handler" in denied.error

    calls = []
    approved = registry.execute(
        "echo",
        {"text": "x"},
        permission_mode="normal",
        approve=lambda name, detail: calls.append((name, detail)) or True,
    )
    assert approved.ok
    assert calls == [("echo", "text=x")]


def test_registry_respects_denied_approval():
    registry = ToolRegistry([make_tool(policy="write")])
    result = registry.execute(
        "echo",
        {"text": "x"},
        permission_mode="normal",
        approve=lambda name, detail: False,
    )
    assert result.kind == "denied"
    assert result.error == "User denied operation"


def test_registry_wraps_tool_errors():
    def handler(args):
        raise ToolError("expected failure")

    registry = ToolRegistry([make_tool(handler)])
    result = registry.execute("echo", {"text": "x"})

    assert result.kind == "error"
    assert result.error == "expected failure"


def test_registry_times_out_slow_tools():
    def slow(args):
        time.sleep(1.6)
        return "late"

    registry = ToolRegistry([make_tool(slow, timeout=1)])
    started = time.monotonic()
    result = registry.execute("echo", {"text": "x"})

    assert result.kind == "timeout"
    assert time.monotonic() - started < 1.5


def test_registry_honours_cancellation():
    registry = ToolRegistry([make_tool()])
    event = threading.Event()
    event.set()

    result = registry.execute(
        "echo", {"text": "x"}, cancel_event=event
    )
    assert result.kind == "cancelled"


def test_registry_truncates_large_output():
    registry = ToolRegistry([
        make_tool(lambda args: "x" * 41_000)
    ])
    result = registry.execute("echo", {"text": "x"})

    assert result.ok
    assert "output truncated" in result.text()
