# simple

[中文](README_zh.md)

A minimal single-process Python 3.12 service template.

## When to use

A `simple` service is one OS process running one logical loop. Reach for it when:

- **Polling jobs** — wake up periodically, do work, sleep. Cron-like schedulers, queue pollers, S3/dir watchers, heartbeat reporters.
- **Stateful single-tenant daemons** — anything where serial execution is a feature, not a limitation: writers that must keep ordering, leader-elected workers, single-instance ETL.
- **Listeners with naturally serialized work** — a Slack bot, a webhook receiver behind a load balancer (per-process), a long-poll consumer.
- **Internal CLIs that happen to run forever** — supervised by systemd / k8s, restarted on failure, no in-process concurrency needed.

The hallmark: throughput is bounded by the *external* system (network, disk, an API rate limit), not by Python's single thread.

## When NOT to use

If you need any of these, pick a different template:

| Need | Use |
|---|---|
| CPU-bound parallelism | `multi_p` (multiprocessing) |
| Many concurrent I/O tasks | `multi_t` or `multi_t_q` (threading + queue) |
| Producer / consumer pipeline with retries | `multi_t_q` |
| RPC / Thrift server | `multi_p_t` |
| HTTP server | a real framework (FastAPI, Flask) — don't grow `simple` into one |

If you find yourself adding a `ThreadPoolExecutor` or spawning subprocesses inside `core.py`, that's the signal to switch templates rather than retrofit.

## What you get

- `pyproject.toml` (PEP 621), Python 3.12+, single dependency: `dynaconf`.
- `settings.yaml` for service config and stdlib `logging` dictConfig (daily rotation, 7-day retention).
- `control.sh` with portable PID-file start/stop/restart/status (uses `kill -0`, no `vmmap`/`/proc` branching).
- Clean signal handling — `SIGTERM`/`SIGINT` set `running = False` and exit the loop.

## Install

    pip install -e .

## Run

    ./control.sh start
    ./control.sh status
    ./control.sh stop

Or directly:

    python -m simple.main

## Project structure as it grows

The template ships at **stage 0**. As the service grows, evolve the layout
through these stages — don't pre-create empty directories at stage 0
"just in case", and don't skip stages.

### Stage 0 — initial (≤ 3 modules)

What you get out of the generator. Three files, flat, public skeleton:

    simple/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── simple/
        ├── __init__.py
        ├── main.py        entry point: argparse, logging, run()
        ├── config.py      Dynaconf loader
        └── core.py        the service loop

**Rule at this stage:** all new code goes flat next to `core.py`. Don't
introduce subpackages yet — overhead is not worth it under 5–8 files.

### Stage 1 — small service (5–8 modules, still flat)

You've added a few helpers. Still flat, still readable in one `ls`:

    simple/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── client.py        an HTTP client wrapper
    ├── parser.py        domain-specific parsing
    ├── retry.py         retry decorator
    └── metrics.py       prometheus / statsd

**Trigger to move to stage 2:** when `ls simple/` no longer fits on one
screen, *or* when two files start sharing a prefix (`order_handler.py`,
`payment_handler.py` — they want a `handlers/` subpackage).

### Stage 2 — by-concern subpackages (8–20 modules)

Group related modules into subpackages **named after the concern**, not
the type. Keep `main.py`, `config.py`, `core.py` at the top — they are
the public skeleton and should stay one level deep.

    simple/
    ├── __init__.py
    ├── main.py            entry point — stays at top
    ├── config.py          stays at top
    ├── core.py            service loop — stays at top
    ├── clients/           ← external API / DB clients
    │   ├── __init__.py
    │   ├── github.py
    │   └── slack.py
    ├── handlers/          ← inbound event/request handling
    │   ├── __init__.py
    │   ├── webhook.py
    │   └── cron.py
    ├── services/          ← business logic
    │   ├── __init__.py
    │   ├── billing.py
    │   └── notification.py
    └── models/            ← dataclasses / ORM / pydantic
        ├── __init__.py
        ├── user.py
        └── invoice.py

**What goes where:**

| Subpackage | Holds | Typical filenames |
|---|---|---|
| `clients/` | wrappers around *outbound* network calls | `github.py`, `redis.py`, `s3.py` |
| `handlers/` | *inbound* dispatch (one file per event type) | `webhook.py`, `cron.py`, `signal.py` |
| `services/` | business logic that orchestrates clients + models | `billing.py`, `auth.py` |
| `models/` | data shapes — no I/O, no side effects | `user.py`, `order.py` |
| `db/` | persistence layer if it grows beyond one file | `connection.py`, `queries.py` |
| `utils/` | last resort — small, stateless, framework-agnostic helpers | `time.py`, `text.py` |

**`utils/` warning:** this directory tends to become a junk drawer. If a
helper is only used by one subpackage, put it inside that subpackage.
Only move it to `utils/` once it's actually used by 2+ subpackages.

### Stage 3 — large service (20+ modules)

Subpackages themselves grow subpackages. The package root stays the
same — what changes is depth, not breadth at the top.

    simple/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── core.py
    ├── clients/
    │   ├── __init__.py
    │   ├── github/        ← was github.py, now a subpackage
    │   │   ├── __init__.py
    │   │   ├── auth.py
    │   │   └── rate_limit.py
    │   └── slack.py
    ├── handlers/
    ├── services/
    │   ├── billing/
    │   │   ├── __init__.py
    │   │   ├── invoice.py
    │   │   └── refund.py
    │   └── notification.py
    ├── models/
    └── db/
        ├── __init__.py
        ├── connection.py
        ├── migrations/
        └── queries/

**At this stage you also pick up sibling top-level directories:**

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── simple/            ← the package
    ├── tests/             ← pytest tree, mirrors simple/ shape
    │   ├── unit/
    │   └── integration/
    ├── scripts/           ← one-off ops scripts (NOT a package)
    │   ├── backfill_users.py
    │   └── dump_db.sh
    ├── docs/              ← arch notes, runbook
    └── ops/               ← Dockerfile, k8s manifests, terraform

**Why `tests/` is a sibling, not `simple/tests/`:** `pip install` will
not ship the test code into site-packages, and `pytest tests/` is a
clean, unambiguous command.

**Why `scripts/` has no `__init__.py`:** these are throwaway tools,
not part of the importable package. Productionized tools (long-lived
CLI subcommands like `simple backfill`) belong in `simple/cli/` instead,
wired through `[project.scripts]` in `pyproject.toml`.

### What does NOT change as you grow

These three rules hold from stage 0 to stage 3:

1. `main.py`, `config.py`, `core.py` stay at the package root. They are
   the load-bearing skeleton; don't bury them.
2. `control.sh`, `pyproject.toml`, `settings.yaml` stay at the project
   root.
3. The package name (`simple` → user's project name after generation)
   stays as the only directory under the project root that contains
   `__init__.py`.

If you ever feel the urge to break one of these, you're probably about
to make the project harder to read for someone joining it cold.
