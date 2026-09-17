from __future__ import annotations

import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_stdio_server_initializes_and_serves_cached_status(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
schema_version: 1
server:
  probe_interval_seconds: 60
  probe_timeout_seconds: 0.2
  unavailable_after_failures: 3
  max_concurrent_probes: 1
  startup_probe_timeout_seconds: 1
remotes:
  - name: unavailable-test
    host: 127.0.0.1
    ssh:
      user: nobody
      port: 9
      auth:
        type: agent
      known_hosts: /dev/null
      connect_timeout_seconds: 0.2
    health_checks:
      - name: ssh
        type: ssh
        required: true
    capabilities:
      - name: sample
        health_checks:
          - name: tool
            type: command
            argv: ["false"]
""".lstrip(),
        encoding="utf-8",
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "remote_computer_use",
            "--config",
            str(config),
            "--log-file",
            str(tmp_path / "server.log"),
        ],
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 6
            result = await session.call_tool("list_all_remotes")
            assert result.structuredContent == {
                "remotes": [{"name": "unavailable-test", "status": "degraded"}]
            }
