from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import CONFIG_INVALID, DiscoveryError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerConfig(StrictModel):
    probe_interval_seconds: float = Field(default=60, gt=0)
    probe_timeout_seconds: float = Field(default=10, gt=0)
    unavailable_after_failures: int = Field(default=3, ge=1)
    max_concurrent_probes: int = Field(default=10, ge=1)
    startup_probe_timeout_seconds: float = Field(default=30, gt=0)


class AuthConfig(StrictModel):
    type: Literal["identity_file", "agent"]
    identity_file: str | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> "AuthConfig":
        if self.type == "identity_file" and not self.identity_file:
            raise ValueError("identity_file is required when auth.type is identity_file")
        if self.type == "agent" and self.identity_file is not None:
            raise ValueError("identity_file is not allowed when auth.type is agent")
        return self


class SSHConfig(StrictModel):
    user: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    auth: AuthConfig
    known_hosts: str = "~/.ssh/known_hosts"
    connect_timeout_seconds: float = Field(default=8, gt=0)


CheckType = Literal[
    "local",
    "ssh",
    "ping",
    "tcp",
    "telnet",
    "nc",
    "curl",
    "path",
    "process",
    "command",
    "script",
]


class HealthCheckConfig(StrictModel):
    name: str = Field(min_length=1)
    type: CheckType
    required: bool = True
    timeout_seconds: float | None = Field(default=None, gt=0)
    count: int = Field(default=1, ge=1, le=10)
    target: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    argv: list[str] | None = None
    script: str | None = None

    @model_validator(mode="after")
    def validate_by_type(self) -> "HealthCheckConfig":
        if self.type == "curl":
            if not self.target:
                raise ValueError("target is required for curl checks")
            parsed = urlparse(self.target)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("curl target must be an http or https URL")
        if self.type in {"tcp", "telnet", "nc"} and self.port is None:
            raise ValueError(f"port is required for {self.type} checks")
        if self.type == "path" and not self.target:
            raise ValueError("target is required for path checks")
        if self.type == "process" and not self.target:
            raise ValueError("target is required for process checks")
        if self.type == "command" and not self.argv:
            raise ValueError("non-empty argv is required for command checks")
        if self.type == "script" and not self.script:
            raise ValueError("script is required for script checks")
        return self


class CapabilityConfig(StrictModel):
    name: str = Field(min_length=1)
    description: str | None = None
    usage: str | None = None
    remark: list[str] = Field(default_factory=list)
    health_checks: list[HealthCheckConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_checks(self) -> "CapabilityConfig":
        _ensure_unique([check.name for check in self.health_checks], "health check")
        if any(check.type in {"local", "ssh"} for check in self.health_checks):
            raise ValueError("local and ssh checks are only allowed at remote level")
        return self


class RemoteConfig(StrictModel):
    name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    description: str | None = None
    connection: Literal["local", "ssh"] = "ssh"
    ssh: SSHConfig | None = None
    health_checks: list[HealthCheckConfig] = Field(default_factory=list)
    capabilities: list[CapabilityConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_members(self) -> "RemoteConfig":
        _ensure_unique([check.name for check in self.health_checks], "health check")
        _ensure_unique([cap.name for cap in self.capabilities], "capability")
        connection_checks = [
            check for check in self.health_checks if check.type in {"local", "ssh"}
        ]
        if self.connection == "ssh":
            if self.ssh is None:
                raise ValueError("ssh is required when connection is ssh")
            if (
                len(connection_checks) != 1
                or connection_checks[0].type != "ssh"
                or not connection_checks[0].required
            ):
                raise ValueError(
                    "ssh remotes require exactly one required ssh health check"
                )
        else:
            if self.ssh is not None:
                raise ValueError("ssh is not allowed when connection is local")
            if (
                len(connection_checks) != 1
                or connection_checks[0].type != "local"
                or not connection_checks[0].required
            ):
                raise ValueError(
                    "local remotes require exactly one required local health check"
                )
        return self


class AppConfig(StrictModel):
    schema_version: Literal[1]
    server: ServerConfig = Field(default_factory=ServerConfig)
    remotes: list[RemoteConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_remotes(self) -> "AppConfig":
        _ensure_unique([remote.name for remote in self.remotes], "remote")
        return self


def _ensure_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates = {value for value in values if value in seen or seen.add(value)}
    if duplicates:
        raise ValueError(f"duplicate {label} name(s): {', '.join(sorted(duplicates))}")


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be a mapping")
        return AppConfig.model_validate(raw)
    except DiscoveryError:
        raise
    except Exception as exc:
        raise DiscoveryError(CONFIG_INVALID, _compact_validation_error(exc)) from exc


def _compact_validation_error(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        parts: list[str] = []
        for item in errors(include_url=False):
            location = ".".join(str(part) for part in item.get("loc", ()))
            parts.append(f"{location}: {item.get('msg', 'invalid value')}")
        if parts:
            return "; ".join(parts[:8])
    return str(exc)
