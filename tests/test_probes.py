from __future__ import annotations

import shlex

import pytest

from remote_computer_use.errors import SSH_AUTH_FAILED, SSH_HOST_KEY_FAILED
from remote_computer_use.models import HealthCheckConfig
from remote_computer_use.probes import ProbeBackend, ProbeFailure, remote_command


def test_remote_command_shell_quotes_yaml_values() -> None:
    check = HealthCheckConfig(
        name="version",
        type="command",
        argv=["tool", "value with spaces", "$(never-run-locally)"],
    )
    command = remote_command(check, 5)
    assert command == " ".join(shlex.quote(value) for value in check.argv or [])


def test_curl_command_has_no_response_body() -> None:
    check = HealthCheckConfig(
        name="api",
        type="curl",
        target="http://127.0.0.1:8188/system_stats",
    )
    command = remote_command(check, 5)
    assert "--output /dev/null" in command
    assert "-fsS" in command


@pytest.mark.asyncio
async def test_missing_known_hosts_is_classified_without_network(tmp_path, app_config) -> None:
    remote = app_config.remotes[0].model_copy(
        update={
            "ssh": app_config.remotes[0].ssh.model_copy(
                update={"known_hosts": str(tmp_path / "missing-known-hosts")}
            )
        }
    )
    backend = ProbeBackend(1)
    with pytest.raises(ProbeFailure) as caught:
        async with backend.connect(remote):
            pass
    assert caught.value.code == SSH_HOST_KEY_FAILED


@pytest.mark.asyncio
async def test_missing_identity_is_classified_without_network(tmp_path, app_config) -> None:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("", encoding="utf-8")
    remote = app_config.remotes[0].model_copy(
        update={
            "ssh": app_config.remotes[0].ssh.model_copy(
                update={
                    "known_hosts": str(known_hosts),
                    "auth": app_config.remotes[0].ssh.auth.model_copy(
                        update={"identity_file": str(tmp_path / "missing-key")}
                    ),
                }
            )
        }
    )
    backend = ProbeBackend(1)
    with pytest.raises(ProbeFailure) as caught:
        async with backend.connect(remote):
            pass
    assert caught.value.code == SSH_AUTH_FAILED
