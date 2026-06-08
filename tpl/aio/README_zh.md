# aio

[English](README.md)

Asyncio-first 单进程 Python 服务骨架。

## 结构

```
.
├── pyproject.toml
├── settings.yaml
├── control.sh
└── aio/
    ├── __init__.py
    ├── config.py
    ├── core.py        # AioService，使用 asyncio.TaskGroup
    └── main.py        # 入口：argparse → dictConfig → asyncio.run
```

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
PATH=".venv/bin:$PATH" ./control.sh start
PATH=".venv/bin:$PATH" ./control.sh status
PATH=".venv/bin:$PATH" ./control.sh stop
```

日志位于 `logs/aio.log`（每日轮转，保留 7 份）和 `logs/aio.out`（stdout 捕获）。

## 配置

`settings.yaml`：

- `service.tasks` — worker 协程数（默认 3）
- `service.tick_interval` — worker 循环间隔（秒，默认 1.0）

## 演示要点

- `asyncio.TaskGroup` 实现结构化并发（3.11+）
- 信号处理用 `loop.add_signal_handler`（asyncio 上下文里唯一可靠的方式）
- `Service.request_stop()` 接口 —— 信号处理和测试都用这一个方法
- 有界关闭：worker 看到 `_stop.set()` 后协作退出；watcher task 强制 30s 截止

## 何时选这个模板

服务主体是 async IO（HTTP 客户端、DB 驱动、外部 API），希望自己掌控 event loop。要做 HTTP 服务用 `multi_p_h`；要消费外部消息队列用 `mq`。
