# multi_p_t

[English](README.md)

多进程 **Apache Thrift** 服务模板 —— N 个 worker 进程通过 `SO_REUSEPORT`
共享端口，跨进程日志、显式 stop flag（Apache Thrift Python 自带的
`TServer.stop()` 不工作），以及一个能跑的 `Ping`/`Echo` 示例。

## 适用场景

下列情况优先选 `multi_p_t`：

- **要对接现有 Thrift 生态** —— 跨语言系统中已经把 Thrift 作为合同：
  HBase Thrift gateway、Hive、Cassandra（老版本）、Scribe、Facebook /
  Meta 风格的 RPC、公司里早于 gRPC 的内部老服务。
- **需要跨语言强保证的 wire 兼容性。** Apache Thrift 是 Java / C++ /
  Python / Go / PHP / Ruby 之间十多年来事实上的协议标准，跨语言客户端
  质量平齐。
- **接受带 `__slots__` 风格的 Python 生成代码作为 API 表面**（参见下面
  "为什么选 Apache 而不是 thriftpy2"）。

## 不适用场景

| 需求 | 选 |
|---|---|
| 2026 年新服务、没有 Thrift 约束 | `multi_p_g`（gRPC）—— 现代等价物 |
| 公开 API，要浏览器 / curl 友好 debug | `multi_p_h`（HTTP） |
| 开发机 / CI 没装 thrift CLI | `multi_p_g` —— gRPC 的 `grpcio-tools` 是 `pip install` 就能装 |
| 长跑后台 worker，没入站流量 | `multi_p` |
| 生产/消费队列 + 重试 | `multi_t_q` |
| 单一逻辑循环、不需要并发 | `simple` |

**新服务 2026 年几乎应该选 gRPC（`multi_p_g`）** —— 同样的协议驱动
设计、同样的多语言故事，但工具链（`grpcurl`、Envoy/Istio 集成、
`grpcio` 安装）平滑得多。这个模板只在外部约束强制你必须用 Thrift 时
才合适。

## 模板提供了什么

- **`pyproject.toml`** 运行时依赖 `thrift` + `dynaconf`；**`thrift` CLI
  编译器是单独的系统依赖**（Homebrew / apt）。
- **`proto/service.thrift`** —— 示例 IDL，定义 `PingService`（Ping +
  Echo）。修改后跑 `./gen.sh` 重新生成。
- **`gen.sh`** —— 包装 `thrift --gen py:slots`，把 `proto/*.thrift`
  全部重新生成到 `multi_p_t/pb/`，删除 Thrift 顺手生成的 `*-remote`
  CLI 客户端脚本，并 patch import 让其相对包内解析。
- **预生成的 `multi_p_t/pb/tsvc/*.py` 已提交到模板** —— `pip install -e .`
  装完立即得到一个能跑的 server。
- **多进程 server** 配 `SO_REUSEPORT` —— N 个 worker 全部 bind 同一端口，
  内核帮忙在 worker 之间分配连接。
- **`_StoppableThriftServer`** 子类 —— Apache Thrift Python 自带的
  `TThreadedServer` 把 listen socket 关闭抛出的 `OSError` 一律吞掉
  继续 accept，所以我们自己维护一个 `_stopped` flag、显式跳出 accept
  循环。
- **跨进程日志** 走 `QueueHandler` + `QueueListener`：单一日志文件、
  不会 interleave。
- **有界关停** —— 父进程给每个 worker `grace + 5s` 退出窗口，超时
  `terminate()` 再 `kill()` 兜底。

## 装 Apache Thrift CLI

`thrift` Python pip 包给你的是运行时；**codegen 需要单独装 `thrift`
编译器二进制**：

    # macOS
    brew install thrift

    # Debian / Ubuntu
    sudo apt install thrift-compiler

    # 验证
    thrift --version

如果跳过这一步，`./gen.sh` 会报清晰的错误；模板仍然能装能跑，因为
`multi_p_t/pb/` 已经预先生成提交了。

## 装 Python 依赖

    pip install -e .

## 运行

    ./control.sh start
    ./control.sh status
    ./control.sh stop

或直接：

    python -m multi_p_t.main

