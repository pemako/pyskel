# simple

[English](README.md)

最小化的单进程 Python 3.12 服务模板。

## 适用场景

`simple` 是单进程 + 单逻辑循环的服务。下列情况优先选它：

- **轮询任务** — 周期性醒来、做活、休眠。类 cron 调度器、队列轮询、S3/目录监听、心跳上报。
- **有状态的单租户守护进程** — 串行执行本身是诉求而不是限制：必须保序的写入器、leader-elected worker、单实例 ETL。
- **本身就串行的事件监听器** — Slack bot、负载均衡后的 webhook 接收器（按进程串行）、长轮询消费者。
- **以服务形式跑的内部 CLI** — 由 systemd / k8s 托管、失败重启、不需要进程内并发。

判断标志：吞吐量瓶颈在 *外部系统*（网络、磁盘、API 限流），而不在 Python 单线程本身。

## 不适用场景

下列情况换其他模板：

| 需求 | 选 |
|---|---|
| CPU 密集并行 | `multi_p`（multiprocessing）|
| 大量并发 I/O | `multi_t` 或 `multi_t_q`（threading + queue）|
| 带重试的生产/消费流水线 | `multi_t_q` |
| RPC / Thrift 服务端 | `multi_p_t` |
| HTTP 服务 | 直接用框架（FastAPI、Flask）—— 不要把 `simple` 改造成这个 |

如果你开始往 `core.py` 里塞 `ThreadPoolExecutor` 或 fork 子进程，这是要换模板的信号，不要原地改造。

## 模板提供了什么

- `pyproject.toml`（PEP 621），Python 3.12+，唯一依赖：`dynaconf`。
- `settings.yaml` 同时承载服务配置和标准库 `logging` 的 dictConfig（按天滚动、保留 7 天）。
- `control.sh` 跨平台 PID 文件式 start/stop/restart/status（用 `kill -0`，没有 `vmmap` / `/proc` 分支）。
- 干净的信号处理 —— `SIGTERM` / `SIGINT` 把 `running = False` 然后退出循环。

## 安装

    pip install -e .

## 运行

    ./control.sh start
    ./control.sh status
    ./control.sh stop

或直接：

    python -m simple.main

## 项目变大时的目录结构演进

模板出厂状态是 **Stage 0**。随着服务变大，按下面四个阶段顺序演进 ——
不要在 Stage 0 就预先建空目录"以备将来"，也不要跳级。

### Stage 0 — 初始（≤ 3 个模块）

生成器开箱即用的状态。三个文件，扁平，公共骨架：

    simple/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── simple/
        ├── __init__.py
        ├── main.py        入口：argparse、日志、run()
        ├── config.py      Dynaconf 加载
        └── core.py        服务循环

**这一阶段的规则：** 新代码全部扁平地放在 `core.py` 旁边。先别拆子包，
5–8 个文件以下，子包带来的目录跳转成本不划算。

### Stage 1 — 小服务（5–8 个模块，仍扁平）

加了几个辅助模块。仍然扁平，仍然 `ls` 一屏看完：

    simple/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── client.py        HTTP 客户端封装
    ├── parser.py        领域相关解析
    ├── retry.py         重试装饰器
    └── metrics.py       prometheus / statsd

**进入 Stage 2 的触发条件：** `ls simple/` 一屏放不下了，*或者* 出现两个
共同前缀的文件（`order_handler.py`、`payment_handler.py` —— 它们想要
一个 `handlers/` 子包了）。

### Stage 2 — 按职责拆子包（8–20 个模块）

把相关模块归到**按职责命名**的子包里，不要按类型拆。`main.py`、
`config.py`、`core.py` 留在顶层 —— 它们是公共骨架，永远只埋一层深。

    simple/
    ├── __init__.py
    ├── main.py            入口 — 留在顶层
    ├── config.py          留在顶层
    ├── core.py            服务循环 — 留在顶层
    ├── clients/           ← 外部 API / 数据库客户端
    │   ├── __init__.py
    │   ├── github.py
    │   └── slack.py
    ├── handlers/          ← 入站事件 / 请求分发
    │   ├── __init__.py
    │   ├── webhook.py
    │   └── cron.py
    ├── services/          ← 业务逻辑
    │   ├── __init__.py
    │   ├── billing.py
    │   └── notification.py
    └── models/            ← dataclass / ORM / pydantic
        ├── __init__.py
        ├── user.py
        └── invoice.py

**什么放哪：**

| 子包 | 装什么 | 典型文件名 |
|---|---|---|
| `clients/` | *出站* 网络调用的封装 | `github.py`、`redis.py`、`s3.py` |
| `handlers/` | *入站* 分发（每种事件一个文件） | `webhook.py`、`cron.py`、`signal.py` |
| `services/` | 编排 clients + models 的业务逻辑 | `billing.py`、`auth.py` |
| `models/` | 数据形状 —— 不做 I/O、无副作用 | `user.py`、`order.py` |
| `db/` | 持久层，超出一个文件后单独拆 | `connection.py`、`queries.py` |
| `utils/` | 最后退路 —— 小、无状态、与框架无关的工具 | `time.py`、`text.py` |

**关于 `utils/` 的警告：** 这个目录最容易变成杂物抽屉。一个工具如果只被
一个子包用，就放进那个子包；只有当它真的被 2+ 个子包共用了，再挪到
`utils/`。

### Stage 3 — 大服务（20+ 模块）

子包内部再长出子包。包根本身不变 —— 变的是深度，不是顶层广度。

    simple/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── core.py
    ├── clients/
    │   ├── __init__.py
    │   ├── github/        ← 原 github.py，现在是子包
    │   │   ├── __init__.py
    │   │   ├── auth.py
    │   │   └── rate_limit.py
    │   └── slack.py
    ├── handlers/
    ├── services/
    │   ├── billing/
    │   │   ├── __init__.py
    │   │   ├── invoice.py
    │   │   └── refund.py
    │   └── notification.py
    ├── models/
    └── db/
        ├── __init__.py
        ├── connection.py
        ├── migrations/
        └── queries/

**这一阶段也会出现一组与包平级的顶层目录：**

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── simple/            ← 包
    ├── tests/             ← pytest 测试树，结构镜像 simple/
    │   ├── unit/
    │   └── integration/
    ├── scripts/           ← 一次性运维脚本（不是包）
    │   ├── backfill_users.py
    │   └── dump_db.sh
    ├── docs/              ← 架构文档、runbook
    └── ops/               ← Dockerfile、k8s manifest、terraform

**为什么 `tests/` 是兄弟目录而不是 `simple/tests/`：** `pip install` 不会
把测试代码也装进 site-packages，并且 `pytest tests/` 是一条干净无歧义
的命令。

**为什么 `scripts/` 不带 `__init__.py`：** 这些是一次性工具，不是可导入包
的一部分。被产品化的工具（长期维护的子命令，比如 `simple backfill`）
应该放进 `simple/cli/`，通过 `pyproject.toml` 的 `[project.scripts]` 暴露。

### 跨阶段不变的规则

下面三条从 Stage 0 到 Stage 3 一直成立：

1. `main.py`、`config.py`、`core.py` 永远在包根。它们是承重骨架，不要埋。
2. `control.sh`、`pyproject.toml`、`settings.yaml` 永远在项目根。
3. 项目根下只有一个带 `__init__.py` 的目录 —— 包目录（`simple` 在生成
   后会被替换成用户的项目名）。

如果某次重构忍不住想破其中一条，多半意味着你在让项目对新加入的人更难
读懂。
