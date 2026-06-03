# multi_p_g

[English](README.md)

多进程 gRPC 服务模板 —— N 个 worker 进程通过 `SO_REUSEPORT` 共享端口，
带跨进程日志、graceful drain，以及一个能跑的 Ping/Echo 示例。

## 适用场景

- **后端服务到服务。** RPC over HTTP/2 + protobuf 是 2026 年高 RPS 内部
  流量的现代默认。
- **需要 streaming。** gRPC 把 4 种 streaming（unary、server stream、
  client stream、bidi）作为 IDL 一等公民。
- **跨语言客户端。** Java / Go / Rust / TypeScript / C++ 都说同一个
  protobuf wire format，客户端质量相当。
- **已经有 gRPC 基础设施。** Envoy / Linkerd / Istio 把 gRPC 作为一等
  公民支持 —— 负载均衡、retry、deadline、tracing 都在 mesh 层配置。
- **性能。** P99 通常几毫秒（HTTP/JSON 通常十几毫秒）；protobuf 序列化
  比 JSON 在网络上小得多。

## 不适用场景

| 需求 | 选 |
|---|---|
| 公开 API（web/mobile/第三方）| `multi_p_h`（HTTP）|
| 任何人能 curl debug 的需求 | `multi_p_h` |
| 长跑后台 worker，没有入站流量 | `multi_p` |
| 消费队列的后台 worker | `multi_t_q` |
| 没有 gRPC 基础设施可依赖 | `multi_p_h`（摩擦低）|
| 必须接老 Thrift 系统 | `multi_p_t` |

## 模板提供了什么

- **`pyproject.toml`** 运行时依赖 `grpcio` + `protobuf`；`grpcio-tools`
  通过 `pip install -e '.[dev]'` 装上做 codegen。
- **`proto/service.proto`** —— 示例 IDL，定义了 `PingService`（Ping +
  Echo）。修改这个文件后跑 `./gen.sh` 重新生成。
- **`gen.sh`** —— 包装 `python -m grpc_tools.protoc`，把 `proto/*.proto`
  全部重新生成到 `multi_p_g/pb/`，并 patch 生成的 `*_pb2_grpc.py` 用
  相对 import。
- **预生成的 `multi_p_g/pb/*.py` 已提交到模板** —— `pip install -e .`
  装完立即得到一个能跑的 server。
- **多进程 server** 配 `SO_REUSEPORT` —— N 个 worker 全部 bind 同一端口，
  内核帮忙在 worker 之间分配连接。单机 fan-out 不需要外部负载均衡器。
- **跨进程日志** 走 `QueueHandler` + `QueueListener`（同 `multi_p` 的方案）：
  单一日志文件、不会 interleave。
- **Graceful drain** —— worker 调 `server.stop(grace=N)` 让在途 RPC 跑完
  再退出。
- **有界关停** —— 父进程给每个 worker `grace + 5s` 退出窗口，超时
  `terminate()` 再 `kill()` 兜底。

## 安装

    pip install -e .

要做 codegen（改 `proto/` 下任何文件后）：

    pip install -e '.[dev]'   # 加 grpcio-tools
    ./gen.sh

## 运行

    ./control.sh start
    ./control.sh status
    ./control.sh stop

或直接：

    python -m multi_p_g.main

Python 客户端调用：

```python
import grpc
from multi_p_g.pb import service_pb2, service_pb2_grpc

with grpc.insecure_channel('127.0.0.1:50051') as ch:
    stub = service_pb2_grpc.PingServiceStub(ch)
    print(stub.Ping(service_pb2.PingRequest()).message)
    print(stub.Echo(service_pb2.EchoRequest(message="hello")).text)
```

或者用 `grpcurl`：

    grpcurl -plaintext -d '{"message":"hello"}' \
      127.0.0.1:50051 service.PingService/Echo

（`grpcurl` 需要 server reflection —— 见下面 "gRPC 相关注意事项"。）

## 配置

`settings.yaml` 里调：

    service:
      host: '[::]'              # 监听所有接口，IPv4 + IPv6
      port: 50051
      workers: 4                # OS 进程数
      threads_per_worker: 10    # 每个 worker 内并发处理的 RPC 数
      shutdown_grace: 10        # 在途 RPC 完成的秒数预算

## 业务代码放在哪

**`multi_p_g/handler.py`** —— 替换 `Ping()` / `Echo()` 的方法体，并按
你的 `.proto` 加新方法。class 签名必须继承
`service_pb2_grpc.<YourService>Servicer`。

**`multi_p_g/core.py`** —— worker 池编排（`Multi_p_gService`）和 worker
入口（`_worker_main`）。这里不放业务逻辑；这是底盘代码。

**`multi_p_g/main.py`** —— 入口 + `QueueListener` setup。这里通常只
改 lifecycle hook（DB pool 开/关之类的），围绕
`Multi_p_gService.run()` 加。

改 `.proto` 后的工作流：