Python 客户端调用：

```python
from thrift.transport import TSocket, TTransport
from thrift.protocol import TBinaryProtocol
from multi_p_t.pb.tsvc import PingService

t = TSocket.TSocket('127.0.0.1', 9090)
t = TTransport.TBufferedTransport(t)
prot = TBinaryProtocol.TBinaryProtocol(t)
client = PingService.Client(prot)
t.open()
print(client.Ping())                  # "pong"
print(client.Echo('hello'))           # "hello"
t.close()
```

## 配置

`settings.yaml` 里调：

    service:
      host: 127.0.0.1            # 改 0.0.0.0 才对外暴露
      port: 9090
      workers: 4                 # OS 进程数
      threads_per_worker: 32     # 每个 worker 内最大并发连接数
      shutdown_grace: 10         # 在途 RPC 完成的秒数预算

## 业务代码放在哪

**`multi_p_t/handler.py`** —— 替换 `Ping()` / `Echo()` 并按你的
`.thrift` IDL 加新方法。方法签名必须**完全匹配**：参数名、类型、返回
类型。

**`multi_p_t/core.py`** —— worker 池编排（`Multi_p_tService`）、
worker 入口（`_worker_main`）、以及两个为绕开 Apache Thrift Python
缺陷而写的子类（`_ReusePortServerSocket`、`_StoppableThriftServer`）。
这里不放业务逻辑；这是底盘代码。

**`multi_p_t/main.py`** —— 入口 + `QueueListener` setup。这里通常只
改 lifecycle hook（DB pool 开/关之类的），围绕 `Multi_p_tService.run()`
加。

改 `.thrift` 后的工作流：

1. 编辑 `proto/service.thrift`（或者新增 `proto/*.thrift`）
2. `./gen.sh`（重新生成 `multi_p_t/pb/`）
3. 更新 `handler.py` 实现新增的 RPC

## Thrift 命名空间 vs Python 包名

示例 `proto/service.thrift` 用的是 **`namespace py tsvc`**，故意是
**非 Thrift 关键字** 也 **非 Python 包名** 的中性名字。两个原因：

1. **`service` 是 Thrift IDL 的保留关键字** —— 用作 namespace 会让
   parser 直接报语法错误。
2. **`pygen.sh` 在生成项目时把字面量 `multi_p_t` 全文替换。**
   Apache Thrift 的生成代码是文本可替换的（不像 protobuf 有二进制
   descriptor），但 namespace 在 .thrift 文件、`multi_p_t/pb/` 下的
   目录布局、import 语句之间还需要保持一致。把 Thrift namespace 跟
   Python 包名解耦，rename 之后整个链路稳定。

替换成你自己的 IDL 时，Thrift namespace 名字随便起（避开 Thrift 关键
字）。Python import 路径（`multi_p_t.pb.tsvc`）跟着 namespace 字符串
走，改了名字记得同步改 gen.sh 的 import-patch 步骤。

## 为什么选 Apache 而不是 thriftpy2

Python 生态还有 `thriftpy2` —— 纯 Python 实现，运行时加载 `.thrift`
文件、跳过 codegen。**追求"最低摩擦"模板的话 thriftpy2 更好；这个
模板选了 Apache。** 理由：

- Apache Thrift 是**协议参考实现**。跨语言 wire-format 边角问题
  （递归类型、某些 compact protocol 边界、JSON protocol 细节）按
  规范本身的定义就匹配。
- **静态类型工具。** 生成的 `_pb.py` 是真实的 Python 模块、有 class
  定义，所以 mypy / pyright / IDE autocomplete 都工作。`thriftpy2`
  动态加载的 class 静态检查器看不到。
- **生产规模化验证。** Apache Thrift Python 在 Facebook、Twitter
  （历史上）等大厂跑过多年百亿级 RPS。`thriftpy2` 也很稳但规模
  小一些。

代价：Apache Thrift Python **近年维护不活跃**（项目精力在 C++ /
Java 上）；codegen 步骤需要系统包安装的 CLI；生成代码风格陈旧（没
类型注解、Python 2 时代的写法）。

