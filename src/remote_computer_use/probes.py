from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Protocol

import asyncssh

from .errors import (
    CHECK_FAILED,
    COMMAND_NOT_FOUND,
    PROBE_TIMEOUT,
    REMOTE_UNREACHABLE,
    SSH_AUTH_FAILED,
    SSH_HOST_KEY_FAILED,
)
from .models import HealthCheckConfig, RemoteConfig
from .state import CheckResult, Status, utc_now

LOGGER = logging.getLogger(__name__)


class RemoteConnection(Protocol):
    async def run(self, command: str, *, check: bool = False, timeout: float | None = None): ...


class ProbeBackend:
    def __init__(self, default_timeout: float) -> None:
        self.default_timeout = default_timeout

    @asynccontextmanager
    async def connect(self, remote: RemoteConfig) -> AsyncIterator[tuple[RemoteConnection, int]]:
        started = time.monotonic()
        ssh = remote.ssh
        if ssh is None:
            raise ProbeFailure(REMOTE_UNREACHABLE)
        known_hosts_path = Path(ssh.known_hosts).expanduser()
        identity_path = (
            Path(ssh.auth.identity_file or "").expanduser()
            if ssh.auth.type == "identity_file"
            else None
        )
        if not known_hosts_path.is_file():
            raise ProbeFailure(SSH_HOST_KEY_FAILED)
        if identity_path is not None and not identity_path.is_file():
            raise ProbeFailure(SSH_AUTH_FAILED)
        kwargs: dict[str, object] = {
            "host": remote.host,
            "port": ssh.port,
            "username": ssh.user,
            "known_hosts": str(known_hosts_path),
            "connect_timeout": ssh.connect_timeout_seconds,
            "login_timeout": ssh.connect_timeout_seconds,
            "password": None,
            "kbdint_auth": False,
            "preferred_auth": "publickey",
        }
        if identity_path is not None:
            kwargs["client_keys"] = [str(identity_path)]
        try:
            async with asyncssh.connect(**kwargs) as connection:
                latency = round((time.monotonic() - started) * 1000)
                yield connection, latency
        except asyncssh.PermissionDenied as exc:
            raise ProbeFailure(SSH_AUTH_FAILED) from exc
        except asyncssh.HostKeyNotVerifiable as exc:
            raise ProbeFailure(SSH_HOST_KEY_FAILED) from exc
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise ProbeFailure(PROBE_TIMEOUT) from exc
        except (OSError, asyncssh.Error) as exc:
            raise ProbeFailure(REMOTE_UNREACHABLE) from exc

    async def run_local(
        self,
        check: HealthCheckConfig,
        remote: RemoteConfig,
    ) -> CheckResult:
        started = time.monotonic()
        timeout = check.timeout_seconds or self.default_timeout
        try:
            await asyncio.wait_for(self._run_local(check, remote), timeout=timeout)
            return _result(check, Status.HEALTHY, started)
        except asyncio.TimeoutError:
            return _result(check, Status.UNHEALTHY, started, PROBE_TIMEOUT)
        except FileNotFoundError:
            return _result(check, Status.UNHEALTHY, started, COMMAND_NOT_FOUND)
        except ProbeFailure as exc:
            return _result(check, Status.UNHEALTHY, started, exc.code)
        except Exception:
            LOGGER.exception("local health check failed", extra={"remote": remote.name, "check": check.name})
            return _result(check, Status.UNHEALTHY, started, CHECK_FAILED)

    async def _run_local(self, check: HealthCheckConfig, remote: RemoteConfig) -> None:
        if check.type == "local":
            return
        if check.type == "ping":
            await _subprocess(["ping", "-c", str(check.count), check.host or remote.host])
            return
        if check.type in {"tcp", "telnet", "nc"}:
            reader, writer = await asyncio.open_connection(check.host or remote.host, check.port)
            del reader
            writer.close()
            await writer.wait_closed()
            return
        if check.type == "curl":
            timeout = check.timeout_seconds or self.default_timeout
            await asyncio.to_thread(_http_head_or_get, check.target or "", timeout)
            return
        if check.type == "path":
            if not Path(check.target or "").expanduser().exists():
                raise ProbeFailure(CHECK_FAILED)
            return
        if check.type == "process":
            await _subprocess(["pgrep", "-f", check.target or ""])
            return
        if check.type == "command":
            await _subprocess(check.argv or [])
            return
        if check.type == "script":
            await _subprocess(["/bin/sh", "-lc", check.script or ""])
            return
        raise ProbeFailure(CHECK_FAILED)

    async def run_remote(
        self,
        connection: RemoteConnection,
        check: HealthCheckConfig,
    ) -> CheckResult:
        started = time.monotonic()
        timeout = check.timeout_seconds or self.default_timeout
        command = remote_command(check, timeout)
        try:
            completed = await connection.run(command, check=False, timeout=timeout)
            if completed.exit_status == 127:
                return _result(check, Status.UNHEALTHY, started, COMMAND_NOT_FOUND)
            if completed.exit_status != 0:
                return _result(check, Status.UNHEALTHY, started, CHECK_FAILED)
            return _result(check, Status.HEALTHY, started)
        except (asyncio.TimeoutError, TimeoutError):
            return _result(check, Status.UNHEALTHY, started, PROBE_TIMEOUT)
        except (OSError, asyncssh.Error):
            return _result(check, Status.UNHEALTHY, started, REMOTE_UNREACHABLE)
        except Exception:
            LOGGER.exception("remote capability check failed", extra={"check": check.name})
            return _result(check, Status.UNHEALTHY, started, CHECK_FAILED)


class ProbeFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def remote_command(check: HealthCheckConfig, timeout: float) -> str:
    if check.type == "ping":
        return _join(["ping", "-c", str(check.count), check.host or "127.0.0.1"])
    if check.type in {"tcp", "telnet", "nc"}:
        return _join(["nc", "-z", "-w", str(max(1, int(timeout))), check.host or "127.0.0.1", str(check.port)])
    if check.type == "curl":
        return _join(["curl", "-fsS", "--max-time", str(timeout), "--output", "/dev/null", check.target or ""])
    if check.type == "path":
        return f"test -e {shlex.quote(check.target or '')}"
    if check.type == "process":
        return _join(["pgrep", "-f", check.target or ""])
    if check.type == "command":
        return _join(check.argv or [])
    if check.type == "script":
        return f"/bin/sh -lc {shlex.quote(check.script or '')}"
    raise ValueError(f"unsupported remote check type: {check.type}")


def _join(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


async def _subprocess(argv: list[str]) -> None:
    if not argv:
        raise ProbeFailure(CHECK_FAILED)
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": os.environ.get("PATH", "")},
    )
    try:
        status = await process.wait()
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    if status == 127:
        raise FileNotFoundError(argv[0])
    if status != 0:
        raise ProbeFailure(CHECK_FAILED)


def _http_head_or_get(target: str, timeout: float) -> None:
    request = urllib.request.Request(target, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 400:
                raise ProbeFailure(CHECK_FAILED)
    except urllib.error.HTTPError as exc:
        if exc.code in {405, 501}:
            with urllib.request.urlopen(target, timeout=timeout) as response:
                if response.status >= 400:
                    raise ProbeFailure(CHECK_FAILED)
            return
        raise ProbeFailure(CHECK_FAILED) from exc
    except urllib.error.URLError as exc:
        raise ProbeFailure(CHECK_FAILED) from exc


def _result(
    check: HealthCheckConfig,
    status: Status,
    started: float,
    reason: str | None = None,
) -> CheckResult:
    return CheckResult(
        name=check.name,
        type=check.type,
        required=check.required,
        status=status,
        latency_ms=round((time.monotonic() - started) * 1000),
        checked_at=utc_now(),
        failure_reason=reason,
    )
