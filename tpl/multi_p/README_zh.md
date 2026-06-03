# multi_p

[English](README.md)

多进程 Python 3.12 服务模板 —— N 个 worker 进程由共享停止事件驱动，跨进程
日志通过 `QueueListener` 汇入单一文件。

## 适用场景

`multi_p` 是一个父进程 fan out 出 N 个子进程。下列情况优先选它：

- **CPU 密集并行** —— 在单核上跑满 100% Python 计算的任务。GIL 让多线程
  在这里完全没用，多进程才能给你真正的并行度。
- **碰到线程不安全的原生库** —— 某些 C 扩展、BLAS 实现、或者持有全局
  状态的库可以安全地跨进程复制，但跨线程会出错。
- **一条坏输入不能拖死全部 worker** —— 一个 worker 进程可以 segfault、
  OOM、命中 `assert` 然后死掉，不会带走其他 worker。同进程内的线程会共享
  命运。
- **内存局部性重要** —— 每个子进程有自己的堆。没有 false sharing，没有
  对象 refcount 的 GIL 争用（高分配率工作负载下这一点尤其明显）。

判断标志：worker 是 CPU-heavy 的、需要进程隔离、或者两者都要。

## 不适用场景

| 需求 | 选 |
|---|---|
| I/O 密集（HTTP、DB 调用、文件 I/O） | `multi_t` —— 便宜得多 |
| 生产/消费 + 重试 | `multi_t_q` |
| 单一逻辑循环、不需要并发 | `simple` |
| RPC / Thrift 服务端 | `multi_p_t` |
| worker 之间有大量共享内存状态 | `multi_t`（或者重新设计） |
| 亚毫秒级 IPC 延迟 | 重新审视架构 |

如果 worker 大部分时间在等网络，`multi_p` 是过度设计 —— fork 开销大、IPC
有延迟、多线程本来就够用，多进程拿不到任何额外并行度。

如果 worker 之间需要持续读写对方的数据，多进程会让你很痛苦：每个共享
结构都要走 `Manager()`（代理 IPC）或者共享内存（`multiprocessing.shared_memory`）。
多线程在这种场景简单 100 倍。

## 模板提供了什么

- `pyproject.toml`（PEP 621），Python 3.12+，唯一依赖：`dynaconf`。
- **跨进程日志走 `QueueHandler` + `QueueListener`** —— 每个子进程把日志
  记录推到共享 `mp.Queue`，父进程开一个后台线程把队列里的记录排到配置
  好的 file/console handler。单一日志文件、不会 interleave、没有文件
  描述符争抢。
- `mp.Event` 做关停信号 —— 父进程收到 SIGTERM 就 set，子进程通过
  `wait(timeout=1.0)` 轮询，set 后立即退出。
- **有界关停** —— 父给 worker 30 秒时间退出，超时 `terminate()`（等价
  SIGTERM），再超时 `kill()`（SIGKILL）兜底。每次升级都打日志。
- 每行日志都带 worker 名（`worker-0`、`worker-1`…）和 PID：
  `[pid=12345 worker-0]`。

## 安装

    pip install -e .

## 运行

    ./control.sh start
    ./control.sh status
    ./control.sh stop

或直接：

    python -m multi_p.main

调整 worker 数量在 `settings.yaml`：

    service:
      workers: 4

## 业务代码放在哪

替换 `multi_p/core.py` 里 `_do_work()` 的方法体。它每个 worker 每拍调
用一次：

```python
def _do_work(logger: logging.Logger) -> None:
    logger.info("running")
```

`_do_work` 和 `_worker_main` 是**模块级函数，不是方法**。`multiprocessing`
在 `spawn` 启动方式下（macOS / Windows 默认）需要 pickle worker 的
target，bound method 也能 pickle，但会把整个 `self` 对象图都带过去；
模块级函数更简单、更安全。

## 项目变大时的目录结构演进

模板出厂状态是 **Stage 0**。随着服务变大，按下面四个阶段顺序演进 ——
不要在 Stage 0 就预先建空目录"以备将来"，也不要跳级。

### Stage 0 — 初始（≤ 3 个模块）

生成器开箱即用的状态：

    multi_p/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── multi_p/
        ├── __init__.py
        ├── main.py        入口：argparse、日志、queue listener
        ├── config.py      Dynaconf 加载
        └── core.py        worker 池 + 每拍工作

**这一阶段的规则：** 新代码全部扁平地放在 `core.py` 旁边。先别拆子包，
5–8 个文件以下子包不划算。

