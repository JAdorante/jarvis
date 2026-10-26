"""Permission decisions. The model cannot raise its own level."""

from dataclasses import dataclass


@dataclass
class Decision:
    allowed: bool
    level: str
    reason: str
    speech: str


class PermissionEngine:
    def __init__(self, roku_standing=True):
        self.roku_standing = roku_standing

    def decide(self, tool, arguments):
        level = tool.permission
        if level == "L3":
            return Decision(False, level, "prohibited", "I can't do that.")
        if level == "L2":
            return Decision(
                False,
                level,
                "needs_approval",
                "That needs your approval before I do it.",
            )
        if level == "L1" and tool.side_effect == "device" and not self.roku_standing:
            return Decision(
                False,
                level,
                "needs_approval",
                "I need your approval before I change the TV.",
            )
        if level in {"L0", "L1"}:
            return Decision(True, level, "allowed", "")
        return Decision(False, level, "unknown_level", "I can't do that.")