1. 编辑 `proto/service.proto`（或者新增 `proto/*.proto` 文件）
2. `./gen.sh`（重新生成 `multi_p_g/pb/` 下所有内容）
3. 更新 `handler.py` 实现新增的 RPC

## protobuf 包名 vs Python 包名

示例 `proto/service.proto` 用的是 **`package service;`**，故意起了一个
跟 Python 包名（`multi_p_g`）不同的中性名字。这是有意的。

`pygen.sh` 在生成项目时把字面量 `multi_p_g` 全文替换。生成的 `*_pb2.py`
里包含一个二进制 protobuf descriptor，里面有**长度前缀**（比如
`\x12\x07service` = "字段 2，长度 7，值 'service'"）。如果 protobuf
的 package 也叫 `multi_p_g`（9 字节），替换成不同长度的用户名（比如
`rpc_server` 是 10 字节）会破坏长度前缀，descriptor 加载会失败。

把 protobuf package 保持成 `service` 完全规避了这个问题 —— 二进制
descriptor 不再引用 Python 包名。

替换成你自己的 `proto/service.proto` 时，protobuf package 名字随便起，
但**不要把 Python 项目名字塞进 protobuf package**。两个命名空间在 gRPC
wire format 里是真正独立的。

## 为什么用 SO_REUSEPORT 而不是负载均衡器

`SO_REUSEPORT` 是 Linux/macOS 的 socket 选项，让多个进程能 bind 同一个
端口；内核对入站连接做 hash 分发到各 listener。我们通过 gRPC 的
`grpc.so_reuseport=1` 选项传：

```python
server = grpc.server(
    futures.ThreadPoolExecutor(max_workers=10),
    options=[("grpc.so_reuseport", 1)],
)
server.add_insecure_port("[::]:50051")
```

单机 N worker fan-out 这个层级它正合适 —— 没有 sidecar、没有用户态
LB，内核搞定。

**生产多机部署**通常在前面还要加 Envoy / Linkerd / service mesh：那一
层给你 retry policy、circuit breaker、mTLS、observability hook。
SO_REUSEPORT 解决主机内的故事，service mesh 解决跨主机的故事 ——
两者是组合关系。

## 项目变大时的目录结构演进

模板出厂状态是 **Stage 0**。按下面顺序演进，不要预先建空目录"以备
将来"，也不要跳级。

### Stage 0 — 初始（≤ 5 个模块）

注意：跟其他模板的"≤ 3 个模块"不同 —— gRPC 模板出厂就是 5 个，因为
handler 跟业务逻辑天然分开，生成的 stub 也单独住一个子包里。

    multi_p_g/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── gen.sh
    ├── proto/
    │   └── service.proto      ← 你编辑这里
    └── multi_p_g/
        ├── __init__.py
        ├── main.py            入口 + QueueListener
        ├── config.py          Dynaconf 加载
        ├── core.py            worker 池编排
        ├── handler.py         servicer 实现
        └── pb/                ← 生成内容，已提交（不要手改）
            ├── __init__.py
            ├── service_pb2.py
            └── service_pb2_grpc.py

两个目录从第一天就独立出来：

- **`proto/`** —— IDL 源码。人编辑。后面要加 `*.proto` 文件就丢这里。
- **`multi_p_g/pb/`** —— 生成的 stub。`gen.sh` 写到这里；不要手改。
  让它住在 Python 包内意味着 import 长这样：
  `from multi_p_g.pb import service_pb2`，调用点一眼能看出"这是生成的"。

### Stage 1 — 小服务（更多 RPC、辅助模块）

这一阶段的增长大多在 `handler.py` 内部，加几个辅助模块。仍扁平：

    multi_p_g/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── handler.py             随 RPC 数量增长
    ├── pb/
    │   ├── __init__.py
    │   ├── service_pb2.py
    │   └── service_pb2_grpc.py
    ├── auth.py                gRPC interceptor 做 auth metadata
    └── metrics.py             prometheus / statsd

### Stage 2 — 多服务 / 多 handler / interceptor

`handler.py` 超过 ~300 行，或者你有多个 gRPC 服务时，开始拆：

    project_root/
    ├── proto/                 多个 .proto 文件都住在这里
    │   ├── service.proto
    │   ├── billing.proto
    │   └── common.proto       共享类型，被其它 .proto 引用
    └── multi_p_g/
        ├── __init__.py
        ├── main.py
        ├── config.py
        ├── core.py
        ├── pb/                proto/ 下每个文件都生成对应的 stub
        │   ├── __init__.py
        │   ├── service_pb2.py / service_pb2_grpc.py
        │   ├── billing_pb2.py / billing_pb2_grpc.py
        │   └── common_pb2.py
        ├── handlers/          每个 gRPC 服务一个文件
        │   ├── __init__.py
        │   ├── ping.py
        │   └── billing.py
        ├── interceptors/      authn、authz、logging、tracing
        │   ├── __init__.py
        │   └── auth.py
        ├── services/          跨 handler 共享的业务逻辑
        │   ├── __init__.py
        │   └── billing.py
        └── clients/           出站 API / DB 客户端
            ├── __init__.py
            └── stripe.py

