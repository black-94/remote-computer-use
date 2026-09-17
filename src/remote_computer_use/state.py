from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from .models import AppConfig, CapabilityConfig, HealthCheckConfig, RemoteConfig


class Status(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    BLOCKED = "blocked"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


@dataclass(slots=True)
class CheckResult:
    name: str
    type: str
    required: bool
    status: Status
    latency_ms: int | None = None
    checked_at: datetime = field(default_factory=utc_now)
    failure_reason: str | None = None

    @property
    def successful(self) -> bool:
        return self.status == Status.HEALTHY


@dataclass(slots=True)
class EntityState:
    status: Status = Status.UNKNOWN
    consecutive_failures: int = 0
    checked_at: datetime | None = None
    last_alive_at: datetime | None = None
    last_failure_reason: str | None = None
    checks: list[CheckResult] = field(default_factory=list)

    def record(self, results: list[CheckResult], threshold: int) -> None:
        self.checks = results
        self.checked_at = max((result.checked_at for result in results), default=utc_now())
        required_failures = [result for result in results if result.required and not result.successful]
        optional_failures = [result for result in results if not result.required and not result.successful]
        if not required_failures:
            self.consecutive_failures = 0
            self.last_failure_reason = optional_failures[0].failure_reason if optional_failures else None
            self.last_alive_at = self.checked_at
            self.status = Status.DEGRADED if optional_failures else Status.HEALTHY
            return

        self.consecutive_failures += 1
        self.last_failure_reason = required_failures[0].failure_reason
        self.status = (
            Status.UNHEALTHY
            if self.consecutive_failures >= threshold
            else Status.DEGRADED
        )

    @property
    def available(self) -> bool:
        return self.last_alive_at is not None and self.status in {
            Status.HEALTHY,
            Status.DEGRADED,
        }


@dataclass(slots=True)
class CapabilityState(EntityState):
    failure_scope: str | None = None


@dataclass(slots=True)
class RemoteState(EntityState):
    ssh_status: Status = Status.UNKNOWN
    capabilities: dict[str, CapabilityState] = field(default_factory=dict)


class StateStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.remotes: dict[str, RemoteState] = {}
        self.replace_config(config)

    def replace_config(self, config: AppConfig) -> None:
        previous = self.remotes
        remotes: dict[str, RemoteState] = {}
        for remote in config.remotes:
            state = previous.get(remote.name, RemoteState())
            state.capabilities = {
                capability.name: state.capabilities.get(capability.name, CapabilityState())
                for capability in remote.capabilities
            }
            remotes[remote.name] = state
        self.config = config
        self.remotes = remotes

    def remote_config(self, name: str) -> RemoteConfig | None:
        return next((item for item in self.config.remotes if item.name == name), None)

    @staticmethod
    def capability_config(remote: RemoteConfig, name: str) -> CapabilityConfig | None:
        return next((item for item in remote.capabilities if item.name == name), None)


def check_to_dict(result: CheckResult) -> dict[str, object]:
    return compact(
        {
            "name": result.name,
            "type": result.type,
            "required": result.required,
            "status": result.status.value,
            "latency_ms": result.latency_ms,
            "checked_at": iso(result.checked_at),
            "failure_reason": result.failure_reason,
        }
    )


def entity_to_dict(state: EntityState) -> dict[str, object]:
    return compact(
        {
            "status": state.status.value,
            "consecutive_failures": state.consecutive_failures,
            "checked_at": iso(state.checked_at),
            "last_alive_at": iso(state.last_alive_at),
            "last_failure_reason": state.last_failure_reason,
            "checks": [check_to_dict(check) for check in state.checks],
        }
    )


def compact(value: dict[str, object]) -> dict[str, object]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {}
    }
