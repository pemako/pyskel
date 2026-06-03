# multi_p_t

[中文](README_zh.md)

A multi-process **Apache Thrift** service template — N worker processes
sharing a port via `SO_REUSEPORT`, with cross-process logging, an
explicit stop flag (Apache Thrift Python's `TServer.stop()` is broken),
and a working `Ping`/`Echo` example.

## When to use

Reach for `multi_p_t` when:

- **You're integrating with an existing Thrift ecosystem** —
  cross-language services where Thrift is already the contract:
  HBase Thrift gateway, Hive, Cassandra (legacy), Scribe, Facebook /
  Meta-style RPC, internal company services that predate gRPC.
- **You need cross-language wire compatibility with strong static
  guarantees.** Apache Thrift has been the protocol-of-record across
  Java / C++ / Python / Go / PHP / Ruby for over a decade; client
  quality across languages is at parity.
- **Generated code with `__slots__`-friendly Python classes** is
  acceptable as the API surface (see "Why Apache Thrift over thriftpy2"
  below).

## When NOT to use

| Need | Use |
|---|---|
| New service in 2026 with no Thrift constraint | `multi_p_g` (gRPC) — modern equivalent |
| Public API with browser/curl-friendly debug | `multi_p_h` (HTTP) |
| No Thrift CLI on developer / CI machines | `multi_p_g` — gRPC's `grpcio-tools` is `pip install`able |
| Long-running background workers, no inbound traffic | `multi_p` |
| Producer/consumer queue with retries | `multi_t_q` |
| Single logical loop, no concurrency | `simple` |

**For brand-new services in 2026, gRPC (`multi_p_g`) is almost always
the better choice** — same wire-protocol-driven design, same
multi-language story, but the tooling (`grpcurl`, Envoy / Istio
integration, `grpcio` install) is much smoother. Pick this template
only when external constraints force Thrift.

## What you get

- **`pyproject.toml`** with `thrift` + `dynaconf` runtime; **the
  `thrift` CLI compiler is a separate system dependency** (Homebrew /
  apt).
- **`proto/service.thrift`** — sample IDL with a `PingService`
  (Ping + Echo). Edit this; rerun `./gen.sh`.
- **`gen.sh`** — wraps `thrift --gen py:slots`, regenerates every
  `proto/*.thrift` into `multi_p_t/pb/`, deletes the `*-remote` CLI
  client scripts Thrift emits, and patches imports for relative
  resolution within the package.
- **Pre-generated `multi_p_t/pb/tsvc/*.py`** committed to the template —
  `pip install -e .` immediately gives you a working server.
- **Multi-process server** with `SO_REUSEPORT` — N workers all bind
  the same port; the kernel load-balances connections across them.
- **`_StoppableThriftServer`** subclass — Apache Thrift Python's stock
  `TThreadedServer` swallows the `OSError` you'd get from closing the
  listening socket and keeps re-accepting forever, so we maintain an
  explicit `_stopped` flag and break out of the accept loop when set.
- **Cross-process logging** via `QueueHandler` + `QueueListener`:
  single log file, no interleaving.
- **Bounded shutdown** — parent gives each worker `grace + 5s` to
  exit, then `terminate()` then `kill()` as escalation.

## Install Apache Thrift CLI

The `thrift` Python pip package gives you the runtime, but **codegen
needs the `thrift` compiler binary** — installed separately:

    # macOS
    brew install thrift

    # Debian / Ubuntu
    sudo apt install thrift-compiler

    # Verify
    thrift --version

If you skip this, `./gen.sh` errors out with a clear message; the
template still installs and runs because `multi_p_t/pb/` is committed
pre-built.

## Install Python deps

    pip install -e .

## Run

    ./control.sh start
    ./control.sh status
    ./control.sh stop

Or directly:

    python -m multi_p_t.main

Try it from Python:

```python
from thrift.transport import TSocket, TTransport
from thrift.protocol import TBinaryProtocol
from multi_p_t.pb.tsvc import PingService

t = TSocket.TSocket('127.0.0.1', 9090)
t = TTransport.TBufferedTransport(t)
prot = TBinaryProtocol.TBinaryProtocol(t)
client = PingService.Client(prot)
t.open()
print(client.Ping())                  # "pong"
print(client.Echo('hello'))           # "hello"
t.close()
```

## Configuration

Adjust in `settings.yaml`:

    service:
      host: 127.0.0.1            # 0.0.0.0 to expose externally
      port: 9090
      workers: 4                 # OS processes
      threads_per_worker: 32     # max concurrent connections per worker
      shutdown_grace: 10         # seconds for in-flight RPCs to finish

## Where the work goes

**`multi_p_t/handler.py`** — replace `Ping()` / `Echo()` and add new
methods matching your `.thrift` IDL. Method signatures must match
exactly: argument names, types, and return types.

**`multi_p_t/core.py`** — worker pool orchestrator (`Multi_p_tService`),
worker entry point (`_worker_main`), and the two custom subclasses
(`_ReusePortServerSocket`, `_StoppableThriftServer`) that work around
Apache Thrift Python's gaps. Don't add business logic here; this is
plumbing.

**`multi_p_t/main.py`** — entry point + `QueueListener` setup. Most
edits here are to add lifecycle hooks (DB pool open/close, etc.)
around `Multi_p_tService.run()`.

When the .thrift changes:

1. Edit `proto/service.thrift` (or add new `proto/*.thrift` files)
2. `./gen.sh` (regenerates `multi_p_t/pb/` from scratch)
3. Update `handler.py` to implement any new RPCs

## The Thrift namespace vs the Python package

The sample `proto/service.thrift` uses **`namespace py tsvc`**, an
intentionally non-keyword, non-Python-package name. This matters for
two reasons:

1. **`service` is a reserved word** in the Thrift IDL grammar — using
   it as a namespace produces a syntax error during codegen.
2. **`pygen.sh` substitutes the literal string `multi_p_t` everywhere
   in the template** when generating a new project. Apache Thrift's
   generated Python is text-substitution-friendly (no binary
   descriptors like protobuf has) but the namespace would still need
   to stay aligned across the .thrift file, the directory layout
   under `multi_p_t/pb/`, and import statements. Decoupling the
   Thrift namespace from the Python package name keeps it stable
   across renames.

When you replace the IDL with your own, you can name the Thrift
namespace whatever you want (avoiding Thrift keywords). The Python
import path (`multi_p_t.pb.tsvc`) tracks the namespace string, so
update gen.sh's import-patch step if you change it.

## Why Apache Thrift over thriftpy2

The Python ecosystem also has `thriftpy2` — a pure-Python
implementation that loads `.thrift` files at runtime and skips
codegen. **For a "minimal friction" template, thriftpy2 wins; for
this template we picked Apache.** The reasoning:

- Apache Thrift is the **reference implementation**. Cross-language
  wire-format quirks (recursive types, certain compact-protocol edge
  cases, JSON protocol details) match the spec by construction.
- **Static type tooling.** Generated `_pb.py` files are real Python
  modules with class definitions, so mypy / pyright / IDE
  autocomplete all work. `thriftpy2`'s dynamically-loaded classes
  don't surface to static checkers.
- **Production hardening.** Apache Thrift Python has been deployed
  at Facebook, Twitter (historical), and other large orgs at
  multi-billion-RPS scale. `thriftpy2` is solid but smaller-scale.

The cost: Apache Thrift Python is **less actively maintained** in
recent years (the project's energy is in C++ / Java); the codegen
step requires a system-package CLI; the generated code style is
dated (no type hints, Python 2-era patterns).

If those costs hurt more than the benefits help, swap to `thriftpy2`
— the .thrift IDL stays the same, only `gen.sh` and a few imports
change.

## Why SO_REUSEPORT and the custom server socket

`SO_REUSEPORT` is the socket option that lets multiple processes bind
the same port; the kernel hashes incoming connections across the
listeners. Apache Thrift's stock `TServerSocket` only sets
`SO_REUSEADDR`, so multi-process binding fails with `EADDRINUSE`.

`_ReusePortServerSocket` (in `core.py`) is a small subclass that
re-implements `TServerSocket.listen()` to set `SO_REUSEPORT` before
`bind()`. It's ~15 lines, mirrors what gRPC's `so_reuseport` option
does internally, and works on Linux + macOS (Windows lacks
`SO_REUSEPORT`).

For **production multi-machine** deployments you typically still want
a service mesh (Envoy / Linkerd) or a dedicated Thrift LB in front
for cross-host load balancing, retries, and observability.
SO_REUSEPORT is the within-host story.

## Why `_StoppableThriftServer` and the explicit stop flag

Apache Thrift's `TThreadedServer.serve()` looks like this:

```python
def serve(self):
    self.serverTransport.listen()
    while True:
        try:
            client = self.serverTransport.accept()
            ...
        except KeyboardInterrupt:
            raise
        except Exception:
            logging.exception("Error occurred ...")
```

The blanket `except Exception` swallows the `OSError` that
`accept()` raises when the listening socket is closed during
shutdown — so `serve()` keeps re-accepting forever. There's no
clean way to stop it without monkey-patching or subclassing.

`_StoppableThriftServer` (in `core.py`) adds:
- A `_stopped` flag.
- An override of `serve()` that breaks out of the loop when the
  flag is set.
- A `stop()` method that flips the flag.

The shutdown sequence in `_worker_main`:

1. `server.stop()` → flag flipped, exception in `accept()` will be
   treated as graceful exit.
2. Close the listen socket → `accept()` unblocks with OSError.
3. `serve_thread.join(timeout=grace)` → thread exits cleanly.

Without the flag, step 2 would just trigger the swallow-and-retry
loop and the worker would hang until terminate().

## Project structure as it grows

The template ships at **stage 0**. Evolve through stages — don't
pre-create empty directories, and don't skip stages.

### Stage 0 — initial (≤ 5 modules)

What you get out of the generator. Note: this template starts with
*five* package modules and a separate `proto/` directory because:
- The handler is genuinely separate from business logic.
- Generated stubs need their own subpackage (Apache Thrift produces
  a 3-file tree per .thrift namespace).
- IDL source is project-level, not package-level.

    multi_p_t/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── gen.sh
    ├── proto/
    │   └── service.thrift     ← you edit this
    └── multi_p_t/
        ├── __init__.py
        ├── main.py            entry point + QueueListener
        ├── config.py          Dynaconf loader
        ├── core.py            worker pool + custom server subclasses
        ├── handler.py         Iface implementation
        └── pb/                ← generated, committed (don't hand-edit)
            ├── __init__.py
            └── tsvc/
                ├── __init__.py
                ├── PingService.py
                ├── ttypes.py
                └── constants.py

### Stage 1 — small service (more methods, helpers)

Most growth at this stage is inside `handler.py`:

    multi_p_t/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── handler.py             grows as you add methods
    ├── pb/
    │   └── tsvc/...
    ├── auth.py                inspect Thrift headers / metadata
    └── metrics.py             prometheus / statsd

### Stage 2 — multiple services / handlers

When `handler.py` exceeds ~300 lines, or you have multiple Thrift
services, split:

    project_root/
    ├── proto/
    │   ├── ping.thrift        each service in its own .thrift
    │   ├── billing.thrift
    │   └── common.thrift      shared structs, included by others
    └── multi_p_t/
        ├── __init__.py
        ├── main.py
        ├── config.py
        ├── core.py
        ├── pb/
        │   ├── __init__.py
        │   ├── ping/...       generated for ping.thrift
        │   ├── billing/...    generated for billing.thrift
        │   └── common/...
        ├── handlers/          one file per Thrift service
        │   ├── __init__.py
        │   ├── ping.py
        │   └── billing.py
        ├── services/          shared business logic
        │   ├── __init__.py
        │   └── billing.py
        └── clients/           outbound API / DB clients

`gen.sh` already iterates over `proto/*.thrift`, so adding a new
.thrift is just dropping the file in `proto/` and re-running. `core.py`
needs to register multiple processors — Apache Thrift has
`TMultiplexedProcessor` for serving multiple services on one port.

### Stage 3 — large service (20+ modules)

Subpackages grow subpackages. Sibling top-level dirs:

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── gen.sh
    ├── proto/
    ├── multi_p_t/
    ├── tests/                 pytest tree
    │   ├── unit/
    │   └── integration/
    ├── scripts/               one-off ops scripts (NOT a package)
    ├── docs/                  arch notes, runbook
    └── ops/                   Dockerfile, k8s manifests, terraform

### What does NOT change as you grow

1. `main.py`, `config.py`, `core.py` stay at the package root.
2. `control.sh`, `pyproject.toml`, `settings.yaml`, `gen.sh` stay
   at the project root.
3. `proto/` lives at project root — IDL is project-level.
4. `multi_p_t/pb/` lives inside the Python package — generated
   stubs need to be importable as `multi_p_t.pb.<x>`.
5. The Python package is the only directory under the project root
   with an `__init__.py`.

## Apache Thrift-specific notes

- **`thrift` CLI version skew is real.** Generated code from one
  version may not be compatible with the runtime from another.
  Always commit the CLI version somewhere observable (Dockerfile,
  README, Makefile target) and CI-check `thrift --version &&
  ./gen.sh && git diff --exit-code` to catch drift.
- **`thrift` Python package is dated.** No type hints in generated
  code; method signatures follow the IDL but mypy can't verify them
  without stubs you'd have to write. The stubs you generate
  *do* surface to autocomplete (functions are real), just no typing.
- **Async support is poor.** Apache Thrift Python has Twisted-flavored
  `TTwisted` and `TNonblockingServer`, both dated. For real asyncio
  Thrift, look at third-party libraries or accept thread-per-connection.
- **Thrift exceptions cross the wire.** Define them in the .thrift
  IDL (`exception MyError { 1: string message }`) — they're sent
  back to the client as typed errors. Throwing arbitrary Python
  exceptions from the handler results in a generic
  `TApplicationException`.
- **`TBufferedTransport` is required on both ends.** It's the
  default in this template. Mixing buffered and unbuffered
  transports between client and server produces silent hangs.
- **Workers don't share state.** Each worker is its own process —
  same constraint as `multi_p` / `multi_p_g`. Use Redis or a DB
  for cross-worker state.
- **Don't use `daemon=True` on the worker pool processes.** This
  template uses `daemon=False` so shutdown waits for clean drain.
  But the *connection threads* inside each worker are
  `daemon=True` — Apache Thrift Python's drain story is
  imperfect (threads serving in-flight RPCs may be cut short on
  process exit). Document this in your runbook if your RPCs are
  long-running.
- **Long-running RPCs need their own cancellation story.** Apache
  Thrift has no client-deadline propagation analogue to gRPC's
  context. If a client disconnects mid-RPC, the server doesn't
  know — it'll keep computing. Bake check-points into long
  handlers and handle disconnection at the transport layer if
  it matters.
- **TLS** — pass an `ssl_context` to `TSSLServerSocket` instead of
  the plain `TServerSocket`. Adapt `_ReusePortServerSocket` to
  inherit from `TSSLServerSocket` if you need SO_REUSEPORT + TLS.
