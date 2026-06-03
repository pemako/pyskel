# multi_t

[English](README.md)

多线程 Python 3.12 服务模板 —— N 个相同的 worker 线程，由同一个停止
信号驱动。

## 适用场景

`multi_t` 是单进程跑 N 个 worker 线程，线程间共享内存、做同一类工作。
下列情况优先选它：

- **并发 I/O 扇出** —— N 个 poller 同时打不同的分片 / key / endpoint，
  整体吞吐重要但单次调用瓶颈在网络。
- **天然并行的 I/O 任务** —— 抓 URL、ping 主机、扫一批设备、对一群目标
  跑健康检查。
- **需要共享内存状态的后台 worker** —— 缓存、进程内 pub/sub、在途请求
  协调 —— worker 之间要读对方数据但又不想付 IPC 成本。
- **GIL 不构成瓶颈的负载** —— 几乎所有 I/O 密集的服务。系统调用期间
  GIL 是释放的，N 个线程能给你 N 倍 I/O 并发度。

判断标志：每个 worker 做**同一类工作**，并且全部响应同一个停止信号。

## 不适用场景

| 需求                       | 选                           |
| -------------------------- | ---------------------------- |
| 生产/消费式任务队列 + 重试 | `multi_t_q`                  |
| CPU 密集并行（GIL 是瓶颈） | `multi_p`（multiprocessing） |
| 单一逻辑循环、不需要并发   | `simple`                     |
| RPC / Thrift 服务端        | `multi_p_t`                  |
| HTTP 服务                  | 直接用框架（FastAPI、Flask） |

如果 worker 之间需要通过队列协作（一个生产、其他消费），或者单个任务
失败要重试 —— 那是 `multi_t_q` 的场景，不是 `multi_t`。`multi_t` 假设
所有 worker 是可互换的，工作量来源是隐式的（一个计数器、一个共享缓存、
一个外部队列各自独立拉取）。

如果你开始往代码里加 `multiprocessing` 原语，或者塞 `queue.Queue` 让
worker 互相协调，这是要换模板的信号，不要原地改造。

## 模板提供了什么

- `pyproject.toml`（PEP 621），Python 3.12+，唯一依赖：`dynaconf`。
- `settings.yaml` 同时承载服务配置和 `logging` dictConfig，日志格式里
  带 `%(threadName)s`，每行日志会显示 `worker-0`、`worker-1`。
- `control.sh` 跨平台 PID 文件式 start/stop/restart/status。
- Worker 用 `threading.Event` 而不是裸 bool —— `SIGTERM` 来时立即跳出
  每轮的 1 秒等待，不必等满。
- 有界关停 —— 主线程 join worker 时带 30 秒上限，超时未退出的线程会
  在日志里告警。

## 安装

    pip install -e .

## 运行

    ./control.sh start
    ./control.sh status
    ./control.sh stop

或直接：

    python -m multi_t.main

调整 worker 数量在 `settings.yaml`：

    service:
      workers: 4

## 业务代码放在哪

把 `multi_t/core.py` 里的 `Multi_tService._do_work()` 替换成你的实际
工作。这个方法每个 worker 每轮调用一次。每个 worker 的循环是：

1. 调 `_do_work()`（你的代码）。
2. 在共享的 `_stop` Event 上 `wait()` 至多 1 秒。
3. 重复，直到 `_stop` 被 set —— 立即退出。

不要把循环本身写到 `_do_work()` 里 —— 循环是 `_work_loop` 的职责，
你只放每一拍要做的事。

## 项目变大时的目录结构演进

模板出厂状态是 **Stage 0**。随着服务变大，按下面四个阶段顺序演进 ——
不要在 Stage 0 就预先建空目录"以备将来"，也不要跳级。

### Stage 0 — 初始（≤ 3 个模块）

生成器开箱即用的状态：

    multi_t/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── multi_t/
        ├── __init__.py
        ├── main.py        入口：argparse、日志、run()
        ├── config.py      Dynaconf 加载
        └── core.py        worker 池 + 每拍工作

**这一阶段的规则：** 新代码全部扁平地放在 `core.py` 旁边。先别拆子包，
5–8 个文件以下子包不划算。

