from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import pytest

from remote_computer_use.models import AppConfig, HealthCheckConfig, RemoteConfig
from remote_computer_use.probes import ProbeFailure
from remote_computer_use.state import CheckResult, Status


def config_data() -> dict:
    return {
        "schema_version": 1,
        "server": {
            "probe_interval_seconds": 60,
            "probe_timeout_seconds": 1,
            "unavailable_after_failures": 3,
            "max_concurrent_probes": 2,
            "startup_probe_timeout_seconds": 2,
        },
        "remotes": [
            {
                "name": "test-host",
                "host": "192.0.2.10",
                "description": "test",
                "ssh": {
                    "user": "tester",
                    "port": 2222,
                    "auth": {
                        "type": "identity_file",
                        "identity_file": "~/.ssh/test_key",
                    },
                    "known_hosts": "~/.ssh/known_hosts",
                },
                "health_checks": [
                    {"name": "ssh", "type": "ssh", "required": True},
                    {"name": "ping", "type": "ping", "required": False},
                ],
                "capabilities": [
                    {
                        "name": "comfyui",
                        "description": "ComfyUI",
                        "usage": "Use an SSH tunnel.",
                        "remark": ["Check the queue."],
                        "health_checks": [
                            {
                                "name": "api",
                                "type": "curl",
                                "target": "http://127.0.0.1:8188/system_stats",
                                "required": True,
                            }
                        ],
                    },
                    {
                        "name": "empty-fields",
                        "health_checks": [
                            {
                                "name": "executable",
                                "type": "command",
                                "argv": ["tool", "--version"],
                            }
                        ],
                    },
                ],
            }
        ],
    }


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig.model_validate(config_data())


@dataclass
class Completed:
    exit_status: int = 0


class FakeConnection:
    async def run(self, command: str, *, check: bool = False, timeout: float | None = None):
        return Completed()


class FakeBackend:
    def __init__(self) -> None:
        self.default_timeout = 1.0
        self.connect_failure: str | None = None
        self.local_failures: dict[str, str] = {}
        self.remote_failures: dict[str, str] = {}
        self.connect_calls = 0
        self.local_calls = 0
        self.remote_calls = 0

    @asynccontextmanager
    async def connect(self, remote: RemoteConfig) -> AsyncIterator[tuple[FakeConnection, int]]:
        self.connect_calls += 1
        if self.connect_failure:
            raise ProbeFailure(self.connect_failure)
        yield FakeConnection(), 5

    async def run_local(self, check: HealthCheckConfig, remote: RemoteConfig) -> CheckResult:
        self.local_calls += 1
        reason = self.local_failures.get(check.name)
        return CheckResult(
            name=check.name,
            type=check.type,
            required=check.required,
            status=Status.UNHEALTHY if reason else Status.HEALTHY,
            latency_ms=3,
            failure_reason=reason,
        )

    async def run_remote(self, connection: FakeConnection, check: HealthCheckConfig) -> CheckResult:
        self.remote_calls += 1
        reason = self.remote_failures.get(check.name)
        return CheckResult(
            name=check.name,
            type=check.type,
            required=check.required,
            status=Status.UNHEALTHY if reason else Status.HEALTHY,
            latency_ms=4,
            failure_reason=reason,
        )

