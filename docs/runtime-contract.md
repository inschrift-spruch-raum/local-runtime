# Runtime Contract

## Scope

`local-runtime` 是 GUI 和 headless MCP endpoint 的通用控制面运行时。它只负责会话发现、生命周期、loopback JSON-RPC substrate、路由和工具 schema 快照。它不提供 stdio/MCP 外层。

## Adapter seam

具体程序必须通过适配器提供以下内容：

- GUI 请求如何通过 `execution_gate` 切换到宿主主线程；
- GUI 生命周期何时调用 `MultiModeRuntime.start()` 和 `stop()`；
- headless worker 的启动命令、输入引用和 ready handshake；
- 工具 handler、capability、identity 和 metadata。

框架不得依赖宿主 SDK、输入文件格式、数据库 API、安装目录、进程名称或具体工具名称。

## Registry schema

活动记录位于 `LOCAL_RUNTIME_REGISTRY_PATH` 指定的 JSON 文件，未指定时为 `~/.local-runtime/sessions.json`。记录包括：

- `session_id` 和 `lease_id`；
- `mode`: `gui` 或 `headless`；
- loopback `endpoint`；
- 可选 `pid`、`label`、`capabilities`；
- 由 adapter 提供的 opaque `identity` 和 `metadata`；
- `started_at`、`last_heartbeat`。

注册表通过 lock 文件和临时文件 `os.replace` 保证跨进程更新；坏 JSON 会被移到 `.corrupt` 文件。过期记录保留有限历史，便于路由器给旧 session id 返回可行动的错误。

## Routing

路由参数使用 `session_id`。恰好一个活动会话时可以省略；零个或多个活动会话时必须显式传入。转发前会剥离 `session_id`，因此下游工具只收到自己的参数。

可选 `IdentityVerifier` 在转发前验证 opaque identity；验证失败必须 fail closed。路由器不读取或比较具体程序的路径、指纹或数据库字段。

## Headless worker

worker 每个输入引用独占一个进程。manager 使用 `stdin=DEVNULL`，后台消费 stderr，轮询 `ping`，ready 后才注册。关闭先 `terminate()` 并等待，超时才 `kill()`。启动失败不留下 registry record。

manager 持有每个 headless session 的 lease 并定期 heartbeat；lease 丢失时回收对应 worker。控制面只能关闭 manager 自己拥有的 headless session，GUI session 必须由其 `MultiModeRuntime` owner 关闭。

## Transport scope

`LocalMcpServer` 是单请求 HTTP JSON-RPC 2.0 substrate：支持对象参数、错误 envelope、响应大小限制和 notification，不实现 stdio、批处理或 MCP initialize 握手。消费项目负责把 `ControlPlane.dispatch` 接到具体的 MCP/stdio 外层。

## Non-goals

本仓库不包含任何具体程序的插件、工具 schema、分析逻辑、安装器、身份算法或 worker entrypoint。它们应放在使用本范式的独立项目中。
