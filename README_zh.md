# pyskel

[![e2e](https://github.com/pemako/pyskel/actions/workflows/e2e.yml/badge.svg)](https://github.com/pemako/pyskel/actions/workflows/e2e.yml)

[English](README.md)

bash 驱动的 Python 3.12 服务模板生成器。

`./pyskel <template> <name>` 拷贝一份模板，把模板名替换成你给的
项目名，生成一个直接 `pip install -e .` 就能跑的服务骨架。

## 模板矩阵

按你工作负载的形状选 —— 每个模板自己的 README 里有详细的"何时用 /
何时不用"说明。

| 模板                                      | 形状                                       | 何时选                                       |
| ----------------------------------------- | ------------------------------------------ | -------------------------------------------- |
| [`simple`](tpl/simple/README_zh.md)       | 单进程单循环                               | 轮询、调度、单租户守护                       |
| [`multi_t`](tpl/multi_t/README_zh.md)     | 单进程 N 线程，无共享队列                  | 并发 I/O 扇出（并行 poller、爬虫）           |
| [`multi_t_q`](tpl/multi_t_q/README_zh.md) | 生产者/消费者，有界队列，重试，关停持久化  | 拉取-then-处理 + 跨重启续跑的流水线          |
| [`multi_p`](tpl/multi_p/README_zh.md)     | 父进程 + N 子进程，共享 `mp.Event`         | CPU 密集并行、进程隔离                       |
| [`multi_p_h`](tpl/multi_p_h/README_zh.md) | FastAPI + uvicorn prefork（HTTP/JSON）     | 公开 API、浏览器友好 debug、REST 服务        |
| [`multi_p_g`](tpl/multi_p_g/README_zh.md) | grpcio + protobuf，多进程 + `SO_REUSEPORT` | 内部 RPC、高 RPS 服务到服务、streaming       |
| [`multi_p_t`](tpl/multi_p_t/README_zh.md) | Apache Thrift，多进程 + `SO_REUSEPORT`     | 老 Thrift 系统对接（HBase gateway、Hive 等） |

2026 年新 RPC 服务、没有 Thrift 历史包袱的话，选 `multi_p_g` 优于
`multi_p_t`。新 HTTP 服务选 `multi_p_h`。

## 快速开始

```bash
# 交互式（列出 tpl/ 下的模板，让你选模板和起项目名）
./pyskel

# 非交互式
./pyskel simple my_service
./pyskel multi_p_h my_api

# 列出可用模板
./pyskel --list
```

生成的项目落到**你当前的工作目录**。从哪里调用 pyskel，项目就在哪里 —— 不用先 `cd` 进 repo 根目录。

```bash
cd ~/projects/
/path/to/pyskel/pyskel multi_p_h my_api
cd my_api
pip install -e .
./control.sh start
```

## 依赖要求

**跑生成器的机器：**

- **bash 4+** —— 用了参数展开（`${var^}`、`${var//x/y}`）和 globstar，
  bash 3.2 不支持。macOS 自带 3.2，装新的：`brew install bash`。
- POSIX 必备命令：`cp`、`mv`、`mkdir`、`cat`、`printf`、`find`。
- 不用 `gsed`、不用 `rename`、不用 `tput`。生成器是纯 bash 文本替换，
  跨平台一份代码。

**生成的项目：**

- Python 3.12+
- `pip install -e .` 装运行时依赖（Dynaconf，加上网络模板的框架：
  FastAPI/uvicorn、grpcio、thrift）
- `multi_p_g` 和 `multi_p_t` 模板预先生成了 stub 文件并提交，所以装完
  立即能跑无需 codegen。要在编辑 IDL 后重新生成：
  - `multi_p_g` → `pip install -e '.[dev]'`（加 `grpcio-tools`）
  - `multi_p_t` → `brew install thrift` / `apt install thrift-compiler`
    （Thrift 编译器是系统包，不是 pip 包）

## 生成项目的统一形状

所有模板的骨架结构是一致的：

```
my_service/
├── pyproject.toml         PEP 621，Python 3.12+
├── settings.yaml          Dynaconf 配置 + stdlib logging dictConfig
├── control.sh             start/stop/restart/status（pid 文件在 logs/）
├── README.md              模板自己的 README
└── my_service/
    ├── __init__.py
    ├── main.py            入口（python -m my_service.main）
    ├── config.py          Dynaconf 加载
    └── core.py            服务类，提供 run() / stop()
```

网络模板（`multi_p_h`、`multi_p_g`、`multi_p_t`）多一个 `handler.py`。
RPC 模板多一个 `proto/`（IDL 源）和 `<pkg>/pb/`（生成的 stub）。
`multi_t_q` 多一个 `tasks.py`（Task dataclass 和 processor）。

跨模板的共同约定：

- **`control.sh` 在项目根**，不在 `scripts/` 下。
- **`kill -0 $pid`** 做进程存活检查（POSIX，不需要 `vmmap` / `/proc`
  分支）。
- **stdlib `logging` dictConfig**（在 `settings.yaml` 里），不用
  `loguru`。
- **有界关停** —— 每个模板的 stop 都带超时上限 + 升级路径。
- **跨进程日志** 在多进程模板里走 `QueueHandler` + `QueueListener`，
  让所有 worker 通过父进程的 handler 写日志（单一文件，不会
  interleave）。
- **`SO_REUSEPORT`** 让两个 RPC 模板的 worker 进程共享端口。
