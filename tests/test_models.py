from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from remote_computer_use.errors import CONFIG_INVALID, DiscoveryError
from remote_computer_use.models import AppConfig, load_config

from .conftest import config_data


def test_valid_config_defaults() -> None:
    config = AppConfig.model_validate(config_data())
    remote = config.remotes[0]
    assert remote.ssh.port == 2222
    assert config.schema_version == 1
    assert remote.capabilities[0].health_checks[0].type == "curl"


@pytest.mark.parametrize("field", ["password", "private_key", "token"])
def test_credential_fields_are_rejected(field: str) -> None:
    raw = config_data()
    raw["remotes"][0]["ssh"][field] = "secret"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw)


def test_agent_rejects_identity_file() -> None:
    raw = config_data()
    raw["remotes"][0]["ssh"]["auth"] = {
        "type": "agent",
        "identity_file": "~/.ssh/id_ed25519",
    }
    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw)


def test_remote_requires_one_required_ssh_check() -> None:
    raw = config_data()
    raw["remotes"][0]["health_checks"] = []
    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw)


def test_local_remote_omits_ssh_and_requires_local_check() -> None:
    raw = config_data()
    remote = raw["remotes"][0]
    remote["connection"] = "local"
    remote.pop("ssh")
    remote["health_checks"] = [
        {"name": "local", "type": "local", "required": True}
    ]
    config = AppConfig.model_validate(raw)
    assert config.remotes[0].connection == "local"
    assert config.remotes[0].ssh is None


def test_local_remote_rejects_ssh_configuration() -> None:
    raw = config_data()
    raw["remotes"][0]["connection"] = "local"
    raw["remotes"][0]["health_checks"] = [
        {"name": "local", "type": "local", "required": True}
    ]
    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw)


def test_duplicate_names_are_rejected() -> None:
    raw = config_data()
    raw["remotes"].append(deepcopy(raw["remotes"][0]))
    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw)


def test_load_config_has_stable_error_code(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("schema_version: 1\nremotes: wrong\n", encoding="utf-8")
    with pytest.raises(DiscoveryError) as caught:
        load_config(path)
    assert caught.value.code == CONFIG_INVALID
