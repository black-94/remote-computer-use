from __future__ import annotations

import pytest

from remote_computer_use.api import DiscoveryAPI
from remote_computer_use.errors import SSH_AUTH_FAILED
from remote_computer_use.service import DiscoveryService

from .conftest import FakeBackend


def local_config(app_config):
    local = app_config.remotes[0].model_copy(
        update={
            "name": "localhost",
            "host": "localhost",
            "connection": "local",
            "ssh": None,
            "health_checks": [
                app_config.remotes[0].health_checks[0].model_copy(
                    update={"name": "local", "type": "local"}
                )
            ],
        }
    )
    return app_config.model_copy(update={"remotes": [local]})


@pytest.fixture
def service(tmp_path, app_config):
    backend = FakeBackend()
    instance = DiscoveryService(
        tmp_path / "config.yaml",
        config=app_config,
        backend=backend,
    )
    return instance, backend


def test_queries_do_not_probe(service) -> None:
    instance, backend = service
    assert instance.list_available_remotes() == {"remotes": []}
    assert instance.list_all_remotes() == {
        "remotes": [{"name": "test-host", "status": "unknown"}]
    }
    instance.get_remote_status("test-host")
    assert backend.connect_calls == 0
    assert backend.local_calls == 0
    assert backend.remote_calls == 0


@pytest.mark.asyncio
async def test_refresh_populates_compact_discovery_responses(service) -> None:
    instance, backend = service
    await instance.refresh_health("test-host")

    assert instance.list_available_remotes() == {"remotes": ["test-host"]}
    available = instance.list_available_capabilities("test-host")
    assert available["remote"]["ssh"]["identity_file"] == "~/.ssh/test_key"
    assert [item["name"] for item in available["capabilities"]] == [
        "comfyui",
        "empty-fields",
    ]
    empty = available["capabilities"][1]
    assert empty == {"name": "empty-fields"}
    assert backend.connect_calls == 1


@pytest.mark.asyncio
async def test_initial_connection_failure_blocks_capabilities(service) -> None:
    instance, backend = service
    backend.connect_failure = SSH_AUTH_FAILED
    await instance.refresh_health("test-host")
    all_capabilities = instance.list_all_capabilities("test-host")
    assert all_capabilities["remote"]["status"] == "degraded"
    assert all(item["status"] == "blocked" for item in all_capabilities["capabilities"])
    assert all(item["failure_scope"] == "remote" for item in all_capabilities["capabilities"])
    assert instance.list_available_remotes() == {"remotes": []}


@pytest.mark.asyncio
async def test_remote_is_removed_after_three_failures(service) -> None:
    instance, backend = service
    await instance.refresh_health("test-host")
    backend.connect_failure = SSH_AUTH_FAILED
    await instance.refresh_health("test-host")
    assert instance.list_available_remotes() == {"remotes": ["test-host"]}
    await instance.refresh_health("test-host")
    await instance.refresh_health("test-host")
    assert instance.list_all_remotes()["remotes"][0]["status"] == "unhealthy"
    assert instance.list_available_remotes() == {"remotes": []}


@pytest.mark.asyncio
async def test_capability_failure_is_distinguished(service) -> None:
    instance, backend = service
    backend.remote_failures["api"] = "CHECK_FAILED"
    for _ in range(3):
        await instance.refresh_health("test-host")
    statuses = instance.list_all_capabilities("test-host")["capabilities"]
    comfy = statuses[0]
    assert comfy == {
        "name": "comfyui",
        "status": "unhealthy",
        "failure_scope": "capability",
    }
    assert instance.list_available_remotes() == {"remotes": ["test-host"]}


@pytest.mark.asyncio
async def test_status_contains_remote_and_capability_diagnostics_without_log_path(service) -> None:
    instance, _ = service
    await instance.refresh_health("test-host")
    status = instance.get_remote_status("test-host")
    assert status["ssh_status"] == "healthy"
    assert status["checks"][0]["name"] == "ssh"
    assert status["capabilities"][0]["checks"][0]["name"] == "api"
    assert "log_file" not in status


@pytest.mark.asyncio
async def test_api_returns_structured_errors(service) -> None:
    instance, _ = service
    api = DiscoveryAPI(instance)
    assert api.get_remote_status("missing") == {
        "error": {"code": "REMOTE_NOT_FOUND", "message": "remote not found"}
    }
    result = await api.refresh_health(capability="comfyui")
    assert result["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_local_remote_probes_directly_without_ssh(tmp_path, app_config) -> None:
    backend = FakeBackend()
    instance = DiscoveryService(
        tmp_path / "config.yaml",
        config=local_config(app_config),
        backend=backend,
    )
    await instance.refresh_health("localhost")
    assert backend.connect_calls == 0
    assert instance.list_available_remotes() == {"remotes": ["localhost"]}
    available = instance.list_available_capabilities("localhost")
    assert available["remote"] == {
        "name": "localhost",
        "host": "localhost",
        "connection": "local",
    }
    status = instance.get_remote_status("localhost")
    assert status["connection"] == "local"
    assert "ssh_status" not in status
