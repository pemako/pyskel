# mq

[中文](README_zh.md)

Asyncio + Redis Streams consumer skeleton with consumer-group, retry, and DLQ.

## Layout

```
.
├── pyproject.toml
├── settings.yaml
├── control.sh
└── mq/
    ├── __init__.py
    ├── config.py
    ├── core.py        # MqService (TaskGroup: N consumers + reaper)
    ├── handler.py     # MessageHandler base + EchoHandler
    └── main.py        # entry: argparse → dictConfig → asyncio.run
```

## Quick start

Requires a reachable Redis 6.2+ (for `XAUTOCLAIM`). Local dev:

```bash
docker run -d --rm --name mq-redis -p 6379:6379 redis:7-alpine
python3 -m venv .venv
.venv/bin/pip install -e .
PATH=".venv/bin:$PATH" ./control.sh start
PATH=".venv/bin:$PATH" ./control.sh status
PATH=".venv/bin:$PATH" ./control.sh stop
```

Produce a message to exercise the consumer:

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

Watch `logs/mq.log` for `got <id>: {'msg': 'hello'}`.

## Configuration

`settings.yaml`:

- `service.consumers` — number of concurrent consumer tasks (default 4)
- `service.max_retries` — DLQ messages whose delivery count exceeds this (default 3)
- `service.claim_idle_ms` — milliseconds a message may sit in PEL before reaper claims it (default 30000)
- `service.reaper_interval_s` — seconds between reaper polling rounds (default 5.0)
- `redis.url` — connection URL (default `redis://localhost:6379/0`); override via `DYNACONF_REDIS__URL=...`
- `redis.stream` — input stream name (default `mq:tasks`)
- `redis.group` — consumer group name (default `mq-workers`)

DLQ messages land on `<stream>:dlq` (e.g. `mq:tasks:dlq`).

## What it demonstrates

- `redis.asyncio` (the recommended path; `aioredis` is deprecated)
- Consumer group pattern: XREADGROUP + XACK on success
- Reaper task using `XAUTOCLAIM` to recover messages from dead consumers
- Retry budget: messages exceeding `max_retries` go to a `:dlq` stream
- Structured shutdown: `Service.request_stop()` + `loop.add_signal_handler`

## Replacing `EchoHandler`

Subclass `MessageHandler` in `mq/handler.py` and assign to `service.handler` before calling `service.run()` (or edit `MqService.__init__` to swap the default).

```python
class MyHandler(MessageHandler):
    async def handle(self, msg_id, fields):
        await do_real_work(fields)
```

## When to pick this template

When you have a Redis Streams source and want consumer-group semantics with a working retry/DLQ scaffold. For other brokers (Kafka, RabbitMQ, NATS), the consumer/reaper shape transfers but you'll swap the client library.
