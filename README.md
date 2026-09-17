# remote-computer-use

一个只读的 MCP Server，用于发现本机与 SSH 远端、持续检查健康状态，以及按需返回能力说明。

它只执行 YAML 中由管理员预先定义的探活检查。MCP 客户端不能通过本服务提交任意命令、URL、HTTP 请求、SSH 隧道、文件传输或业务操作。

## 要求

- Python 3.11+
- SSH 目标机仅允许公钥或 SSH Agent 认证
- SSH 目标机公钥已经写入配置指定的 `known_hosts`

## 安装

```bash
uv sync --extra test
cp config.example.yaml config.yaml
```

编辑 `config.yaml` 后先校验：

```bash
uv run remote-computer-use validate --config config.yaml
```

配置的 JSON Schema 位于 `schema/remote-computer-use.schema.json`。

## 启动

stdio 是默认传输方式：

```bash
uv run remote-computer-use --config /absolute/path/config.yaml
```

也可以显式指定子命令：

```bash
uv run remote-computer-use serve --config /absolute/path/config.yaml
```

Streamable HTTP：

```bash
uv run remote-computer-use --config /absolute/path/config.yaml \
  --transport streamable-http --host 127.0.0.1 --port 8000
```

一次性运行探活并输出结构化结果：

```bash
uv run remote-computer-use check --config config.yaml
uv run remote-computer-use check --config config.yaml --remote black-lenovo
```

## MCP 客户端配置

本机 Codex CLI 可以直接注册 stdio 服务：

```bash
codex mcp add remote_computer_use -- \
  /absolute/path/.venv/bin/remote-computer-use \
  --config /absolute/path/config.yaml
```

也可以使用等价的客户端配置：

```json
{
  "mcpServers": {
    "remote-computer-use": {
      "command": "/absolute/path/.venv/bin/remote-computer-use",
      "args": ["--config", "/absolute/path/config.yaml"]
    }
  }
}
```

服务公开六个工具：

- `list_available_remotes()`：只返回当前可用远端名称。
- `list_all_remotes()`：返回全部远端的名称和状态。
- `list_available_capabilities(remote)`：返回连接参数及可用能力说明。
- `list_all_capabilities(remote)`：返回全部能力及简洁状态。
- `get_remote_status(remote)`：返回缓存中的远端和能力诊断信息。
- `refresh_health(remote?, capability?)`：执行配置中固定的有界探活。

前五个工具只读取内存缓存。只有定时任务和 `refresh_health` 会访问网络或目标机。

## 配置

完整示例见 [`config.example.yaml`](config.example.yaml)。`local` 对象直接在 MCP 主机探活，不经过 SSH：

```yaml
schema_version: 1
server:
  probe_interval_seconds: 60
  probe_timeout_seconds: 10
  unavailable_after_failures: 3
  max_concurrent_probes: 10
  startup_probe_timeout_seconds: 30

remotes:
  - name: localhost
    host: localhost
    connection: local
    health_checks:
      - name: local
        type: local
        required: true
    capabilities:
      - name: codebuddy
        health_checks:
          - name: executable
            type: command
            argv: [/usr/local/bin/codebuddy, --version]

  - name: example
    host: 192.0.2.10
    connection: ssh
    ssh:
      user: operator
      port: 22
      auth:
        type: identity_file
        identity_file: ~/.ssh/id_ed25519
      known_hosts: ~/.ssh/known_hosts
      connect_timeout_seconds: 8
    health_checks:
      - name: ssh
        type: ssh
        required: true
    capabilities:
      - name: godot
        description: Godot 无头检查
        usage: 使用 godot --headless --path <project>
        health_checks:
          - name: executable
            type: command
            argv: [godot, --version]
```

连接模式：

- `local`：能力检查直接在 MCP 主机执行，必须包含一个必需的 `local` 检查，并且不能配置 `ssh`。
- `ssh`：能力检查登录目标机后执行，必须配置 `ssh` 和一个必需的 `ssh` 检查。

SSH 认证模式：

- `identity_file`：必须给出密钥文件路径。
- `agent`：使用 SSH Agent 或 OpenSSH 默认身份，不允许 `identity_file`。

配置模型禁止额外字段，因此 `password`、`private_key`、`token` 等字段会导致校验失败。私钥内容不会进入进程配置、响应或日志。

## 当前本机实例

本机 Codex 已注册 `/Users/black94/project/remote-computer-use/.venv/bin/remote-computer-use`，读取被忽略的 `config.yaml`。其中：

- `localhost` 是本机直连对象，只探活 `/usr/local/bin/codebuddy --version`，不经过 SSH。
- `black-lenovo` 使用 `master@192.168.0.98:22` 的 Agent 认证；同一主机的 Tailscale 地址为 `100.125.171.64`。
- 远端固定版本路径为 `/home/master/.local/bin/godot`、`/home/master/Applications/blender-5.2.1-linux-x64/blender` 和 `/home/master/.local/bin/codebuddy`。
- Godot MCP 的远端回环端口是 `8000`（HTTP）和 `9500`（插件 WebSocket）；Blender 插件配置端口是 `9876`；ComfyUI 回环 API 是 `8188`。
- 这些端口需要由调用方按需建立 SSH 映射；本 MCP 只探活和说明，不建立隧道或转发业务请求。Godot、Blender、ComfyUI 未启动时，对应能力会按缓存状态显示为不可用或降级。

## 检查类型

支持 `local`、`ssh`、`ping`、`tcp`、`telnet`、`nc`、`curl`、`path`、`process`、`command` 和 `script`。

- `local` 对象的对象级和能力级检查都在 MCP 主机执行。
- `ssh` 对象的对象级检查在 MCP 主机执行，能力级检查在通过认证的 SSH 会话中执行。
- `tcp`、`telnet` 和 `nc` 需要 `port`，可选 `host`。
- `curl` 需要 HTTP 或 HTTPS `target`。
- `path` 和 `process` 使用 `target`。
- `command` 使用参数数组 `argv`。
- YAML 会把未加引号的 `true`、`false` 等值解析为布尔值；作为命令参数时需要写成字符串，例如 `["false"]`。
- `script` 使用固定的 `script` 字符串，仅适合受信任的管理员配置。
- `timeout_seconds` 可以覆盖服务器默认超时。
- `required: false` 的失败会显示诊断信息，但不会使对象下线。

能力检查只保存成功状态、错误码、检查时间和延迟。命令输出和 HTTP 响应体不会出现在 MCP 响应中。

## 状态

远端状态为 `unknown`、`healthy`、`degraded` 或 `unhealthy`。能力还可能为 `blocked`，表示所属远端当前不可连接。

首次成功立即上线。必需检查失败后先进入 `degraded`，达到 `unavailable_after_failures` 后进入 `unhealthy`。成功探活会立即清零连续失败次数。可用远端必须至少有一个可用能力。

YAML 文件变化时会自动重新校验。有效配置会原子替换当前配置并触发探活；无效配置会被拒绝，服务继续使用上一份配置。

## 日志

默认日志路径：

- 设置了 `XDG_STATE_HOME`：`$XDG_STATE_HOME/remote-computer-use/remote-computer-use.log`
- 其他环境：`~/.local/state/remote-computer-use/remote-computer-use.log`

可通过 `--log-file` 覆盖。日志按 10 MiB、5 个备份轮转，不记录私钥内容、HTTP 响应体或完整探活输出。

## 测试

```bash
uv run pytest
```

测试使用模拟 SSH 和 HTTP 状态，不需要真实远端。
