# multi_p

[中文](README_zh.md)

A multi-process Python 3.12 service template — N worker processes
driven off a shared shutdown event, with cross-process logging
through a `QueueListener`.

## When to use

`multi_p` is one parent process that fans out N children. Reach for it when:

- **CPU-bound parallelism** — pure Python work that's pegged at 100% on
  one core. The GIL makes threads useless here; processes give you
  true parallelism.
- **Workloads that touch native libraries with thread-unsafe state** —
  some C extensions, BLAS implementations, or libraries that hold
  global state can be safely multiplied across processes but not
  across threads.
- **Workloads where one bad input shouldn't kill the others** — a
  worker process can segfault, OOM, or hit an `assert` and die without
  taking the rest of the pool with it. Threads in the same process
  share fate.
- **Memory locality matters** — each child gets its own heap. No false
  sharing, no GIL contention on object refcounts when handling lots
  of allocations.

The hallmark: workers are CPU-heavy, isolation matters, or both.

## When NOT to use

| Need | Use |
|---|---|
| I/O-bound work (HTTP, DB calls, file I/O) | `multi_t` — far cheaper |
| Producer/consumer with retries | `multi_t_q` |
| Single logical loop, no concurrency | `simple` |
| RPC / Thrift server | `multi_p_t` |
| Lots of shared in-memory state between workers | `multi_t` (or rethink) |
| Sub-millisecond IPC latency | rethink the architecture |

If your workers spend most of their time waiting on the network,
`multi_p` is overkill — fork is expensive, IPC adds latency, and you
get no parallelism benefit because threads would have done the job.

If workers need to constantly read/write each other's data, processes
hurt: every shared structure needs `Manager()` (proxy IPC) or shared
memory (`multiprocessing.shared_memory`). Threads are 100× simpler.

## What you get

- `pyproject.toml` (PEP 621), Python 3.12+, single dependency: `dynaconf`.
- **Cross-process logging via `QueueHandler` + `QueueListener`** — every
  child pushes records to a shared `mp.Queue`; the parent has one
  background thread that drains the queue into the configured
  file/console handlers. Single log file, no interleaved lines, no
  fight over file descriptors.
- `mp.Event` for shutdown — `SIGTERM` to the parent sets it; children
  poll it via `wait(timeout=1.0)` and exit immediately on signal.
- **Bounded shutdown** — parent gives workers 30s to exit, then
  `terminate()` (SIGTERM equivalent), then `kill()` (SIGKILL) as last
  resort. Logs every escalation.
- Worker name (`worker-0`, `worker-1`, …) and PID in every log line:
  `[pid=12345 worker-0]`.

## Install

    pip install -e .

## Run

    ./control.sh start
    ./control.sh status
    ./control.sh stop

Or directly:

    python -m multi_p.main

Adjust worker count in `settings.yaml`:

    service:
      workers: 4

## Where the work goes

Replace the body of `_do_work()` in `multi_p/core.py`. It runs once
per worker per tick:

```python
def _do_work(logger: logging.Logger) -> None:
    logger.info("running")
```

`_do_work` and `_worker_main` are **module-level functions, not methods**,
because `multiprocessing` with the `spawn` start method (default on
macOS, Windows) needs to pickle the worker target. Bound methods can
be pickled but bring along their entire `self` graph; module-level
functions are simpler and safer.

## Project structure as it grows

The template ships at **stage 0**. As the service grows, evolve the
layout through these stages — don't pre-create empty directories at
stage 0 "just in case", and don't skip stages.

### Stage 0 — initial (≤ 3 modules)

What you get out of the generator:

    multi_p/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── multi_p/
        ├── __init__.py
        ├── main.py        entry point: argparse, logging, queue listener
        ├── config.py      Dynaconf loader
        └── core.py        worker pool + per-tick work

**Rule at this stage:** new code goes flat next to `core.py`. Don't
introduce subpackages yet — under 5–8 files the overhead isn't worth it.