`gen.sh` 已经会遍历 `proto/*.proto`，加新 IDL 就是丢一个文件进 `proto/`
然后重跑。

### Stage 3 — 大服务（20+ 模块）

子包内部再长出子包，加上与包平级的兄弟目录：

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── gen.sh
    ├── proto/                IDL 源码（一个或多个文件）
    │   ├── service.proto
    │   ├── billing.proto
    │   └── common.proto
    ├── multi_p_g/            Python 包（pb/ 在内部）
    ├── tests/                pytest 测试树，结构镜像 multi_p_g/
    │   ├── unit/
    │   └── integration/
    ├── scripts/              一次性运维脚本（不是包）
    ├── docs/                 架构文档、runbook
    └── ops/                  Dockerfile、k8s manifest、terraform

### 跨阶段不变的规则

1. `main.py`、`config.py`、`core.py` 永远在包根。
2. `control.sh`、`pyproject.toml`、`settings.yaml`、`gen.sh` 永远在
   项目根。
3. `proto/` 永远在项目根 —— IDL 是项目级的产物，不是包级的。
4. `multi_p_g/pb/` 永远在 Python 包内 —— 生成的 stub 必须能作为
   `multi_p_g.pb.<x>` 被 import。
5. Python 包是项目根下唯一带 `__init__.py` 的目录。

## gRPC 相关注意事项

- **同步 vs 异步 servicer。** 模板用同步 handler（`def Ping(self, ...)`）。
  每个 worker 用 `ThreadPoolExecutor` 服务并发 RPC。要纯异步就用
  `grpc.aio.server` 和 `async def` handler —— 但要注意异步 gRPC 是一条
  独立代码路径，不要在同一个 server 里混用同步和异步。
- **状态码是错误信号的方式。** 不要往客户端抛 Python 异常 —— 它们会变成
  `INTERNAL`。预期的失败用 `context.abort(grpc.StatusCode.NOT_FOUND, "user not found")`。
  完整状态码：`OK`、`CANCELLED`、`UNKNOWN`、`INVALID_ARGUMENT`、
  `DEADLINE_EXCEEDED`、`NOT_FOUND`、`ALREADY_EXISTS`、`PERMISSION_DENIED`、
  `RESOURCE_EXHAUSTED`、`FAILED_PRECONDITION`、`ABORTED`、`OUT_OF_RANGE`、
  `UNIMPLEMENTED`、`INTERNAL`、`UNAVAILABLE`、`DATA_LOSS`、`UNAUTHENTICATED`。
- **Deadline 由客户端驱动。** 客户端设 deadline；服务端通过
  `context.time_remaining()` 拿到。长跑 handler 应该周期性检查并在超时
  时 `abort` 报 `DEADLINE_EXCEEDED`。
- **Streaming RPC。** `rpc StreamLogs(LogRequest) returns (stream LogEntry);`
  生成的 handler 要 yield entry：
  ```python
  def StreamLogs(self, request, context):
      for entry in tail_log():
          if not context.is_active(): return
          yield entry
  ```
- **给 grpcurl 加 reflection。** `pip install grpcio-reflection`，然后在
  `_worker_main` bind 之后：
  ```python
  from grpc_reflection.v1alpha import reflection
  from multi_p_g.pb import service_pb2
  reflection.enable_server_reflection(
      [service_pb2.DESCRIPTOR.services_by_name["PingService"].full_name,
       reflection.SERVICE_NAME], server)
  ```
  之后 `grpcurl -plaintext 127.0.0.1:50051 list` 不需要客户端有 .proto
  也能用。
- **TLS。** 把 `add_insecure_port` 换成 `add_secure_port` 配合
  `grpc.ssl_server_credentials([(key_pem, cert_pem)])`。要 mTLS 加上
  `root_certificates_pem` 并设 `require_client_auth=True`。
- **worker 之间不共享状态。** 每个 worker 是独立进程。要跨 worker 共享
  状态（缓存、计数器、限流 token）必须走 Redis 或别的外部存储 ——
  跟 `multi_p` 同样的约束。
- **`grpc.so_reuseport=1`** 是 gRPC 把 socket option 传给内部 listener
  的方式。如果反过来要保证只有一个 listener（比如测试并发上限），用
  `("grpc.so_reuseport", 0)` 并跑单 worker。
- **不要给 worker 加 `daemon=True`。** 模板默认 `daemon=False`，确保
  关停时真的等 worker drain 完。daemon worker 会在 RPC 中途被强杀。
- **长跑 RPC 要查 `context.is_active()`。** 客户端 cancel 时，服务端
  通过 `context.is_active()` 返回 False 知道。长循环代码应该周期性检查
  并提前退出；不然 worker 会一直忙在一个幻影 RPC 上。
