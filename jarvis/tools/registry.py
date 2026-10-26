"""Tool definitions, validation, and audited execution."""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolResult:
    ok: bool
    output: dict
    speech: str
    error_code: str = ""
    duration_ms: int = 0
    verified: Optional[bool] = None
    retryable: bool = False
    tool: str = ""


@dataclass
class Tool:
    name: str
    description: str
    permission: str
    side_effect: str
    timeout: float
    idempotent: bool
    required: tuple
    handler: object
    schema: dict = field(default_factory=dict)


class Registry:
    def __init__(self):
        self.tools = {}

    def add(self, tool):
        self.tools[tool.name] = tool

    def describe(self):
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "permission": tool.permission,
                "required": list(tool.required),
                "side_effect": tool.side_effect,
            }
            for tool in self.tools.values()
        ]

    def call(self, name, arguments, context, permission_engine, store, trigger):
        started = time.perf_counter()
        arguments = arguments or {}
        tool = self.tools.get(name)
        if tool is None or name in {"shell", "bash", "system"}:
            result = ToolResult(
                ok=False,
                output={},
                speech="That action is not available.",
                error_code="unknown_tool",
                tool=name,
            )
        elif not _arguments_ok(tool, arguments):
            result = ToolResult(
                ok=False,
                output={},
                speech="I could not run that because the arguments were invalid.",
                error_code="invalid_arguments",
                tool=name,
            )
        else:
            decision = permission_engine.decide(tool, arguments)
            if not decision.allowed:
                result = ToolResult(
                    ok=False,
                    output={"permission": decision.level, "reason": decision.reason},
                    speech=decision.speech,
                    error_code=decision.reason,
                    tool=name,
                )
            else:
                try:
                    result = tool.handler(arguments, context)
                    result.tool = name
                except Exception as exc:
                    result = ToolResult(
                        ok=False,
                        output={},
                        speech=f"The {name} tool failed. {exc}",
                        error_code="tool_error",
                        retryable=False,
                        tool=name,
                    )
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        if store is not None:
            verified = "" if result.verified is None else str(result.verified).lower()
            store.add_audit(
                trigger,
                name,
                _safe_args(arguments),
                tool.permission if tool else "unknown",
                "ok" if result.ok else result.error_code,
                verified,
            )
        return result


def _arguments_ok(tool, arguments):
    if not isinstance(arguments, dict):
        return False
    for key in tool.required:
        if key not in arguments or arguments[key] in ("", None):
            return False
    return True


def _safe_args(arguments):
    import json

    from jarvis.observe import redact

    return json.dumps(redact(arguments), ensure_ascii=False)[:2000]
