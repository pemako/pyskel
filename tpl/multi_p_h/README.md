# multi_p_h

[中文](README_zh.md)

A multi-process HTTP service template — FastAPI app served by uvicorn
in prefork mode (N worker processes sharing a port via `SO_REUSEPORT`).

## When to use

`multi_p_h` is the modern Python default for serving HTTP. Reach for
it when:

- **Public APIs** — anything mobile, web, or third-party clients hit.
- **Internal APIs you want anyone to debug** — `curl`, browsers,
  Postman all just work; no special client tooling.
- **REST / JSON services** — endpoint-per-resource fits naturally.
- **Mixed-language clients** — OpenAPI spec auto-generated from your
  pydantic models, clients in any language can codegen against it.
- **You don't have gRPC infrastructure** — and don't want to build it.

The hallmark: HTTP is the universal denominator for service-to-anything
communication, with the largest ecosystem of clients, tools, proxies,
and observability.

## When NOT to use

| Need | Use |
|---|---|
| Service-to-service inside backend, perf-critical | `multi_p_g` (gRPC) |
| First-class streaming RPCs | `multi_p_g` |
| Long-running background workers, no inbound traffic | `multi_p` |
| Background workers consuming a queue | `multi_t_q` |
| Single logical loop, no HTTP | `simple` |
| Talking to legacy Thrift infrastructure | `multi_p_t` |

If your service has no inbound HTTP traffic — it polls a queue, runs a
schedule, or processes a stream — picking `multi_p_h` adds a web server
you don't need. Use `multi_p` or `multi_t_q` instead.

## What you get

- **FastAPI** for routing + pydantic for request/response validation +
  auto-generated OpenAPI docs at `/docs` and `/redoc`.
- **uvicorn[standard]** with `uvloop` + `httptools` for the ASGI server
  (production-grade performance on Linux/macOS).
- **Prefork multi-process** — `uvicorn.run(workers=N)` forks N children
  that all bind the same socket via `SO_REUSEPORT`; the kernel
  load-balances incoming connections across workers. No code-level
  coordination needed.
- **Graceful shutdown** — uvicorn handles `SIGTERM` correctly: parent
  signals children, each child finishes in-flight requests, application
  shutdown hooks run, then the process exits.
- **`pyproject.toml`** (PEP 621), Python 3.12+. Single command to
  install: `pip install -e .`.

## Install

    pip install -e .

## Run

    ./control.sh start
    ./control.sh status
    ./control.sh stop

Or directly:

    python -m multi_p_h.main

Try it:

    curl http://127.0.0.1:8000/ping
    curl -X POST http://127.0.0.1:8000/echo \
      -H 'content-type: application/json' \
      -d '{"message":"hello"}'

Open `http://127.0.0.1:8000/docs` for the Swagger UI.

Configure host / port / workers in `settings.yaml`:

    service:
      host: 127.0.0.1   # 0.0.0.0 to expose externally
      port: 8000
      workers: 4

## Where the work goes

- **Routes** in `multi_p_h/main.py`. Add an `@app.get(...)` /
  `@app.post(...)` per endpoint; pydantic models declared there or
  imported from a `models/` subpackage.
- **Business logic** in `multi_p_h/core.py` (`Service` class). Routes
  call `service.method(...)` — keep handlers thin.
- **Settings** read via `multi_p_h/config.py` (Dynaconf). Workers
  initialize from the same `settings.yaml`; environment overrides via
  `DYNACONF_SERVICE__PORT=8080` etc.

## Why multi-process is uvicorn's job, not ours

Other templates here (`multi_p`, `multi_t`) hand-roll a worker pool with
`multiprocessing.Process` or `threading.Thread`, manage their lifecycle,
do cross-process logging via `QueueListener`, and shut down with
`mp.Event` + `terminate()` fallbacks.

`multi_p_h` doesn't, because **uvicorn already does all of that for HTTP
servers and does it better**:

1. **Prefork model** — uvicorn binds the listening socket in the parent
   and forks workers that inherit it. Each worker accepts on the same
   port; the kernel (Linux/macOS with `SO_REUSEPORT`) does fair
   load-balancing across processes. No `mp.Queue`, no shared sockets
   to pass via `multiprocessing.reduction`.
2. **Signal handling and graceful shutdown** are first-class: `SIGTERM`
   to the parent → parent signals children → each child stops accepting
   new connections, finishes in-flight requests, runs `lifespan`
   shutdown hooks, exits cleanly. Reproducing this correctly is more
   code than the rest of the template combined.
3. **Reload / hot-restart** for development is a `--reload` flag.
4. **Production deployment** = the same binary; you swap the
   process manager (systemd, k8s, gunicorn-as-supervisor) but uvicorn
   itself stays.

The template's job is to teach the right pattern, not invent a worse
version of one that already exists in `pip install`-able form.

## Why logs go to stdout, not a file

The other multi-process template (`multi_p`) configures a
`TimedRotatingFileHandler` and pipes records through `QueueListener`
to avoid N processes racing on the same log file.

`multi_p_h` deliberately doesn't do this. **All log handlers are
`StreamHandler` to stdout**, and `control.sh` redirects stdout/stderr
to `logs/multi_p_h.out` and `logs/multi_p_h.err`.