### Stage 1 — 小服务（5–8 个模块，仍扁平）

    multi_p/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── client.py
    ├── parser.py
    └── metrics.py

**进入 Stage 2 的触发条件：** `ls multi_p/` 一屏放不下，*或者* 出现两个
共同前缀的文件。

### Stage 2 — 按职责拆子包（8–20 个模块）

    multi_p/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── core.py
    ├── clients/         出站 API / DB 客户端
    ├── handlers/        入站事件分发
    ├── services/        业务逻辑
    └── models/          dataclass / ORM / pydantic

| 子包 | 装什么 | 典型文件名 |
|---|---|---|
| `clients/` | 出站网络调用的封装 | `github.py`、`redis.py`、`s3.py` |
| `handlers/` | 入站分发（每种事件一个文件） | `webhook.py`、`cron.py` |
| `services/` | 编排 clients + models 的业务逻辑 | `billing.py`、`auth.py` |
| `models/` | 数据形状 —— 不做 I/O、无副作用 | `user.py`、`order.py` |
| `db/` | 持久层，超出一个文件后单独拆 | `connection.py`、`queries.py` |
| `utils/` | 最后退路 —— 小、无状态的工具 | `time.py`、`text.py` |

**关于 `utils/` 的警告：** 这个目录最容易变成杂物抽屉。一个工具如果只被
一个子包用，就放进那个子包；只有当它真的被 2+ 个子包共用了再挪到
`utils/`。

### Stage 3 — 大服务（20+ 模块）

子包内部再长出子包。包根本身不变；变的是深度，不是顶层广度。

    multi_p/
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
    ├── multi_p/          包
    ├── tests/            pytest 测试树，结构镜像 multi_p/
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

## 多进程相关注意事项

- **`fork` vs `spawn` 启动方式。** Linux 默认 `fork`，macOS 和 Windows
  默认 `spawn`。两者行为差别很大：
    - **`fork`** —— 子进程继承父进程整个内存映像，包括打开的文件描述符、
      锁（状态未知）、线程（只有调用线程被复制）。启动便宜；如果父进程
      在 fork 前持有锁或起了线程，会很危险。**不要在起线程之后 fork。**
    - **`spawn`** —— 子进程从头跑 `python`，重新 import 模块，重新过
      `if __name__ == '__main__':` 守卫。启动慢；但因为没有继承状态所以
      安全。要求 worker target 和 args 能被 pickle（这就是为什么
      `_worker_main` 是模块级函数而不是方法）。
  本模板的子进程端 `_init_child_logging` 会显式重置 root logger，两种
  启动方式下都能正确工作。

- **子进程不能直接共享 Python 对象。** 每个进程有独立的堆。要共享状态
  必须用：
    - `multiprocessing.Value` / `Array` —— 单个原语，共享内存。
    - `multiprocessing.Manager().dict()` / `.list()` —— 通过 manager
      进程做代理；用起来方便，但每次访问都是 IPC。
    - `multiprocessing.shared_memory`（3.8+）—— 大块 numpy / bytes 数据。
    - 外部存储（Redis、SQLite）—— 拿不准时就这条。
  不要尝试修改一个"以为是共享的"普通 Python 对象 —— 子进程拿到的是副本，
  你改了父进程永远看不到。

- **IPC 不是免费的。** 通过 `mp.Queue` 发送大对象（numpy 数组、大 dict）
  会经历 pickle + 字节流 + unpickle。每个队列每秒能跑过的小对象顶多几
  万。如果队列是瓶颈，就要批处理。

- **崩溃是隔离的，但需要你处理。** 如果一个子进程 segfault 或 OOM，
  `Process.is_alive()` 会返回 False，`Process.exitcode` 会是负数（被
  信号杀死）或非零。父进程要 log + 决定要不要重启。本模板的关停循环
  能处理"worker 不退出" → `terminate()` → `kill()`，但**没有**自动重启
  死掉的 worker —— 需要的话自己加。

- **子进程的信号处理继承不一定符合预期。** 在 `fork` 下子进程继承父进
  程的信号 handler；在 `spawn` 下不继承。模板把 SIGTERM handler 只挂
  在父进程上，子进程通过共享的 `mp.Event` 收停止信号。除非你显式设置，
  否则不要依赖信号送达子进程。

- **不要直接 `os.fork()`。** 用 `multiprocessing.Process`（或一次性任务
  用 `concurrent.futures.ProcessPoolExecutor`）。裸 `os.fork()` 会跳过
  Python 期望做的很多清理工作。
