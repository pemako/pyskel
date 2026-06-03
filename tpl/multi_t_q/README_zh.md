# multi_t_q

[English](README.md)

多线程**生产者 / 消费者**任务流水线，带有界队列、失败重试、以及
**关停 → 重启续跑**的持久化语义 —— 关停时残留任务被序列化到磁盘，下次
启动重新载入并继续处理。

## 适用场景

`multi_t_q` 解决"一个工作来源 + N 个 worker 扇出处理"的形状。下列情况
优先选它：

- **拉取-then-处理流水线** —— 一个 producer 从外部源拉（数据库、S3、
  Redis 队列、文件 watcher），N 个 worker 并行做单条处理。
- **会失败要重试的工作** —— 内置重试，可配重试次数和间隔；最终失败的
  任务进失败队列保留下来供检视，不是默默丢弃。
- **关停后能"接着干"的流水线** —— 关停时把 task queue + failed list
  pickle 到 todo 文件；下次启动先 load 再开始产新任务。
- **I/O 密集的并行处理，单一入口** —— 抓 URL、发通知、索引文档。

判断标志：**一个 producer，多个 worker，有界缓冲，重试 + 持久化**。

## 不适用场景

| 需求 | 选 |
|---|---|
| N 个独立 worker，无共享队列 | `multi_t` |
| CPU 密集 | `multi_p`（multiprocessing）|
| 单一逻辑循环、不需要并行 | `simple` |
| HTTP API | `multi_p_h`（FastAPI）|
| RPC API | `multi_p_g`（gRPC）|
| 跨机分布式队列 | 用 Redis / RabbitMQ / SQS 当队列，配 `multi_t` 或 `multi_p` 做消费者 |

`multi_t_q` 的队列是**进程内的** —— 通过 todo 文件能扛过单进程关停，
但**无法跨机扇出**。多机要把队列外置（Redis、SQS、NATS），每台机器跑
一个 `multi_t` 风格的消费者。

## 模板提供了什么

- `pyproject.toml`（PEP 621），Python 3.12+，唯一依赖：`dynaconf`。
- `tasks.py` —— `Task` dataclass + `TaskProcessor` 基类。覆写
  `TaskProcessor.process()` 写你真实的单条处理逻辑。
- `core.py` —— `Multi_t_qService` 编排：
  - **有界 `queue.Queue`**，producer 太快不会把进程 OOM 掉。
  - **单 producer 线程**（`_produce_loop`）调 `_produce_next()` ——
    覆写这一个方法接你真实的工作来源。
  - **N 个 worker 线程**（`_worker_loop`）消费 + 重试。
  - **重试-后-入失败队列**：超过 `retry_attempts` 次仍失败的任务进入
    独立的 failed queue。
  - **持久化重放**：关停时 drain 两个队列 pickle 到 `data/todo.pickle`；
    下次启动 load 进来续跑。
- **有界关停** —— 主线程用 30 秒上限 join 所有 worker + producer。
- **响应信号** —— `SIGTERM` / `SIGINT` 干净停 producer + worker，并触发
  关停前持久化。

## 安装

    pip install -e .

## 运行

    ./control.sh start
    ./control.sh status
    ./control.sh stop

或直接：

    python -m multi_t_q.main

## 配置

`settings.yaml` 里调：

    service:
      workers: 4
      queue_max: 1000        # 有界；满时 producer 反压
      retry_attempts: 3      # 临时失败重试这么多次
      retry_interval: 1      # 重试间隔（秒）
      todo_file: data/todo.pickle

## 业务代码放在哪

两个扩展点，按你改的频率从高到低：

1. **`tasks.TaskProcessor.process(task)`** —— 替换方法体。返回 `True`
   表示成功，`False` 表示临时失败（会重试），抛异常则被捕获、按重试
   计算。
2. **`Multi_t_qService._produce_next()`**（在 `core.py` 里）—— 替换成
   你真实的工作来源。默认每秒生成一个合成任务，让模板出厂就能跑。
   真实实现可能：
   - 拉外部队列（Redis BLPOP、SQS receive_message）
   - 监听目录（inotify、轮询 stat）
   - 读文件或流
   - 排空一个数据库查询

   暂时没活干就返回 `None` —— producer 会 sleep 1s 再问。**不要在
   `_produce_next` 里阻塞太久** —— 关停信号没法叫醒你。

`Task` dataclass 需要更多字段就改 `tasks.py`，但要保持 pickle 友好，
否则 todo 重放会坏掉。

## 重试语义

任务失败 = `process()` 返回 `False` 或抛异常。worker 行为：

1. 把 `task.attempts` +1。
2. 若 `attempts > retry_attempts`，把任务推到 failed queue 不再重试。
   （failed 任务在关停时一起持久化）
3. 否则 sleep `retry_interval`（关停时立即可被打断），再重试。

这是**至少一次**投递：worker 在 `process()` 中途崩溃会丢那一条任务
（不会自动重试）。要做到"跨 worker 崩溃也至少一次"，需要先在外部
队列把任务标记为"in flight"再处理 —— 这是 SQS / Redis with ack /
RabbitMQ 提供的语义。`multi_t_q` 的进程内队列做不到这个。

## 持久化语义

干净关停（SIGTERM / SIGINT）时，service 做：

