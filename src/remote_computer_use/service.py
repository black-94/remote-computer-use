from __future__ import annotations

import asyncio
import logging
import random
from contextlib import suppress
from pathlib import Path
from typing import Any

from watchfiles import awatch

from .errors import (
    CAPABILITY_NOT_FOUND,
    INTERNAL_ERROR,
    INVALID_ARGUMENT,
    PROBE_IN_PROGRESS,
    REMOTE_NOT_FOUND,
    DiscoveryError,
)
from .models import AppConfig, CapabilityConfig, RemoteConfig, load_config
from .probes import ProbeBackend, ProbeFailure
from .state import (
    CheckResult,
    RemoteState,
    StateStore,
    Status,
    compact,
    entity_to_dict,
    iso,
    utc_now,
)

LOGGER = logging.getLogger(__name__)


class DiscoveryService:
    def __init__(
        self,
        config_path: str | Path,
        *,
        config: AppConfig | None = None,
        backend: ProbeBackend | None = None,
    ) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        loaded = config or load_config(self.config_path)
        self.store = StateStore(loaded)
        self.backend = backend or ProbeBackend(loaded.server.probe_timeout_seconds)
        self._semaphore = asyncio.Semaphore(loaded.server.max_concurrent_probes)
        self._locks = {remote.name: asyncio.Lock() for remote in loaded.remotes}
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        timeout = self.store.config.server.startup_probe_timeout_seconds
        try:
            await asyncio.wait_for(self.refresh_health(), timeout=timeout)
        except asyncio.TimeoutError:
            LOGGER.warning("startup health probe reached its time limit")
        self._tasks = [
            asyncio.create_task(self._scheduler(), name="health-scheduler"),
            asyncio.create_task(self._watch_config(), name="config-watcher"),
        ]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    def list_available_remotes(self) -> dict[str, object]:
        names = [
            remote.name
            for remote in self.store.config.remotes
            if self._remote_is_available(remote, self.store.remotes[remote.name])
        ]
        return {"remotes": names}

    def list_all_remotes(self) -> dict[str, object]:
        return {
            "remotes": [
                {"name": remote.name, "status": self.store.remotes[remote.name].status.value}
                for remote in self.store.config.remotes
            ]
        }

    def list_available_capabilities(self, remote_name: str) -> dict[str, object]:
        remote, remote_state = self._get_remote(remote_name)
        capabilities: list[dict[str, object]] = []
        if remote_state.available:
            for capability in remote.capabilities:
                state = remote_state.capabilities[capability.name]
                if state.available and state.status != Status.BLOCKED:
                    capabilities.append(self._capability_description(capability))
        remote_info: dict[str, object] = {
            "name": remote.name,
            "host": remote.host,
            "connection": remote.connection,
        }
        if remote.connection == "ssh" and remote.ssh is not None:
            ssh: dict[str, object] = {
                "user": remote.ssh.user,
                "port": remote.ssh.port,
                "auth_type": remote.ssh.auth.type,
            }
            if remote.ssh.auth.type == "identity_file":
                ssh["identity_file"] = remote.ssh.auth.identity_file or ""
            remote_info["ssh"] = compact(ssh)
        return {
            "remote": remote_info,
            "capabilities": capabilities,
        }

    def list_all_capabilities(self, remote_name: str) -> dict[str, object]:
        remote, remote_state = self._get_remote(remote_name)
        capabilities: list[dict[str, object]] = []
        for capability in remote.capabilities:
            state = remote_state.capabilities[capability.name]
            item: dict[str, object] = {
                "name": capability.name,
                "status": state.status.value,
            }
            if state.failure_scope:
                item["failure_scope"] = state.failure_scope
            capabilities.append(item)
        return {
            "remote": {"name": remote.name, "status": remote_state.status.value},
            "capabilities": capabilities,
        }

    def get_remote_status(self, remote_name: str) -> dict[str, object]:
        remote, state = self._get_remote(remote_name)
        result = entity_to_dict(state)
        result["name"] = remote.name
        result["connection"] = remote.connection
        if remote.connection == "ssh":
            result["ssh_status"] = state.ssh_status.value
        result["capabilities"] = []
        for capability in remote.capabilities:
            cap_state = state.capabilities[capability.name]
            item = entity_to_dict(cap_state)
            item["name"] = capability.name
            if cap_state.failure_scope:
                item["failure_scope"] = cap_state.failure_scope
            result["capabilities"].append(item)
        # Stable key order puts identity first while compacting empty nested fields.
        return compact(
            {
                "name": result.pop("name"),
                "status": result.pop("status"),
                "connection": result.pop("connection"),
                "ssh_status": result.pop("ssh_status", None),
                **result,
            }
        )

    async def refresh_health(
        self,
        remote: str | None = None,
        capability: str | None = None,
    ) -> dict[str, object]:
        if capability and not remote:
            raise DiscoveryError(INVALID_ARGUMENT, "capability requires remote")
        if remote:
            remote_config, _ = self._get_remote(remote)
            if capability and self.store.capability_config(remote_config, capability) is None:
                raise DiscoveryError(CAPABILITY_NOT_FOUND, "capability not found")
            await self._refresh_remote(remote_config, capability, fail_if_busy=True)
            state = self.store.remotes[remote]
            result: dict[str, object] = {
                "scope": "capability" if capability else "remote",
                "remote": remote,
                "status": (
                    state.capabilities[capability].status.value if capability else state.status.value
                ),
                "checked_at": iso(
                    state.capabilities[capability].checked_at if capability else state.checked_at
                ),
            }
            if capability:
                result["capability"] = capability
            return compact(result)

        await asyncio.gather(
            *(self._refresh_remote(item, None, fail_if_busy=False) for item in self.store.config.remotes)
        )
        return {
            "scope": "all",
            "checked_at": iso(utc_now()),
            "remotes": [
                {"name": item.name, "status": self.store.remotes[item.name].status.value}
                for item in self.store.config.remotes
            ],
        }

    async def _refresh_remote(
        self,
        remote: RemoteConfig,
        capability_name: str | None,
        *,
        fail_if_busy: bool,
    ) -> None:
        lock = self._locks.setdefault(remote.name, asyncio.Lock())
        if lock.locked():
            if fail_if_busy:
                raise DiscoveryError(PROBE_IN_PROGRESS, "probe already in progress")
            return
        async with lock, self._semaphore:
            state = self.store.remotes.get(remote.name)
            if state is None:
                return
            await self._probe_locked(remote, state, capability_name)

    async def _probe_locked(
        self,
        remote: RemoteConfig,
        state: RemoteState,
        capability_name: str | None,
    ) -> None:
        if remote.connection == "local":
            await self._probe_local_locked(remote, state, capability_name)
            return
        await self._probe_ssh_locked(remote, state, capability_name)

    async def _probe_local_locked(
        self,
        remote: RemoteConfig,
        state: RemoteState,
        capability_name: str | None,
    ) -> None:
        threshold = self.store.config.server.unavailable_after_failures
        results = list(
            await asyncio.gather(
                *(self.backend.run_local(check, remote) for check in remote.health_checks)
            )
        )
        state.record(results, threshold)
        selected = [
            capability
            for capability in remote.capabilities
            if capability_name is None or capability.name == capability_name
        ]
        await asyncio.gather(
            *(
                self._probe_local_capability(remote, state, capability, threshold)
                for capability in selected
            )
        )

    async def _probe_ssh_locked(
        self,
        remote: RemoteConfig,
        state: RemoteState,
        capability_name: str | None,
    ) -> None:
        threshold = self.store.config.server.unavailable_after_failures
        ssh_check = next(check for check in remote.health_checks if check.type == "ssh")
        other_checks = [check for check in remote.health_checks if check.type != "ssh"]
        local_task = asyncio.gather(*(self.backend.run_local(check, remote) for check in other_checks))
        started = asyncio.get_running_loop().time()
        try:
            async with self.backend.connect(remote) as (connection, latency_ms):
                ssh_result = CheckResult(
                    name=ssh_check.name,
                    type="ssh",
                    required=True,
                    status=Status.HEALTHY,
                    latency_ms=latency_ms,
                )
                local_results = list(await local_task)
                results = self._ordered_remote_results(remote, ssh_result, local_results)
                state.ssh_status = Status.HEALTHY
                state.record(results, threshold)
                selected = [
                    capability
                    for capability in remote.capabilities
                    if capability_name is None or capability.name == capability_name
                ]
                await asyncio.gather(
                    *(self._probe_capability(connection, state, capability, threshold) for capability in selected)
                )
        except asyncio.CancelledError:
            local_task.cancel()
            with suppress(asyncio.CancelledError):
                await local_task
            raise
        except ProbeFailure as exc:
            local_results = list(await local_task)
            ssh_result = CheckResult(
                name=ssh_check.name,
                type="ssh",
                required=True,
                status=Status.UNHEALTHY,
                latency_ms=round((asyncio.get_running_loop().time() - started) * 1000),
                failure_reason=exc.code,
            )
            state.ssh_status = Status.UNHEALTHY
            state.record(self._ordered_remote_results(remote, ssh_result, local_results), threshold)
            if not state.available:
                self._block_capabilities(remote, state, exc.code, capability_name)
        except Exception:
            LOGGER.exception("unexpected remote probe failure", extra={"remote": remote.name})
            if not local_task.done():
                local_task.cancel()
            raise DiscoveryError(INTERNAL_ERROR, "unexpected probe failure")

    async def _probe_capability(
        self,
        connection: Any,
        remote_state: RemoteState,
        capability: CapabilityConfig,
        threshold: int,
    ) -> None:
        results = list(
            await asyncio.gather(
                *(self.backend.run_remote(connection, check) for check in capability.health_checks)
            )
        )
        state = remote_state.capabilities[capability.name]
        state.record(results, threshold)
        state.failure_scope = None if state.status == Status.HEALTHY else "capability"

    async def _probe_local_capability(
        self,
        remote: RemoteConfig,
        remote_state: RemoteState,
        capability: CapabilityConfig,
        threshold: int,
    ) -> None:
        results = list(
            await asyncio.gather(
                *(self.backend.run_local(check, remote) for check in capability.health_checks)
            )
        )
        state = remote_state.capabilities[capability.name]
        state.record(results, threshold)
        state.failure_scope = None if state.status == Status.HEALTHY else "capability"

    def _block_capabilities(
        self,
        remote: RemoteConfig,
        state: RemoteState,
        reason: str,
        capability_name: str | None,
    ) -> None:
        now = utc_now()
        for capability in remote.capabilities:
            if capability_name is not None and capability.name != capability_name:
                continue
            cap_state = state.capabilities[capability.name]
            cap_state.status = Status.BLOCKED
            cap_state.checked_at = now
            cap_state.last_failure_reason = reason
            cap_state.failure_scope = "remote"
            cap_state.checks = [
                CheckResult(
                    name=check.name,
                    type=check.type,
                    required=check.required,
                    status=Status.BLOCKED,
                    checked_at=now,
                    failure_reason=reason,
                )
                for check in capability.health_checks
            ]

    @staticmethod
    def _ordered_remote_results(
        remote: RemoteConfig,
        ssh_result: CheckResult,
        other_results: list[CheckResult],
    ) -> list[CheckResult]:
        by_name = {result.name: result for result in [ssh_result, *other_results]}
        return [by_name[check.name] for check in remote.health_checks]

    def _get_remote(self, name: str) -> tuple[RemoteConfig, RemoteState]:
        remote = self.store.remote_config(name)
        if remote is None:
            raise DiscoveryError(REMOTE_NOT_FOUND, "remote not found")
        return remote, self.store.remotes[name]

    @staticmethod
    def _capability_description(capability: CapabilityConfig) -> dict[str, object]:
        return compact(
            {
                "name": capability.name,
                "description": capability.description,
                "usage": capability.usage,
                "remark": capability.remark,
            }
        )

    @staticmethod
    def _remote_is_available(remote: RemoteConfig, state: RemoteState) -> bool:
        return state.available and any(
            state.capabilities[capability.name].available
            and state.capabilities[capability.name].status != Status.BLOCKED
            for capability in remote.capabilities
        )

    async def _scheduler(self) -> None:
        while not self._stop.is_set():
            interval = self.store.config.server.probe_interval_seconds
            jittered = interval * random.uniform(0.9, 1.1)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=jittered)
            except asyncio.TimeoutError:
                try:
                    await self.refresh_health()
                except Exception:
                    LOGGER.exception("scheduled health refresh failed")

    async def _watch_config(self) -> None:
        try:
            async for changes in awatch(self.config_path.parent, stop_event=self._stop):
                if not any(Path(path).resolve() == self.config_path for _, path in changes):
                    continue
                try:
                    config = load_config(self.config_path)
                    self.store.replace_config(config)
                    self.backend.default_timeout = config.server.probe_timeout_seconds
                    self._semaphore = asyncio.Semaphore(config.server.max_concurrent_probes)
                    self._locks = {
                        remote.name: self._locks.get(remote.name, asyncio.Lock())
                        for remote in config.remotes
                    }
                    LOGGER.info("configuration reloaded")
                    await self.refresh_health()
                except DiscoveryError as exc:
                    LOGGER.error("configuration reload rejected: %s", exc.message)
                except Exception:
                    LOGGER.exception("configuration reload failed")
        except asyncio.CancelledError:
            raise