### Stage 1 — small service (5–8 modules, still flat)

You've added a few helpers. Still flat:

    multi_p/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── client.py
    ├── parser.py
    └── metrics.py

**Trigger to move to stage 2:** when `ls multi_p/` no longer fits on
one screen, *or* when two files start sharing a prefix.

### Stage 2 — by-concern subpackages (8–20 modules)

Group by concern, not by type. Keep `main.py`, `config.py`, `core.py`
at the top.

    multi_p/
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

    multi_p/
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
    ├── multi_p/          the package
    ├── tests/            pytest tree, mirrors multi_p/
    │   ├── unit/
    │   └── integration/
    ├── scripts/          one-off ops scripts (NOT a package)
    ├── docs/             arch notes, runbook
    └── ops/              Dockerfile, k8s manifests, terraform

`tests/` is a sibling so `pip install` doesn't ship test code.
`scripts/` has no `__init__.py` because those tools are throwaway,
not importable.

### What does NOT change as you grow

These three rules hold from stage 0 to stage 3:

1. `main.py`, `config.py`, `core.py` stay at the package root.
2. `control.sh`, `pyproject.toml`, `settings.yaml` stay at the project
   root.
3. The package is the only directory under the project root with an
   `__init__.py`.

## Multiprocessing-specific notes

- **`fork` vs `spawn` start method.** Linux default is `fork`; macOS
  and Windows default is `spawn`. They behave very differently:
    - **`fork`** — child inherits the parent's entire memory image,
      including open file descriptors, locks (in unknown state),
      and threads (only the calling thread is recreated). Cheap to
      start; dangerous if the parent has held a lock or started a
      thread before forking. **Don't fork after starting any threads.**
    - **`spawn`** — child runs `python` from scratch, re-imports
      modules, re-runs `if __name__ == '__main__':` guards. Slower
      to start; safer because there's no inherited state. Requires
      worker target + args to be picklable (which is why
      `_worker_main` is a module-level function, not a method).
  This template's child-side `_init_child_logging` resets the root
  logger explicitly so it works correctly under both methods.

- **Children can't share Python objects directly.** Each process has
  its own heap. To share state, use:
    - `multiprocessing.Value` / `Array` — single primitive, shared memory.
    - `multiprocessing.Manager().dict()` / `.list()` — proxied through
      a manager process; convenient but every access is IPC.
    - `multiprocessing.shared_memory` (3.8+) — for large numpy/bytes data.
    - An external store (Redis, SQLite) — when in doubt, do this.
  Don't try to mutate a "shared" Python object that wasn't explicitly
  created shared — the child has its own copy and the parent never sees
  the changes.

- **IPC overhead is real.** Sending a large object (numpy array, big
  dict) through `mp.Queue` involves pickling + bytes-over-pipe + unpickling.
  Throughput tops out around tens of thousands of small objects per
  second per queue. If you're queue-bound, batch.

- **Crashes are isolated, but you have to handle them.** If a child
  segfaults or hits OOM, `Process.is_alive()` returns False and
  `Process.exitcode` will be negative (signal-killed) or non-zero. The
  parent should log + decide whether to respawn. This template's
  shutdown loop handles "worker won't exit" with `terminate()` →
  `kill()`, but does **not** auto-respawn dead workers — add that if
  you need it.

- **Signal handlers in children inherit but don't always do what you'd
  expect.** Children inherit the parent's signal handlers under `fork`
  but not under `spawn`. The template puts the SIGTERM handler only on
  the parent; children watch the shared `mp.Event` instead. Don't
  rely on signal delivery to children unless you set it up explicitly.

- **Don't `os.fork()` directly.** Use `multiprocessing.Process` (or
  `concurrent.futures.ProcessPoolExecutor` for one-off tasks). Bare
  `os.fork()` skips a lot of cleanup that Python expects.
