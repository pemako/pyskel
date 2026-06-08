# mq

[English](README.md)

Asyncio + Redis Streams 消费者骨架，自带 consumer-group、重试、DLQ。

## 结构

```
.
├── pyproject.toml
├── settings.yaml
├── control.sh
└── mq/
    ├── __init__.py
    ├── config.py
    ├── core.py        # MqService（TaskGroup：N 个 consumer + 1 个 reaper）
    ├── handler.py     # MessageHandler 基类 + EchoHandler
    └── main.py        # 入口：argparse → dictConfig → asyncio.run
```

## 快速开始

需要一个可达的 Redis 6.2+（`XAUTOCLAIM` 要求）。本地开发：

```bash
docker run -d --rm --name mq-redis -p 6379:6379 redis:7-alpine
python3 -m venv .venv
.venv/bin/pip install -e .
PATH=".venv/bin:$PATH" ./control.sh start
PATH=".venv/bin:$PATH" ./control.sh status
PATH=".venv/bin:$PATH" ./control.sh stop
```

produce 一条消息试一下消费链路：

```bash
.venv/bin/python -c "
import asyncio
from redis.asyncio import Redis

async def main():
    r = Redis.from_url('redis://localhost:6379/0', decode_responses=True)
    await r.xadd('mq:tasks', {'msg': 'hello'})
    await r.aclose()

asyncio.run(main())
"
```

看 `logs/mq.log` 应该有 `got <id>: {'msg': 'hello'}`。

## 配置

`settings.yaml`：

- `service.consumers` — 并发 consumer task 数（默认 4）
- `service.max_retries` — 投递次数超过此值进 DLQ（默认 3）
- `service.claim_idle_ms` — 消息在 PEL 里超过此毫秒数会被 reaper 接管（默认 30000）
- `service.reaper_interval_s` — reaper 轮询间隔秒数（默认 5.0）
- `redis.url` — 连接 URL（默认 `redis://localhost:6379/0`）；env 覆盖：`DYNACONF_REDIS__URL=...`
- `redis.stream` — 输入 stream 名（默认 `mq:tasks`）
- `redis.group` — consumer group 名（默认 `mq-workers`）

DLQ 消息落到 `<stream>:dlq`（例如 `mq:tasks:dlq`）。

## 演示要点

- `redis.asyncio`（官方推荐；`aioredis` 已废弃）
- Consumer group pattern：XREADGROUP + 成功后 XACK
- Reaper task 用 `XAUTOCLAIM` 接管挂掉 consumer 的未 ack 消息
- 重试预算：投递次数超过 `max_retries` 进 `:dlq` stream
- 结构化关闭：`Service.request_stop()` + `loop.add_signal_handler`

## 替换 `EchoHandler`

在 `mq/handler.py` 里继承 `MessageHandler`，赋值给 `service.handler`（或改 `MqService.__init__` 里的默认）：

```python
class MyHandler(MessageHandler):
    async def handle(self, msg_id, fields):
        await do_real_work(fields)
```

## 何时选这个模板

数据源是 Redis Streams 且需要 consumer-group 语义、想要现成的重试/DLQ 骨架。其他 broker（Kafka、RabbitMQ、NATS）的话，consumer/reaper 形态可以照搬，换个客户端库即可。