如果这些代价比好处更刺痛，换 `thriftpy2` —— .thrift IDL 不用动，
只需要改 `gen.sh` 和几个 import。

## 为什么用 SO_REUSEPORT 和自定义 server socket

`SO_REUSEPORT` 是让多进程能 bind 同一端口的 socket option；内核对
入站连接做 hash 分发。Apache Thrift 的 stock `TServerSocket` 只设
了 `SO_REUSEADDR`，多进程绑同一端口会 `EADDRINUSE` 失败。

`_ReusePortServerSocket`（在 `core.py` 里）是个小子类，重写
`TServerSocket.listen()` 在 `bind()` 之前 set `SO_REUSEPORT`。代码
~15 行，跟 gRPC 的 `so_reuseport` option 内部做的事一样，在 Linux
和 macOS 都能用（Windows 没 `SO_REUSEPORT`）。

**生产多机部署**通常前面还要加 service mesh（Envoy / Linkerd）或者
专用的 Thrift LB 做跨主机的负载均衡、retry、observability。
SO_REUSEPORT 解决主机内的故事。

## 为什么需要 `_StoppableThriftServer` 和显式 stop flag

Apache Thrift 的 `TThreadedServer.serve()` 长这样：

```python
def serve(self):
    self.serverTransport.listen()
    while True:
        try:
            client = self.serverTransport.accept()
            ...
        except KeyboardInterrupt:
            raise
        except Exception:
            logging.exception("Error occurred ...")
```

笼统的 `except Exception` 把关闭 listen socket 时 `accept()` 抛出的
`OSError` 也吃掉了 —— `serve()` 会一直再 accept 下去。没有干净的
stop 方式，除非 monkey-patch 或子类化。

`_StoppableThriftServer`（在 `core.py` 里）加了：
- `_stopped` flag。
- 重写 `serve()`，flag 被 set 后跳出 loop。
- `stop()` 方法，flip flag。

`_worker_main` 里的关停顺序：

1. `server.stop()` → flag 立起来，`accept()` 抛异常时被当成 graceful
   退出。
2. 关 listen socket → `accept()` 立即解阻塞，抛 OSError。
3. `serve_thread.join(timeout=grace)` → 线程干净退出。

没有这个 flag 的话，第 2 步只会触发"吞-继续"循环，worker 会一直挂到
被 terminate。

## 项目变大时的目录结构演进

模板出厂状态是 **Stage 0**。按下面顺序演进，不要预先建空目录、不要
跳级。

### Stage 0 — 初始（≤ 5 个模块）

注意：跟其他模板不同 —— 这个模板出厂就是 5 个模块 + 一个独立的
`proto/` 目录，因为：
- handler 跟业务逻辑天然分开。
- 生成的 stub 需要自己的子包（Apache Thrift 每个 .thrift namespace
  生成 3 个文件）。
- IDL 源是项目级的，不是包级的。

    multi_p_t/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── gen.sh
    ├── proto/
    │   └── service.thrift     ← 你编辑这里
    └── multi_p_t/
        ├── __init__.py
        ├── main.py            入口 + QueueListener
        ├── config.py          Dynaconf 加载
        ├── core.py            worker 池 + 自定义 server 子类
        ├── handler.py         Iface 实现
        └── pb/                ← 生成内容，已提交（不要手改）
            ├── __init__.py
            └── tsvc/
                ├── __init__.py
                ├── PingService.py
                ├── ttypes.py
                └── constants.py

### Stage 1 — 小服务（更多方法、辅助模块）

这一阶段的增长大多在 `handler.py` 内部：

    multi_p_t/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── handler.py             随 RPC 数量增长
    ├── pb/
    │   └── tsvc/...
    ├── auth.py                解析 Thrift header / metadata
    └── metrics.py             prometheus / statsd

### Stage 2 — 多服务 / 多 handler

