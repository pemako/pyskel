# aio

Asyncio-first single-process Python service skeleton.

## Layout

```
.
├── pyproject.toml
├── settings.yaml
├── control.sh
└── aio/
    ├── __init__.py
    ├── config.py
    ├── core.py        # AioService with asyncio.TaskGroup
    └── main.py        # entry: argparse → dictConfig → asyncio.run
```

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
PATH=".venv/bin:$PATH" ./control.sh start
PATH=".venv/bin:$PATH" ./control.sh status
PATH=".venv/bin:$PATH" ./control.sh stop
```

Logs go to `logs/aio.log` (rotating daily, 7 backups) and `logs/aio.out` (stdout capture).

## Configuration

`settings.yaml`:

- `service.tasks` — number of worker coroutines (default 3)
- `service.tick_interval` — seconds between worker iterations (default 1.0)

## What it demonstrates

- `asyncio.TaskGroup` for structured concurrency (3.11+)
- Signal handling via `loop.add_signal_handler` (the asyncio-correct way)
- `Service.request_stop()` interface — same method used by signal handler and tests
- Bounded shutdown: workers exit cooperatively on `_stop.set()`; a watcher task enforces a 30s deadline

## When to pick this template

When your service is mostly async IO (HTTP clients, DB drivers, external API calls) and you want full control of the event loop. For HTTP-serving use `multi_p_h`. For an external message broker consumer use `mq`.