Why:

1. POSIX guarantees writes ≤ `PIPE_BUF` (typically 4096 bytes) to a
   shared fd are atomic. With workers writing single log lines, you
   get correct interleaving without locks.
2. **Production HTTP services don't rotate their own logs.** `logrotate`
   (system-level) or k8s log collection (sidecar / fluent-bit /
   vector) is the right place for that — it's process-aware in a way
   Python's stdlib handlers cannot be.
3. Adding `QueueListener` machinery to a template whose worker manager
   is uvicorn (already a separate process tree) means redoing what
   uvicorn doesn't know about. Cross-process logging via files is
   either OS-level (this template) or external (a log shipper). Both
   are right; in-process queue is wrong here.

If you need `multi_p_h.log` rotated automatically:

- **Linux:** add a `/etc/logrotate.d/multi_p_h` config that touches the
  redirected file.
- **k8s:** stdout already goes to `kubectl logs` — no rotation needed
  in the container.
- **Single-machine dev:** rotate manually or use a tool like
  `multilog` from daemontools.

## Project structure as it grows

The template ships at **stage 0**. Evolve through stages — don't pre-
create empty directories at stage 0 "just in case", and don't skip
stages.

### Stage 0 — initial (≤ 3 modules)

What you get out of the generator:

    multi_p_h/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    └── multi_p_h/
        ├── __init__.py
        ├── main.py        FastAPI app + routes + run()
        ├── config.py      Dynaconf loader
        └── core.py        Service business logic

**Rule at this stage:** routes, pydantic models, and Service stay flat.
Don't introduce subpackages until you have a real reason.

### Stage 1 — small service (5–8 modules, still flat)

A few helpers added. Still flat:

    multi_p_h/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── auth.py
    ├── client.py
    └── metrics.py

### Stage 2 — by-concern subpackages (8–20 modules)

Pull routes into a `routers/` subpackage, models into `models/`. Keep
`main.py`, `config.py`, `core.py` at the top.

    multi_p_h/
    ├── __init__.py
    ├── main.py            app definition + router mount points
    ├── config.py
    ├── core.py            shared business logic
    ├── routers/           one router per resource (FastAPI APIRouter)
    │   ├── __init__.py
    │   ├── users.py
    │   └── billing.py
    ├── models/            pydantic schemas
    │   ├── __init__.py
    │   ├── user.py
    │   └── invoice.py
    ├── services/          business logic per concern
    │   ├── __init__.py
    │   └── billing.py
    └── clients/           outbound API / DB clients
        ├── __init__.py
        └── stripe.py

`main.py` becomes the wiring layer:

```python
from fastapi import FastAPI
from multi_p_h.routers import users, billing

app = FastAPI()
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(billing.router, prefix="/billing", tags=["billing"])
```

### Stage 3 — large service (20+ modules)

Subpackages grow subpackages. Add sibling top-level dirs:

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── multi_p_h/         the package
    ├── tests/             pytest tree, mirrors multi_p_h/
    │   ├── unit/
    │   └── integration/
    ├── scripts/           one-off ops scripts (NOT a package)
    ├── docs/              arch notes, runbook
    └── ops/               Dockerfile, k8s manifests, terraform

### What does NOT change as you grow

These three rules hold from stage 0 to stage 3:

1. `main.py`, `config.py`, `core.py` stay at the package root.
2. `control.sh`, `pyproject.toml`, `settings.yaml` stay at the project
   root.
3. The package is the only directory under the project root with an
   `__init__.py`.

## HTTP-specific notes

- **`workers=1` for development.** Multi-worker mode disables
  auto-reload. For dev, edit `settings.yaml` to `workers: 1` and run
  `python -m multi_p_h.main` (or use `uvicorn --reload` directly).
- **Workers don't share state.** Each worker is a separate process with
  its own memory. In-process caches, rate limiters, etc. are per-worker.
  For shared state across workers, use Redis / a database / sticky
  routing — exactly the same constraint as `multi_p`.
- **Lifespan for startup/shutdown hooks.** Use FastAPI's `lifespan`
  context manager (`@asynccontextmanager async def lifespan(app)`) for
  per-worker setup/teardown — opening a DB pool, warming a cache,
  closing connections gracefully.
- **`workers > 1` + `--reload` is incompatible.** uvicorn refuses to
  run them together. Pick one mode.
- **Prefork on macOS works** but `SO_REUSEPORT` semantics differ from
  Linux's: macOS allows multiple binds, but load balancing is less
  fair. Fine for development; for production, deploy on Linux.
- **Don't put long-running CPU work in route handlers.** FastAPI is
  async-friendly; blocking work blocks the event loop in that worker.
  Either run the handler `def` (sync, runs in thread pool) or offload
  to a background queue / a `multi_p_q`-style worker pool.
- **TLS / auth / CORS are your responsibility.** Don't expose
  `multi_p_h` directly to the public internet without a reverse proxy
  (nginx, Caddy, Cloudflare) handling TLS termination, rate limiting,
  and DoS protection. uvicorn can do TLS, but a dedicated proxy is
  better at it.
