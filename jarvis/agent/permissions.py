"""Permission decisions. The model cannot raise its own level."""

import hashlib
import json
from dataclasses import dataclass


@dataclass
class Decision:
    allowed: bool
    level: str
    reason: str
    speech: str


def approval_key(tool_name, arguments):
    """Bind an approval to one exact tool and argument set."""
    payload = json.dumps([tool_name, arguments], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class PermissionEngine:
    def __init__(self, roku_standing=True):
        self.roku_standing = roku_standing
        self._granted = set()

    def grant(self, tool_name, arguments):
        self._granted.add(approval_key(tool_name, arguments))

    def clear_grants(self):
        self._granted.clear()

    def decide(self, tool, arguments):
        level = tool.permission
        if level == "L3":
            return Decision(False, level, "prohibited", "I can't do that.")
        granted = approval_key(tool.name, arguments) in self._granted
        if level == "L2" and not granted:
            return Decision(
                False,
                level,
                "needs_approval",
                "That needs your approval before I do it.",
            )
        if tool.name == "schedule.routine" and arguments.get("action") == "power_off" and not self.roku_standing:
            return Decision(
                False,
                level,
                "standing_required",
                "A scheduled TV-off needs standing TV authorization, and that is off.",
            )
        if level == "L1" and tool.side_effect == "device" and not self.roku_standing and not granted:
            return Decision(
                False,
                level,
                "needs_approval",
                "I need your approval before I change the TV.",
            )
        if level in {"L0", "L1", "L2"}:
            return Decision(True, level, "allowed", "")
        return Decision(False, level, "unknown_level", "I can't do that.")