### Stage 1 — 小服务（5–8 个模块，仍扁平）

加了几个辅助模块。仍扁平：

    multi_t/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── client.py
    ├── parser.py
    ├── retry.py
    └── metrics.py

**进入 Stage 2 的触发条件：** `ls multi_t/` 一屏放不下，_或者_ 出现两个
共同前缀的文件。

### Stage 2 — 按职责拆子包（8–20 个模块）

按职责拆，不是按类型拆。`main.py`、`config.py`、`core.py` 留顶层。

    multi_t/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── core.py
    ├── clients/         出站 API / DB 客户端
    ├── handlers/        入站事件分发
    ├── services/        业务逻辑
    └── models/          dataclass / ORM / pydantic

| 子包        | 装什么                           | 典型文件名                       |
| ----------- | -------------------------------- | -------------------------------- |
| `clients/`  | 出站网络调用的封装               | `github.py`、`redis.py`、`s3.py` |
| `handlers/` | 入站分发（每种事件一个文件）     | `webhook.py`、`cron.py`          |
| `services/` | 编排 clients + models 的业务逻辑 | `billing.py`、`auth.py`          |
| `models/`   | 数据形状 —— 不做 I/O、无副作用   | `user.py`、`order.py`            |
| `db/`       | 持久层，超出一个文件后单独拆     | `connection.py`、`queries.py`    |
| `utils/`    | 最后退路 —— 小、无状态的工具     | `time.py`、`text.py`             |

**关于 `utils/` 的警告：** 这个目录最容易变成杂物抽屉。一个工具如果只被
一个子包用，就放进那个子包；只有当它真的被 2+ 个子包共用了再挪到
`utils/`。

### Stage 3 — 大服务（20+ 模块）

子包内部再长出子包。包根本身不变；变的是深度，不是顶层广度。

    multi_t/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── core.py
    ├── clients/
    │   ├── __init__.py
    │   ├── github/        原 github.py，现在是子包
    │   │   ├── __init__.py
    │   │   ├── auth.py
    │   │   └── rate_limit.py
    │   └── slack.py
    ├── handlers/
    ├── services/
    └── db/

加上与包平级的兄弟目录：

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── multi_t/          包
    ├── tests/            pytest 测试树，结构镜像 multi_t/
    │   ├── unit/
    │   └── integration/
    ├── scripts/          一次性运维脚本（不是包）
    ├── docs/             架构文档、runbook
    └── ops/              Dockerfile、k8s manifest、terraform

`tests/` 是兄弟目录这样 `pip install` 不会把测试代码也装进去；`scripts/`
不带 `__init__.py` 因为里面是一次性工具，不应该被 import。

### 跨阶段不变的规则

下面三条从 Stage 0 到 Stage 3 一直成立：

1. `main.py`、`config.py`、`core.py` 永远在包根。
2. `control.sh`、`pyproject.toml`、`settings.yaml` 永远在项目根。
3. 项目根下只有一个带 `__init__.py` 的目录 —— 包目录。

## 多线程相关注意事项

- **GIL 仍然存在。** `multi_t` 给你的是并行 I/O，不是并行 CPU。如果
  发现某个 worker 在 100% 烧 Python（而不是等网络/磁盘），多线程救不
  了你 —— 换 `multi_p`。
- **共享状态需要锁。** 所有 worker 看到同一组 Python 对象。任何可变
  共享状态都要用 `threading.Lock` / `threading.RLock` 保护，或者用
  线程安全的原语（`queue.Queue`，部分操作下的 `collections.deque`）。
- **生产环境别用 `daemon=True`。** 模板里用的是 `daemon=False`，确保
  关停时真的等到 worker 退出。Daemon 线程会在主线程结束时被强杀 ——
  对可丢失的工作 OK，对在途写操作是灾难。
- **`_do_work` 里要捕异常。** 未捕的异常会让那个 worker 线程死掉，
  其他线程继续静默地以减少的并发度运行 —— 这是非常隐蔽的故障模式。
  能恢复的异常 log + 吞掉；不可恢复的，调 `self.stop()` 后再让它向上
  抛。
