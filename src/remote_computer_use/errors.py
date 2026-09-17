from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DiscoveryError(Exception):
    code: str
    message: str

    def as_dict(self) -> dict[str, object]:
        return {"error": {"code": self.code, "message": self.message}}


REMOTE_NOT_FOUND = "REMOTE_NOT_FOUND"
CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
CONFIG_INVALID = "CONFIG_INVALID"
SSH_AUTH_FAILED = "SSH_AUTH_FAILED"
SSH_HOST_KEY_FAILED = "SSH_HOST_KEY_FAILED"
REMOTE_UNREACHABLE = "REMOTE_UNREACHABLE"
PROBE_TIMEOUT = "PROBE_TIMEOUT"
PROBE_IN_PROGRESS = "PROBE_IN_PROGRESS"
COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
CHECK_FAILED = "CHECK_FAILED"
INTERNAL_ERROR = "INTERNAL_ERROR"

