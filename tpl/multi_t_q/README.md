# multi_t_q

[中文](README_zh.md)

A multi-threaded **producer / consumer** task pipeline with bounded
queue, retry-on-failure, and **durable replay** — leftover tasks
persist to disk on shutdown and are resumed on next start.

## When to use

`multi_t_q` is for "one source of work, fan it out to N workers".
Reach for it when:

- **Polling-then-processing pipelines** — a producer pulls from an
  external source (database, S3, Redis queue, file watcher), workers
  do the per-item processing in parallel.
- **Work that can fail and should be retried** — built-in retry with
  configurable attempts and interval; final failures captured for
  inspection rather than silently dropped.
- **Pipelines you want to *resume* after restart** — the queue plus
  failed-task list are pickled to a todo file on shutdown; next start
  loads them before producing new work.
- **I/O-bound parallelism with a single point of arrival** — e.g.
  scraping URLs, sending notifications, indexing documents.

The hallmark: **one producer, many workers, bounded buffer, retries
with persistence**.

## When NOT to use

| Need | Use |
|---|---|
| N independent workers, no shared queue | `multi_t` |
| CPU-bound work | `multi_p` (multiprocessing) |
| Single logical loop, no parallelism | `simple` |
| HTTP API | `multi_p_h` (FastAPI) |
| RPC API | `multi_p_g` (gRPC) |
| Distributed queue across machines | use Redis / RabbitMQ / SQS as the queue, then `multi_t` or `multi_p` to consume |

`multi_t_q`'s queue is **in-process** — it survives one process's
shutdown via the todo file, but it doesn't fan out across machines.
For multi-host work, externalize the queue (Redis, SQS, NATS) and let
each host run a `multi_t`-style consumer.

## What you get

- `pyproject.toml` (PEP 621), Python 3.12+. Single dependency:
  `dynaconf`.
- `tasks.py` — `Task` dataclass + `TaskProcessor` base class. Override
  `TaskProcessor.process()` with your real per-task work.
- `core.py` — `Multi_t_qService` orchestrator with:
  - **Bounded `queue.Queue`** so a fast producer can't OOM you.
  - **One producer thread** (`_produce_loop`) calling `_produce_next()`
    — override that one method with your real source of work.
  - **N worker threads** (`_worker_loop`) consuming + retrying.
  - **Retry-with-failed-queue**: tasks that fail more than
    `retry_attempts` times move to a separate failed queue.
  - **Durable replay**: on shutdown, drain both queues and pickle to
    `data/todo.pickle`; on next start, load and resume.
- **Bounded shutdown** — main thread joins all workers + producer with
  a 30s deadline.
- **Signal-aware** — `SIGTERM`/`SIGINT` cleanly stops producer + workers
  and triggers persistence before exit.

## Install

    pip install -e .

## Run

    ./control.sh start
    ./control.sh status
    ./control.sh stop

Or directly:

    python -m multi_t_q.main

## Configuration

Adjust in `settings.yaml`:

    service:
      workers: 4
      queue_max: 1000        # bounded; producer back-pressures when full
      retry_attempts: 3      # transient failures retried this many times
      retry_interval: 1      # seconds between retries
      todo_file: data/todo.pickle

## Where the work goes

Two extension points, in order of how often you'll touch them:

1. **`tasks.TaskProcessor.process(task)`** — replace the body. Return
   `True` on success, `False` for transient failure (retry), or raise
   on unexpected error (caught and counted as retry).
2. **`Multi_t_qService._produce_next()`** in `core.py` — replace with
   your real source. Default emits a synthetic task once per second so
   the template is end-to-end runnable out of the box. Real
   implementations might:
   - poll an external queue (Redis BLPOP, SQS receive_message)
   - watch a directory (inotify, polling stat)
   - read from a file or stream
   - drain a database query

   Return `None` when there's no work right now — the producer sleeps
   1s before asking again. Don't block forever inside `_produce_next`;
   shutdown won't be able to wake you.

If your `Task` dataclass needs more fields, edit `tasks.py`. Just keep
it pickle-friendly so the todo replay still works.

## Retry semantics

A task fails when `process()` returns `False` or raises. The worker:

1. Increments `task.attempts`.
2. If `attempts > retry_attempts`, moves the task to the failed queue
   and stops. (Failed tasks are persisted on shutdown.)
3. Otherwise sleeps `retry_interval` seconds (interruptible by
   shutdown) and retries.

This is **at-least-once** delivery: a worker that crashes mid-`process()`
will lose that one task (not retried). For at-least-once across worker
crashes, you'd need to commit the task as "in flight" durably before
processing — which is exactly what an external queue (SQS, Redis with
ack, RabbitMQ) gives you. `multi_t_q`'s in-process queue isn't that.

## Persistence semantics

On clean shutdown (SIGTERM / SIGINT), the service:

1. Stops the producer (no new tasks enter).
2. Joins all worker threads with a deadline.
3. Drains `task_queue` (in-flight) and `failed_queue` (gave up) into
   one bundle.
4. Pickles the bundle to `data/todo.pickle`.

On the next start, `_load_todo()` reads the bundle:

- `pending` tasks rejoin `task_queue` (workers pick them up).
- `failed` tasks go straight back into `failed_queue` (NOT retried) —
  surfaced for the operator to inspect via `data/todo.pickle` if the
  next run also fails to drain them.

The todo file is **deleted after a successful load**, so a crash
before the next clean shutdown doesn't double-replay tasks.

If your processing is non-idempotent (sending money, posting messages),
relying on this for crash safety is risky — use an external queue
with explicit ack instead.

## Project structure as it grows

The template ships at **stage 0**. Evolve through stages — don't
pre-create empty directories at stage 0 "just in case", and don't
skip stages.

### Stage 0 — initial (≤ 4 modules)

What you get out of the generator. Note: this template starts with
*four* package modules instead of three because tasks/processor are
genuinely separate from the orchestrator.

    multi_t_q/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── multi_t_q/
        ├── __init__.py
        ├── main.py        entry point
        ├── config.py      Dynaconf loader
        ├── core.py        Multi_t_qService — pool, queue, persistence
        └── tasks.py       Task dataclass + TaskProcessor

### Stage 1 — small service (5–8 modules, still flat)

Helpers added. Still flat:

    multi_t_q/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── tasks.py
    ├── client.py            external API client
    └── metrics.py           per-task counters

### Stage 2 — multiple task types

When you have 2+ kinds of tasks (different processors, different
payloads), split `tasks.py` into a subpackage:

    multi_t_q/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    └── tasks/
        ├── __init__.py      re-export Task / TaskProcessor for backward compat
        ├── base.py          shared Task dataclass + base TaskProcessor
        ├── url_fetch.py     UrlFetchTask + UrlFetchProcessor
        └── notify.py        NotifyTask + NotifyProcessor

The orchestrator in `core.py` typically picks a processor based on
task type — a router method, or a dict mapping `type(task)` →
processor.

### Stage 3 — large pipeline (20+ modules)

Subpackages grow subpackages. Sibling top-level dirs:

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── multi_t_q/            the package
    ├── tests/                pytest tree
    │   ├── unit/
    │   └── integration/
    ├── scripts/              one-off ops (backfill, replay)
    ├── docs/                 runbook, architecture
    └── ops/                  Dockerfile, k8s manifests

### What does NOT change as you grow

1. `main.py`, `config.py`, `core.py` stay at the package root.
2. `control.sh`, `pyproject.toml`, `settings.yaml` stay at the project
   root.
3. The Python package is the only directory under the project root
   with an `__init__.py`.
4. `data/todo.pickle` lives at project root (under `data/`), not
   inside the package. It's runtime state, not code.

## Producer/consumer-specific notes

- **The default producer pace (1 task/s) is just for the demo.** Your
  real `_produce_next` should return as fast as possible when work is
  available. Returning None tells the producer to sleep 1s and ask
  again — that's the polling cadence.
- **Don't sleep inside `_produce_next`.** Returning None is the
  graceful way; sleeping locks the thread out of seeing shutdown.
- **The producer is single-threaded.** If your producer is the
  bottleneck (e.g. it's CPU-heavy parsing or it serializes against
  external rate limits), make `_produce_next` cheap and do the heavy
  work inside `TaskProcessor.process` where the parallelism actually
  is. Or split the source into multiple producers (override `run()`
  to start more producer threads — but expect to design queue
  contention).
- **`task_queue` is bounded; failures are unbounded.** A persistent
  per-task failure that can't be retried away will grow the failed
  queue indefinitely until shutdown. Watch for this in your processor
  and fail fast (raise something specific) when the operator should
  intervene.
- **`task.attempts` survives retries within a process.** It does NOT
  reset across restarts — a task in `data/todo.pickle` carries its
  current attempt count. This means a worker crash loop won't quietly
  spend your retry budget. If you DO want fresh attempts on resume,
  zero out `task.attempts` in `_load_todo`.
- **Pickle is the persistence format.** Pros: any Python object survives
  faithfully. Cons: schema changes break old pickles; pickle is unsafe
  for untrusted data. For long-lived production state, swap to JSON
  (limits Task fields to JSON-able types) or SQLite (gives you
  transactions and crash safety). The shape of `_dump_todo` /
  `_load_todo` is small enough that the swap is straightforward.
- **`task_done()` is called even on retry exhaustion.** The
  `queue.Queue` task counter goes down once per task you `get()`,
  regardless of whether process succeeded. We never call `join()` on
  the queue, so this only matters if you add monitoring around it.
- **In-process only.** This template is one process. To horizontally
  scale, externalize the queue. Don't run multiple `multi_t_q`
  instances against the same `data/todo.pickle` — they'll race on the
  file, and pickle protocol doesn't lock.
