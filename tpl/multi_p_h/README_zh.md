# multi_p_h

[English](README.md)

多进程 HTTP 服务模板 —— FastAPI 应用由 uvicorn 以 prefork 模式提供服务
（N 个 worker 进程通过 `SO_REUSEPORT` 共享同一个端口）。

## 适用场景

`multi_p_h` 是 2026 年 Python 跑 HTTP 服务的标准默认选择。下列情况优先
选它：

- **公开 API** —— 任何 mobile / web / 第三方客户端要打的接口。
- **希望任何工程师都能 debug 的内部 API** —— `curl`、浏览器、Postman 都
  能直接用，不需要专用客户端工具。
- **REST / JSON 服务** —— 资源-端点的映射很自然。
- **多语言客户端** —— pydantic model 自动生成 OpenAPI spec，任何语言
  都可以用 codegen 生成客户端 SDK。
- **没有 gRPC 基础设施** —— 也不打算搭。

判断标志：HTTP 是服务对接外界的最大公约数 —— 客户端、工具、代理、
observability 生态都最完整。

## 不适用场景

| 需求 | 选 |
|---|---|
| 后端服务到服务，性能敏感 | `multi_p_g`（gRPC） |
| 一等公民的 streaming RPC | `multi_p_g` |
| 长跑后台 worker，没有入站流量 | `multi_p` |
| 消费队列的后台 worker | `multi_t_q` |
| 单一逻辑循环、不需要 HTTP | `simple` |
| 对接老 Thrift 基础设施 | `multi_p_t` |

如果服务**没有入站 HTTP 流量** —— 它在拉队列、按时间表跑、处理流 ——
选 `multi_p_h` 就是凭空多了一个 web server 用不上。用 `multi_p` 或
`multi_t_q` 替代。

## 模板提供了什么

- **FastAPI** 路由 + **pydantic** request/response 校验 + 自动生成的
  OpenAPI 文档（`/docs` 和 `/redoc`）。
- **uvicorn[standard]** 配合 `uvloop` + `httptools`（Linux/macOS 上
  生产级性能）。
- **prefork 多进程** —— `uvicorn.run(workers=N)` fork 出 N 个子进程
  通过 `SO_REUSEPORT` 共享同一个监听端口；内核帮忙在 worker 之间分配
  入站连接。代码层面不需要任何协调。
- **graceful shutdown** —— uvicorn 正确处理 `SIGTERM`：父进程通知
  子进程，每个子进程把在途请求处理完、跑完应用 shutdown hook，再退出。
- **`pyproject.toml`**（PEP 621），Python 3.12+。一条命令安装：
  `pip install -e .`。

## 安装

    pip install -e .

## 运行

    ./control.sh start
    ./control.sh status
    ./control.sh stop

或直接：

    python -m multi_p_h.main

试一下：

    curl http://127.0.0.1:8000/ping
    curl -X POST http://127.0.0.1:8000/echo \
      -H 'content-type: application/json' \
      -d '{"message":"hello"}'

打开 `http://127.0.0.1:8000/docs` 看 Swagger UI。

`settings.yaml` 里调整 host / port / workers：

    service:
      host: 127.0.0.1   # 改 0.0.0.0 才会对外暴露
      port: 8000
      workers: 4

## 业务代码放在哪

- **路由** 在 `multi_p_h/main.py`。每个 endpoint 加一个
  `@app.get(...)` / `@app.post(...)`；pydantic 模型在那里声明，或者
  从 `models/` 子包导入。
- **业务逻辑** 在 `multi_p_h/core.py`（`Service` 类）。路由调
  `service.method(...)` —— 保持 handler 薄薄一层。
- **配置** 通过 `multi_p_h/config.py`（Dynaconf）读。worker 都从同一
  个 `settings.yaml` 初始化；环境变量覆盖通过
  `DYNACONF_SERVICE__PORT=8080` 这种方式。

## 为什么多进程交给 uvicorn 而不是自己写

这个仓库里其它模板（`multi_p`、`multi_t`）都是手写一个 worker 池：用
`multiprocessing.Process` 或 `threading.Thread` 起 worker、自己管生命
周期、用 `QueueListener` 做跨进程日志、用 `mp.Event` + `terminate()`
做关停兜底。

`multi_p_h` 不这么做，因为 **uvicorn 在 HTTP server 这件事上已经把上
面的全部做完了，而且做得更好**：

1. **prefork 模型** —— uvicorn 在父进程 bind listening socket，fork
   出来的 worker 继承它。每个 worker 在同一个端口 accept，内核
   （Linux/macOS 配 `SO_REUSEPORT`）公平分配入站连接。不需要 `mp.Queue`，
   不需要用 `multiprocessing.reduction` 传 socket。
2. **信号处理和 graceful shutdown** 是一等公民：父进程收 `SIGTERM` →
   通知子进程 → 每个子进程停止接受新连接、完成在途请求、跑 `lifespan`
   shutdown hook、干净退出。自己写一遍这些逻辑的代码量比模板的剩余部分
   加起来还多。
3. **reload / hot-restart** 开发时一个 `--reload` 标志就解决了。
4. **生产部署** 是同一个二进制 —— 换 process manager（systemd、k8s、
   gunicorn 当 supervisor）但 uvicorn 本身不变。

模板的职责是教**正确的模式**，不是发明一个比已经在 `pip install` 里
的更差的版本。

## 为什么日志走 stdout 而不是文件

另一个多进程模板（`multi_p`）配的是 `TimedRotatingFileHandler`，通过
`QueueListener` 把记录串起来避免 N 个进程在同一个日志文件上打架。

