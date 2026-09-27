# Runtime Contract

## Scope

`local-runtime` 是 GUI 和 headless MCP endpoint 的通用控制面运行时。它只负责会话发现、生命周期、loopback JSON-RPC substrate、路由和工具 schema 快照。它不提供 stdio/MCP 外层。

## Adapter seam

具体程序必须通过适配器提供以下内容：

- GUI 请求如何通过 `execution_gate` 切换到宿主主线程；
- GUI 生命周期何时调用 `MultiModeRuntime.start()` 和 `stop()`；
- headless worker 的进程启动、输入引用和 ready handshake；
- 工具 handler、capability、identity 和 metadata。

框架不得依赖宿主 SDK、输入文件格式、数据库 API、安装目录、进程名称或具体工具名称。

GUI 构造和 headless `open()` 使用 `RegistrationDetails` 表达会话标签、能力、opaque identity 和 metadata；未提供时分别采用 `gui` 和 `headless` 标签。

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

worker 每个输入引用独占一个进程。宿主 adapter 通过 `WorkerLauncher(input_ref, endpoint_hint)` 启动进程并返回 `subprocess.Popen[bytes]`；`endpoint_hint.port` 为 `0`。adapter 必须选择可信的可执行文件，不得直接把用户输入作为可执行文件路径；启动时使用 argv 列表、`shell=False`、`stdin=DEVNULL`、`stdout=PIPE` 和 `stderr=PIPE`。manager 会拒绝缺少 stdout 或 stderr 管道的进程并回收它。从 stdout 读取一行 `LOCAL_RUNTIME_ENDPOINT <json>` 握手；worker 必须先自行绑定 loopback 随机端口，再立即 flush 它实际绑定的 `host`、`port` 和 `path`。manager 只对握手返回的 endpoint 轮询 `ping`，ready 后才注册，因此不存在先探测再释放临时端口的窗口。stdout 握手之后的内容会被持续消费但不会被解释，stderr 也会持续 drain；诊断日志写入 stderr。启动失败不留下 registry record。关闭先 `terminate()` 并等待，超时才 `kill()`。

manager 持有每个 headless session 的 lease 并定期 heartbeat；lease 丢失时回收对应 worker。控制面只能关闭 manager 自己拥有的 headless session，GUI session 必须由其 `MultiModeRuntime` owner 关闭。

## Transport scope

`LocalMcpServer` 是单请求 HTTP JSON-RPC 2.0 substrate：支持对象参数、错误 envelope、响应大小限制和 notification，不实现 stdio、批处理或 MCP initialize 握手。`JsonRpcClient.request()` 的超时、request id 和响应大小上限由 `JsonRpcRequestOptions` 表达；超限响应在 JSON 解码前拒绝。消费项目负责把 `ControlPlane.dispatch` 接到具体的 MCP/stdio 外层。

## Non-goals

本仓库不包含任何具体程序的插件、工具 schema、分析逻辑、安装器、身份算法或 worker entrypoint。它们应放在使用本范式的独立项目中。