`handler.py` 超过 ~300 行，或者有多个 Thrift 服务时，开始拆：

    project_root/
    ├── proto/
    │   ├── ping.thrift        每个服务一个 .thrift
    │   ├── billing.thrift
    │   └── common.thrift      共享 struct，被其它 .thrift include
    └── multi_p_t/
        ├── __init__.py
        ├── main.py
        ├── config.py
        ├── core.py
        ├── pb/
        │   ├── __init__.py
        │   ├── ping/...       为 ping.thrift 生成
        │   ├── billing/...    为 billing.thrift 生成
        │   └── common/...
        ├── handlers/          每个 Thrift 服务一个文件
        │   ├── __init__.py
        │   ├── ping.py
        │   └── billing.py
        ├── services/          共用业务逻辑
        │   ├── __init__.py
        │   └── billing.py
        └── clients/           出站 API / DB 客户端

`gen.sh` 已经会遍历 `proto/*.thrift`，加新 IDL 就是丢一个文件进
`proto/` 然后重跑。`core.py` 需要注册多个 processor —— Apache Thrift
有 `TMultiplexedProcessor` 可以在一个端口上服务多个服务。

### Stage 3 — 大服务（20+ 模块）

子包内部再长出子包，加上与包平级的兄弟目录：

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── gen.sh
    ├── proto/
    ├── multi_p_t/
    ├── tests/                 pytest 测试树
    │   ├── unit/
    │   └── integration/
    ├── scripts/               一次性运维脚本（不是包）
    ├── docs/                  架构文档、runbook
    └── ops/                   Dockerfile、k8s manifest、terraform

### 跨阶段不变的规则

1. `main.py`、`config.py`、`core.py` 永远在包根。
2. `control.sh`、`pyproject.toml`、`settings.yaml`、`gen.sh` 永远
   在项目根。
3. `proto/` 永远在项目根 —— IDL 是项目级的产物。
4. `multi_p_t/pb/` 永远在 Python 包内 —— 生成的 stub 必须能作为
   `multi_p_t.pb.<x>` 被 import。
5. Python 包是项目根下唯一带 `__init__.py` 的目录。

## Apache Thrift 相关注意事项

- **`thrift` CLI 版本漂移是真实问题。** 一个版本生成的代码可能跟
  另一个版本的 runtime 不兼容。把 CLI 版本固定记录到 Dockerfile /
  README / Makefile 之类可观察的地方，CI 里检
  `thrift --version && ./gen.sh && git diff --exit-code` 防漂移。
- **`thrift` Python 包陈旧。** 生成代码没类型注解；方法签名跟着 IDL
  但 mypy 看不到，除非你自己写 stub。生成的 function 是真实存在的，
  autocomplete 看得到，只是没 typing。
- **异步支持差。** Apache Thrift Python 有 Twisted 风味的 `TTwisted`
  和 `TNonblockingServer`，都过时了。要真正的 asyncio Thrift 看
  第三方库或者接受 thread-per-connection。
- **Thrift 异常会跨网络。** 在 .thrift IDL 里定义
  （`exception MyError { 1: string message }`）—— 它们以 typed error
  发回客户端。从 handler 里抛任意 Python 异常会被打包成通用的
  `TApplicationException`。
- **`TBufferedTransport` 两端都要用。** 模板默认用它。如果客户端
  和服务端用的 buffered 设置不一致会无声挂起。
- **worker 之间不共享状态。** 每个 worker 独立进程 —— 跟 `multi_p` /
  `multi_p_g` 同样的约束。要跨 worker 共享状态用 Redis 或 DB。
- **不要给 worker 池进程加 `daemon=True`。** 模板默认 `daemon=False`，
  确保关停时真的等 worker drain 完。但**每个 worker 内部的连接线程**
  是 `daemon=True` —— Apache Thrift Python 的 drain 故事不完美
  （在途 RPC 的线程在进程退出时可能被截断）。如果你的 RPC 是长跑
  类型，这一点要在 runbook 里写清楚。
- **长跑 RPC 需要自己的取消机制。** Apache Thrift 没有像 gRPC context
  那样的客户端 deadline 传播。客户端中途断开，server 不知道 ——
  会一直跑。在长 handler 里做 check point，必要时在 transport 层
  处理断开。
- **TLS** —— 用 `TSSLServerSocket` 替代普通的 `TServerSocket`，传
  `ssl_context`。如果同时要 SO_REUSEPORT + TLS，把
  `_ReusePortServerSocket` 改成继承自 `TSSLServerSocket`。
