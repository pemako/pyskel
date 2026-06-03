# multi_t

[中文](README_zh.md)

A multi-threaded Python 3.12 service template — N identical worker
threads, all driven off the same shutdown signal.

## When to use

`multi_t` is one OS process running N worker threads that share memory
and run the same kind of work. Reach for it when:

- **Concurrent I/O fan-out** — N pollers hitting different shards/keys/
  endpoints in parallel; aggregate throughput matters but each call is
  network-bound.
- **Embarrassingly parallel I/O** — fetch URLs, ping hosts, scan a fleet
  of devices, run health checks across many targets.
- **Background workers with shared in-memory state** — caches, in-process
  pub/sub, in-flight request coordination — anything where threads need
  to read each other's data without IPC overhead.
- **Workloads where GIL is not the bottleneck** — almost any I/O-bound
  service. The GIL is released during system calls, so N threads do
  give you N× I/O concurrency.

The hallmark: each worker does the **same kind of work** and they all
react to the same shutdown signal.

## When NOT to use

| Need | Use |
|---|---|
| Producer/consumer with a task queue + retries | `multi_t_q` |
| CPU-bound parallelism (the GIL hurts you) | `multi_p` (multiprocessing) |
| Single logical loop, no concurrency | `simple` |
| RPC / Thrift server | `multi_p_t` |
| HTTP server | a real framework (FastAPI, Flask) |

If your workers need to coordinate via a queue (one produces, others
consume), or if individual tasks fail and need retry, that's `multi_t_q`,
not `multi_t`. `multi_t` assumes all workers are interchangeable and
the workload is implicit (a counter, a shared cache, an external queue
each worker pulls from independently).

If you find yourself adding `multiprocessing` primitives or a
`queue.Queue` to coordinate between workers, that's the signal to switch
templates rather than retrofit.

## What you get

- `pyproject.toml` (PEP 621), Python 3.12+, single dependency: `dynaconf`.
- `settings.yaml` for service config and stdlib `logging` dictConfig with
  `%(threadName)s` in the format string (you'll see `worker-0`, `worker-1`
  in every log line).
- `control.sh` with portable PID-file start/stop/restart/status.
- Workers built around `threading.Event`, not a bare `running` bool —
  `SIGTERM` exits the per-iteration sleep immediately instead of waiting
  out the full second.
- Bounded shutdown — main thread joins workers with a 30s deadline and
  warns on any thread that won't exit.

## Install

    pip install -e .

## Run

    ./control.sh start
    ./control.sh status
    ./control.sh stop

Or directly:

    python -m multi_t.main

Adjust worker count in `settings.yaml`:

    service:
      workers: 4

## Where the work goes

Replace the body of `Multi_tService._do_work()` in `multi_t/core.py`.
That method is called once per worker per iteration. Each worker:

1. Calls `_do_work()` (your code).
2. Waits up to 1 second on the shared `_stop` Event.
3. Repeats unless `_stop` was set — in which case it exits immediately.

Don't put the loop itself in `_do_work()`; that's `_work_loop`'s job.
Just put the per-tick action.

## Project structure as it grows

The template ships at **stage 0**. As the service grows, evolve the
layout through these stages — don't pre-create empty directories at
stage 0 "just in case", and don't skip stages.

### Stage 0 — initial (≤ 3 modules)

What you get out of the generator:

    multi_t/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── multi_t/
        ├── __init__.py
        ├── main.py        entry point: argparse, logging, run()
        ├── config.py      Dynaconf loader
        └── core.py        the worker pool + per-tick work

**Rule at this stage:** new code goes flat next to `core.py`. Don't
introduce subpackages yet — overhead is not worth it under 5–8 files.

### Stage 1 — small service (5–8 modules, still flat)

You've added a few helpers. Still flat:

    multi_t/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── client.py
    ├── parser.py
    ├── retry.py
    └── metrics.py

**Trigger to move to stage 2:** when `ls multi_t/` no longer fits on
one screen, *or* when two files start sharing a prefix.

### Stage 2 — by-concern subpackages (8–20 modules)

Group by concern, not by type. Keep `main.py`, `config.py`, `core.py`
at the top.

    multi_t/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── core.py
    ├── clients/         outbound API / DB clients
    ├── handlers/        inbound dispatch
    ├── services/        business logic
    └── models/          dataclasses / ORM / pydantic

| Subpackage | Holds | Typical filenames |
|---|---|---|
| `clients/` | wrappers around outbound network calls | `github.py`, `redis.py`, `s3.py` |
| `handlers/` | inbound dispatch (one file per event type) | `webhook.py`, `cron.py` |
| `services/` | business logic that orchestrates clients + models | `billing.py`, `auth.py` |
| `models/` | data shapes — no I/O, no side effects | `user.py`, `order.py` |
| `db/` | persistence layer if it grows beyond one file | `connection.py`, `queries.py` |
| `utils/` | last resort — small, stateless helpers | `time.py`, `text.py` |

**`utils/` warning:** tends to become a junk drawer. If a helper is
only used by one subpackage, put it inside that subpackage. Only move
it to `utils/` once it's actually used by 2+ subpackages.

### Stage 3 — large service (20+ modules)

Subpackages themselves grow subpackages. Package root stays the same;
what changes is depth, not breadth at the top.

    multi_t/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── core.py
    ├── clients/
    │   ├── __init__.py
    │   ├── github/        was github.py, now a subpackage
    │   │   ├── __init__.py
    │   │   ├── auth.py
    │   │   └── rate_limit.py
    │   └── slack.py
    ├── handlers/
    ├── services/
    └── db/

Plus sibling top-level directories:

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── multi_t/          the package
    ├── tests/            pytest tree, mirrors multi_t/
    │   ├── unit/
    │   └── integration/
    ├── scripts/          one-off ops scripts (NOT a package)
    ├── docs/             arch notes, runbook
    └── ops/              Dockerfile, k8s manifests, terraform

`tests/` is a sibling so `pip install` doesn't ship test code. `scripts/`
has no `__init__.py` because those tools are throwaway, not importable.

### What does NOT change as you grow

These three rules hold from stage 0 to stage 3:

1. `main.py`, `config.py`, `core.py` stay at the package root.
2. `control.sh`, `pyproject.toml`, `settings.yaml` stay at the project
   root.
3. The package is the only directory under the project root with an
   `__init__.py`.

## Threading-specific notes

- **The GIL applies.** `multi_t` gives you parallel I/O, not parallel
  CPU. If you find a worker pegged at 100% Python (not waiting on
  network/disk), threads won't help — switch to `multi_p`.
- **Shared state needs locks.** Workers see the same Python objects.
  Use `threading.Lock` / `threading.RLock` around any mutable shared
  state, or use thread-safe primitives (`queue.Queue`,
  `collections.deque` for some operations).
- **Don't use `daemon=True` for production workers.** The template uses
  `daemon=False` so shutdown actually waits for clean exit. Daemon
  threads die abruptly when the main thread exits — fine for things
  you can lose, lethal for in-flight writes.
- **Catch exceptions inside `_do_work`.** An unhandled exception kills
  that worker thread; the others keep running silently with reduced
  parallelism. Log + swallow exceptions you can recover from; let
  unrecoverable ones propagate after calling `self.stop()`.
