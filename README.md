# local-runtime

一个与具体程序无关的 Python 运行时范式，用来同时承载 GUI 宿主和无头 worker 的 MCP/JSON-RPC 操作。

它只提供控制面和生命周期基础设施，不包含任何宿主 SDK、数据库或业务工具代码。具体应用通过适配器注入自己的 handler、宿主线程执行器和 worker 启动命令。

## 拓扑

```text
MCP client / control plane
          |
          | session_id + local JSON-RPC
          v
    InstanceRegistry
       /       \
      /         \
 GUI endpoint   headless worker endpoint
```

GUI 和 headless 共享 `Endpoint`、注册表、lease heartbeat、路由器和工具目录。每个会话只拥有一个 loopback endpoint；同一会话内部的线程串行化和宿主主线程约束通过适配器的 `execution_gate` 注入。

## 公共模块

| 模块 | 责任 |
| --- | --- |
| `types.py` | `Endpoint`、`Registration`、`InstanceRecord`、JSON 类型和运行模式 |
| `registry.py` | 原子 JSON 注册表、跨进程锁、opaque session id、lease、过期记录 |
| `transport.py` | loopback JSON-RPC client/server；body 限制和异常脱敏 |
| `router.py` | 单会话自动选择、多会话显式选择、身份校验 seam、转发 |
| `catalog.py` | 工具 schema 的原子快照和重复名称检查 |
| `gui.py` | GUI 宿主的通用 endpoint adapter，不依赖 GUI SDK |
| `headless.py` | 子进程启动、ready ping、stderr drain、优雅停止和强制回收 |
| `runtime.py` | 注册、heartbeat、注销和 adapter 的逆序清理 |
| `server.py` | 控制面管理方法和远程工具调用入口 |

## 最小接入示例

```python
from local_runtime import GuiAdapter, InstanceRegistry, InstanceRouter, MultiModeRuntime

registry = InstanceRegistry()
gui = GuiAdapter(
    dispatcher,
    execution_gate=host_main_thread.run,
    label="interactive-host",
    capabilities=("tools",),
)
runtime = MultiModeRuntime(registry, gui)
record = runtime.start()

router = InstanceRouter(registry)
result = router.route("example/tool", {"session_id": record.session_id, "value": 1})

runtime.stop()
```

`dispatcher(method, params)`、`execution_gate(call)`、worker command factory 和工具目录是具体程序注入的 seams。框架不会导入或命名具体工具，也不会猜测宿主的线程模型。

无头模式使用 `HeadlessSessionManager`，通过 `WorkerCommandFactory(input_ref, endpoint)` 生成命令。worker 必须在准备好同一 JSON-RPC endpoint 后响应 `ping`；manager 只有在 ready 后才写入注册表。

## 生命周期契约

1. endpoint bind 成功并可响应 `ping` 后，才发布 registry record。
2. registry record 带有随机 `session_id` 和 `lease_id`；旧 owner 不能注销后来复用的记录。
3. stop 顺序固定为停止 heartbeat、注销 lease、停止 endpoint 或 worker。
4. worker 的 stdin 与控制面 stdio 脱钩，stderr 必须持续 drain；stdout 不承载诊断日志。
5. 路由只允许 loopback endpoint；没有 session id 时仅在恰好一个会话存在时自动选择。
6. `identity` 与 `metadata` 是 opaque JSON，由具体程序定义；框架不解释路径、二进制或数据库字段。

## Transport scope

`LocalMcpServer` 提供单请求 HTTP JSON-RPC 2.0 substrate：支持对象参数、响应大小限制和 notification，不实现 stdio、批处理或 MCP initialize 握手。消费项目可把 `ControlPlane.dispatch` 接到自己的 stdio/MCP 外层。

## 开发

使用 `uv` 管理 Python 运行时和依赖：

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
```

验证顺序是 `ruff check` -> `ruff format --check` -> `basedpyright` -> `pytest`。发布前运行 `uv build` 并检查 wheel 内容。