`multi_p_h` 故意不这么做。**所有 log handler 都是 `StreamHandler`
往 stdout 写**，由 `control.sh` 把 stdout/stderr 重定向到
`logs/multi_p_h.out` 和 `logs/multi_p_h.err`。

为什么：

1. POSIX 保证 ≤ `PIPE_BUF`（一般 4096 字节）的写入到共享 fd 是原子的。
   每条日志一行，不同 worker 的写入不会相互交错（行内不会乱）。
2. **生产 HTTP 服务通常不自己 rotate 日志**。`logrotate`（系统级）或
   k8s 日志收集（sidecar / fluent-bit / vector）才是该处理 rotation 的
   层 —— 它们能感知进程，Python stdlib handler 做不到。
3. 一个 worker manager 是 uvicorn 的模板（已经是单独的进程树），还要
   再加 `QueueListener` 这套机制，等于是绕过 uvicorn 自己重做一份。
   跨进程日志要么走 OS 层（这个模板）要么走外部（log shipper）。两条路
   都对，进程内 queue 在这个场景下是错的。

如果需要 `multi_p_h.log` 自动 rotate：

- **Linux：** 加一份 `/etc/logrotate.d/multi_p_h` 配置指向被重定向的文件。
- **k8s：** stdout 直接进 `kubectl logs`，容器里不需要 rotate。
- **单机开发：** 手动 rotate 或用 daemontools 的 `multilog`。

## 项目变大时的目录结构演进

模板出厂状态是 **Stage 0**。按下面四个阶段顺序演进 —— 不要在 Stage 0
就预先建空目录"以备将来"，也不要跳级。

### Stage 0 — 初始（≤ 3 个模块）

生成器开箱即用的状态：

    multi_p_h/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── multi_p_h/
        ├── __init__.py
        ├── main.py        FastAPI app + 路由 + run()
        ├── config.py      Dynaconf 加载
        └── core.py        Service 业务逻辑

**这一阶段的规则：** 路由、pydantic 模型、Service 都扁平放着。除非有
真实理由，先不要拆子包。

### Stage 1 — 小服务（5–8 个模块，仍扁平）

加了几个辅助模块。仍扁平：

    multi_p_h/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── auth.py
    ├── client.py
    └── metrics.py

### Stage 2 — 按职责拆子包（8–20 个模块）

把路由拆进 `routers/` 子包，模型拆进 `models/`。`main.py`、`config.py`、
`core.py` 留顶层。

    multi_p_h/
    ├── __init__.py
    ├── main.py            app 定义 + router mount 点
    ├── config.py
    ├── core.py            共用业务逻辑
    ├── routers/           按资源拆 router（FastAPI APIRouter）
    │   ├── __init__.py
    │   ├── users.py
    │   └── billing.py
    ├── models/            pydantic schema
    │   ├── __init__.py
    │   ├── user.py
    │   └── invoice.py
    ├── services/          按 concern 拆业务逻辑
    │   ├── __init__.py
    │   └── billing.py
    └── clients/           出站 API / DB 客户端
        ├── __init__.py
        └── stripe.py

`main.py` 变成"接线层"：

```python
from fastapi import FastAPI
from multi_p_h.routers import users, billing

app = FastAPI()
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(billing.router, prefix="/billing", tags=["billing"])
```

### Stage 3 — 大服务（20+ 模块）

子包内部再长出子包。加上与包平级的兄弟目录：

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── multi_p_h/         包
    ├── tests/             pytest 测试树，结构镜像 multi_p_h/
    │   ├── unit/
    │   └── integration/
    ├── scripts/           一次性运维脚本（不是包）
    ├── docs/              架构文档、runbook
    └── ops/               Dockerfile、k8s manifest、terraform

### 跨阶段不变的规则

下面三条从 Stage 0 到 Stage 3 一直成立：

1. `main.py`、`config.py`、`core.py` 永远在包根。
2. `control.sh`、`pyproject.toml`、`settings.yaml` 永远在项目根。
3. 项目根下只有一个带 `__init__.py` 的目录 —— 包目录。

## HTTP 相关注意事项

- **开发时用 `workers=1`。** 多 worker 模式不能用 auto-reload。开发时
  把 `settings.yaml` 改成 `workers: 1`，跑 `python -m multi_p_h.main`
  （或者直接 `uvicorn --reload`）。
- **worker 之间不共享状态。** 每个 worker 是独立进程，有自己的内存。
  进程内缓存、限流器之类的状态都是 per-worker。要跨 worker 共享必须用
  Redis / 数据库 / sticky routing —— 跟 `multi_p` 是同样的约束。
- **用 lifespan 做启动/关停 hook。** 用 FastAPI 的 `lifespan` 上下文
  管理器（`@asynccontextmanager async def lifespan(app)`）做每个 worker
  的初始化/清理 —— 开 DB pool、warm 缓存、关连接。
- **`workers > 1` 和 `--reload` 互斥。** uvicorn 拒绝同时跑两者。二选一。
- **macOS 上的 prefork 能跑** 但 `SO_REUSEPORT` 语义跟 Linux 不一样：
  macOS 允许多个 bind，但负载分配没那么均。开发够用；生产请部署到
  Linux。
- **不要在路由 handler 里跑长时间 CPU 工作。** FastAPI 是 async-friendly
  的；阻塞操作会阻塞那个 worker 的事件循环。要么把 handler 写成 `def`
  （同步，跑在 thread pool 里），要么 offload 到后台队列 /
  `multi_t_q` 这种 worker 池。
- **TLS / auth / CORS 是你的责任。** 不要直接把 `multi_p_h` 暴露在公
  网上 —— 应该在前面加反向代理（nginx、Caddy、Cloudflare）做 TLS 终端、
  限流、DoS 防护。uvicorn 能做 TLS，但专用代理做得更好。