1. 停 producer（不再有新任务进队列）。
2. join 所有 worker 线程，带超时上限。
3. drain `task_queue`（在途）和 `failed_queue`（已放弃）两个队列到一个
   bundle。
4. pickle bundle 到 `data/todo.pickle`。

下次启动 `_load_todo()` 读 bundle：

- `pending` 任务重新进 `task_queue`，worker 拿到就处理。
- `failed` 任务直接进 `failed_queue`（**不重试**）—— 留给运维通过
  `data/todo.pickle` 内容判断。

todo 文件**加载成功后就被删掉** —— 避免下次干净关停前崩溃导致任务被
重放两次。

如果你的处理是**非幂等的**（转账、发消息），**不能**靠这个机制保证
crash safety —— 用外部队列 + 显式 ack 才行。

## 项目变大时的目录结构演进

模板出厂状态是 **Stage 0**。按下面顺序演进，不要预先建空目录"以备
将来"，也不要跳级。

### Stage 0 — 初始（≤ 4 个模块）

注意：跟其他模板的"≤ 3 个模块"不同 —— 这个模板出厂就 4 个，因为
task/processor 的关注点跟编排器天然分开。

    multi_t_q/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── multi_t_q/
        ├── __init__.py
        ├── main.py        入口
        ├── config.py      Dynaconf 加载
        ├── core.py        Multi_t_qService —— worker 池、队列、持久化
        └── tasks.py       Task dataclass + TaskProcessor

### Stage 1 — 小服务（5–8 个模块，仍扁平）

加了几个辅助模块。仍扁平：

    multi_t_q/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── tasks.py
    ├── client.py            外部 API 客户端
    └── metrics.py           per-task 计数器

### Stage 2 — 多种任务类型

有 2+ 种任务（不同 processor、不同 payload）时，把 `tasks.py` 拆成子包：

    multi_t_q/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    └── tasks/
        ├── __init__.py      re-export Task / TaskProcessor 保持向后兼容
        ├── base.py          共用 Task dataclass + 基类 TaskProcessor
        ├── url_fetch.py     UrlFetchTask + UrlFetchProcessor
        └── notify.py        NotifyTask + NotifyProcessor

`core.py` 里的编排器通常按 task 类型挑 processor —— 一个 router 方法，
或者一个 `type(task)` → processor 的 dict。

### Stage 3 — 大流水线（20+ 模块）

子包内部再长出子包，加上与包平级的兄弟目录：

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── multi_t_q/            包
    ├── tests/                pytest 测试树
    │   ├── unit/
    │   └── integration/
    ├── scripts/              一次性运维（backfill、replay）
    ├── docs/                 runbook、架构
    └── ops/                  Dockerfile、k8s manifest

### 跨阶段不变的规则

1. `main.py`、`config.py`、`core.py` 永远在包根。
2. `control.sh`、`pyproject.toml`、`settings.yaml` 永远在项目根。
3. Python 包是项目根下唯一带 `__init__.py` 的目录。
4. `data/todo.pickle` 在项目根 `data/` 下，不在包内 —— 这是运行时状态，
   不是代码。

## producer/consumer 相关注意事项

- **默认 producer 节奏（1 任务/秒）只是 demo。** 你真实的 `_produce_next`
  应该在有活时尽快返回。返 None 让 producer sleep 1s 再问 —— 这就是
  轮询节拍。
- **不要在 `_produce_next` 里 sleep。** 返 None 才是 graceful 的做法；
  sleep 会让线程错过关停信号。
- **producer 是单线程的。** 如果 producer 是瓶颈（CPU-heavy 解析，或
  对外限流），把 `_produce_next` 写得便宜，重活放进
  `TaskProcessor.process` 里 —— 那才是并行度真正存在的地方。或者
  覆写 `run()` 起多个 producer 线程，但要自己设计队列竞争。
- **`task_queue` 有界，失败队列无界。** 持续失败而无法被重试解决的
  任务会让 failed queue 无限长，直到关停。在 processor 里看到这种
  情况要尽快 fail（抛特定异常），让运维介入。
- **`task.attempts` 在进程内重试间累积，跨重启不重置。** 也就是说
  pickle 在 `data/todo.pickle` 里的任务带着它现在的尝试次数 ——
  worker 崩溃-重启循环不会偷偷把你的重试预算耗光。如果你**想**在
  resume 时重置尝试次数，在 `_load_todo` 里把 `task.attempts` 清零。
- **持久化用的是 pickle。** 优点：任何 Python 对象都能完整保存。
  缺点：schema 变化会让旧 pickle 加载失败；pickle 加载不可信数据
  不安全。生产长期持久化考虑换 JSON（Task 字段限于 JSON 可序列化
  类型）或 SQLite（提供事务和 crash safety）。`_dump_todo` /
  `_load_todo` 的形状很小，替换起来直接。
- **`task_done()` 在重试耗尽后也会被调用。** `queue.Queue` 的任务计数
  按 `get()` 一次减一，不管 process 成不成功。我们没有调 `join()`，
  这只在你加监控看队列时会有影响。
- **进程内的，不是分布式的。** 这个模板就一个进程。要横向扩缩必须
  把队列外置。**不要**让多个 `multi_t_q` 实例指同一个
  `data/todo.pickle` —— 它们会在文件上 race，pickle 协议不带锁。
