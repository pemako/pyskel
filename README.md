# pyskel

[![e2e](https://github.com/pemako/pyskel/actions/workflows/e2e.yml/badge.svg)](https://github.com/pemako/pyskel/actions/workflows/e2e.yml)

[中文](README_zh.md)

A bash-driven scaffolder for Python 3.12 service templates.

`./pyskel <template> <name>` copies one of seven templates and
substitutes the template name with yours. The result is a runnable
service skeleton you can `pip install -e .` and start immediately.

## Templates

Pick the shape that matches your workload — each template's own
README has detailed "when to use" / "when not to use" guidance.

| Template                               | Shape                                                       | Pick when                                              |
| -------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------ |
| [`simple`](tpl/simple/README.md)       | Single process, single loop                                 | Polling, schedulers, single-tenant daemons             |
| [`multi_t`](tpl/multi_t/README.md)     | One process, N threads, no shared queue                     | Concurrent I/O fan-out (parallel pollers, scrapers)    |
| [`multi_t_q`](tpl/multi_t_q/README.md) | Producer/consumer with bounded queue, retry, durable replay | Pipelines that pull-then-process and survive restarts  |
| [`multi_p`](tpl/multi_p/README.md)     | One parent process, N child processes, shared `mp.Event`    | CPU-bound parallelism, process isolation               |
| [`multi_p_h`](tpl/multi_p_h/README.md) | FastAPI + uvicorn prefork (HTTP/JSON)                       | Public APIs, browser-friendly debug, REST services     |
| [`multi_p_g`](tpl/multi_p_g/README.md) | grpcio + protobuf, multi-process via `SO_REUSEPORT`         | Internal RPC, high-RPS service-to-service, streaming   |
| [`multi_p_t`](tpl/multi_p_t/README.md) | Apache Thrift, multi-process via `SO_REUSEPORT`             | Legacy Thrift integrations (HBase gateway, Hive, etc.) |
| [`aio`](tpl/aio/README.md)             | Single-process asyncio loop with TaskGroup                  | Async IO services (HTTP clients, DB drivers, brokers)  |
| [`mq`](tpl/mq/README.md)               | Asyncio + Redis Streams consumer (group, retry, DLQ)        | Brokered tasks, event consumers, durable replay queues |

For new RPC services in 2026 with no Thrift constraint, prefer
`multi_p_g` over `multi_p_t`. For new HTTP services, prefer `multi_p_h`.

## Quick start

```bash
# Interactive (lists tpl/ and prompts for template + name)
./pyskel

# Non-interactive
./pyskel simple my_service
./pyskel multi_p_h my_api

# List available templates
./pyskel --list
```

The generated project goes into your **current working directory**.
Run `pyskel` from wherever you want the project to land — no need to
`cd` into the repo first.

```bash
cd ~/projects/
/path/to/pyskel/pyskel multi_p_h my_api
cd my_api
pip install -e .
./control.sh start
```

## Requirements

**Generator host (machine running `pyskel`):**

- **bash 4+** — uses parameter expansion (`${var^}`, `${var//x/y}`) and
  globstar that bash 3.2 doesn't support. macOS ships bash 3.2; install
  the modern one with `brew install bash`.
- POSIX essentials: `cp`, `mv`, `mkdir`, `cat`, `printf`, `find`.
- No `gsed`, no `rename`, no `tput`. The generator is plain-bash text
  substitution — single platform-neutral code path.

**Generated projects:**

- Python 3.12+
- `pip install -e .` for runtime deps (Dynaconf, plus framework for
  network templates: FastAPI/uvicorn, grpcio, thrift)
- `multi_p_g` and `multi_p_t` ship pre-generated stubs so they install
  and run without codegen tools. To regenerate after editing IDL:
  - `multi_p_g` → `pip install -e '.[dev]'` (adds `grpcio-tools`)
  - `multi_p_t` → `brew install thrift` / `apt install thrift-compiler`
    (the Thrift compiler is a system package, not a pip package)

## What every generated project looks like

The skeleton is consistent across templates:

```
my_service/
├── pyproject.toml         PEP 621, Python 3.12+
├── settings.yaml          Dynaconf config + stdlib logging dictConfig
├── control.sh             start/stop/restart/status (pid file in logs/)
├── README.md              copy of the template's README
└── my_service/
    ├── __init__.py
    ├── main.py            entry point (python -m my_service.main)
    ├── config.py          Dynaconf loader
    └── core.py            service class with run() / stop()
```

Network templates (`multi_p_h`, `multi_p_g`, `multi_p_t`) add a
`handler.py`. RPC templates add `proto/` (IDL source) and `<pkg>/pb/`
(generated stubs). `multi_t_q` adds a `tasks.py` for the Task dataclass
and processor.

Conventions across all templates:

- **`control.sh` lives at the project root** (not in `scripts/`).
- **`kill -0 $pid`** for process-existence checks (POSIX, no `vmmap` /
  `/proc` branching).
- **Stdlib `logging` dictConfig** in `settings.yaml`. No `loguru`.
- **Bounded shutdown** — every template's `stop` has a deadline + escalation.
- **Cross-process logging** in multi-process templates uses
  `QueueHandler` + `QueueListener` so all workers write through the
  parent's handlers (single log file, no interleaving).
- **`SO_REUSEPORT`** for the two RPC templates' multi-worker port sharing.
