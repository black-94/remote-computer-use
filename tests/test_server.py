from __future__ import annotations

import asyncio

from remote_computer_use.server import build_server
from remote_computer_use.service import DiscoveryService

from .conftest import FakeBackend


def test_mcp_exposes_only_the_six_discovery_tools(tmp_path, app_config) -> None:
    service = DiscoveryService(
        tmp_path / "config.yaml",
        config=app_config,
        backend=FakeBackend(),
    )
    server = build_server(service)
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "list_available_remotes",
        "list_all_remotes",
        "list_available_capabilities",
        "list_all_capabilities",
        "get_remote_status",
        "refresh_health",
    }
    assert by_name["refresh_health"].inputSchema["properties"].keys() == {
        "remote",
        "capability",
    }
    for forbidden in {"command", "url", "request", "body", "script", "argv"}:
        assert forbidden not in str([tool.inputSchema for tool in tools]).lower()
