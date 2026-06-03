# multi_p_g

[中文](README_zh.md)

A multi-process gRPC service template — N worker processes sharing a
port via `SO_REUSEPORT`, with cross-process logging, graceful drain,
and a working Ping/Echo example.

## When to use

- **Service-to-service inside a backend.** RPC over HTTP/2 + protobuf
  is the modern default for high-RPS internal traffic.
- **You need streaming.** gRPC has four kinds of streaming (unary,
  server-stream, client-stream, bidi) as first-class IDL constructs.
- **Cross-language clients.** Java / Go / Rust / TypeScript / C++ all
  speak the same protobuf wire format with comparable client quality.
- **You have gRPC infrastructure.** Envoy / Linkerd / Istio support
  gRPC as a first-class citizen — load balancing, retries, deadlines,
  tracing all configured at the mesh level.
- **Performance.** P99 typically a few ms vs HTTP/JSON's 10s of ms;
  protobuf serialization is much smaller on the wire than JSON.

## When NOT to use

| Need | Use |
|---|---|
| Public APIs (web/mobile/3rd party) | `multi_p_h` (HTTP) |
| Anyone-can-curl debug experience | `multi_p_h` |
| Long-running background workers, no inbound traffic | `multi_p` |
| Background workers consuming a queue | `multi_t_q` |
| No gRPC infrastructure to lean on | `multi_p_h` (lower friction) |
| Legacy Thrift integration mandated | `multi_p_t` |

## What you get

- **`pyproject.toml`** with `grpcio` + `protobuf` runtime; `grpcio-tools`
  installed via `pip install -e '.[dev]'` for codegen.
- **`proto/service.proto`** — sample IDL with a `PingService` (Ping +
  Echo). Edit this; rerun `./gen.sh`.
- **`gen.sh`** — wraps `python -m grpc_tools.protoc`, regenerates every
  `proto/*.proto` into `multi_p_g/pb/`, and patches the generated
  `*_pb2_grpc.py` to use a relative import.
- **Pre-generated `multi_p_g/pb/*.py`** committed to the template —
  `pip install -e .` immediately gives you a working server.
- **Multi-process server** with `SO_REUSEPORT` — N workers all bind the
  same port; the kernel load-balances connections across them. No
  external load balancer required for in-machine fan-out.
- **Cross-process logging** via `QueueHandler` + `QueueListener`
  (same pattern as `multi_p`): single log file, no interleaving.
- **Graceful drain** — workers call `server.stop(grace=N)` so in-flight
  RPCs finish before the worker exits.
- **Bounded shutdown** — parent gives each worker `grace + 5s` to exit,
  then `terminate()` then `kill()` as escalation.

## Install

    pip install -e .

For codegen (when you change anything under `proto/`):

    pip install -e '.[dev]'   # adds grpcio-tools
    ./gen.sh

## Run

    ./control.sh start
    ./control.sh status
    ./control.sh stop

Or directly:

    python -m multi_p_g.main

Try it from Python:

```python
import grpc
from multi_p_g.pb import service_pb2, service_pb2_grpc

with grpc.insecure_channel('127.0.0.1:50051') as ch:
    stub = service_pb2_grpc.PingServiceStub(ch)
    print(stub.Ping(service_pb2.PingRequest()).message)
    print(stub.Echo(service_pb2.EchoRequest(message="hello")).text)
```

Or with `grpcurl`:

    grpcurl -plaintext -d '{"message":"hello"}' \
      127.0.0.1:50051 service.PingService/Echo

(`grpcurl` needs server reflection enabled — see "gRPC-specific notes"
below for adding it.)

## Configuration

Adjust in `settings.yaml`:

    service:
      host: '[::]'              # listens on all interfaces, IPv4 + IPv6
      port: 50051
      workers: 4                # OS processes
      threads_per_worker: 10    # concurrent RPCs per worker
      shutdown_grace: 10        # seconds for in-flight RPCs to finish

## Where the work goes

**`multi_p_g/handler.py`** — replace the body of `Ping()` / `Echo()` and
add new methods matching your `.proto` service. The class signature
must extend `service_pb2_grpc.<YourService>Servicer`.

**`multi_p_g/core.py`** — worker pool orchestrator (`Multi_p_gService`)
and the worker entry point (`_worker_main`). Don't add business logic
here; this is plumbing.

**`multi_p_g/main.py`** — entry point + `QueueListener` setup. Most
edits here are to add lifecycle hooks (DB pool open/close, etc.) around
`Multi_p_gService.run()`.

When the .proto changes:

1. Edit `proto/service.proto` (or add new `proto/*.proto` files)
2. `./gen.sh` (regenerates everything in `multi_p_g/pb/`)
3. Update `handler.py` to implement any new RPCs

## The protobuf package vs the Python package

The sample `proto/service.proto` uses **`package service;`**, a generic
name that's intentionally different from the Python package
(`multi_p_g`). This is deliberate.

`pygen.sh` substitutes the literal string `multi_p_g` everywhere in the
template when generating a new project. The generated `*_pb2.py` files
contain a binary protobuf descriptor with **length prefixes** baked in
(e.g. `\x12\x07service` = "field 2, length 7, value 'service'"). If
the protobuf package were also `multi_p_g` (9 bytes), substituting it
to a different-length user name (e.g. `rpc_server`, 10 bytes) would
corrupt the length prefix and the descriptor would fail to parse.

Keeping the protobuf package as `service` avoids this entirely — the
binary descriptor never references the Python package name.

When you replace `proto/service.proto` with your own IDL, you can name
the protobuf package whatever you want; just keep that name unchanged
during a `pygen.sh` substitution scope (i.e. don't put your Python
project name into the protobuf package). The two namespaces are
genuinely independent in gRPC's wire format.

## Why SO_REUSEPORT instead of a load balancer

`SO_REUSEPORT` is a Linux/macOS socket option that lets multiple
processes bind the same port; the kernel hashes incoming connections
across the listeners. We pass it via gRPC's `grpc.so_reuseport=1`
option:

```python
server = grpc.server(
    futures.ThreadPoolExecutor(max_workers=10),
    options=[("grpc.so_reuseport", 1)],
)
server.add_insecure_port("[::]:50051")
```

For a single-machine N-worker fan-out this is the right level — no
sidecar, no userspace LB, kernel handles it.

For **production multi-machine** deployments you typically still want
Envoy / Linkerd / a service mesh in front: it gives you retry policies,
circuit breakers, mTLS, observability hooks. SO_REUSEPORT is the
within-host story; the mesh is the cross-host story. They compose.

## Project structure as it grows

The template ships at **stage 0**. As the service grows, evolve the
layout through these stages — don't pre-create empty directories at
stage 0 "just in case", and don't skip stages.

### Stage 0 — initial (≤ 5 modules)

What you get out of the generator. Note: this template starts with
*five* package modules instead of three because the gRPC handler is
genuinely separate from business logic, and the generated stubs live
in their own subpackage.

    multi_p_g/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── gen.sh
    ├── proto/
    │   └── service.proto      ← you edit this
    └── multi_p_g/
        ├── __init__.py
        ├── main.py            entry point + QueueListener
        ├── config.py          Dynaconf loader
        ├── core.py            worker pool orchestrator
        ├── handler.py         servicer implementation
        └── pb/                ← generated, committed (don't hand-edit)
            ├── __init__.py
            ├── service_pb2.py
            └── service_pb2_grpc.py

Two directories deserve their own folders from day one:

- **`proto/`** — IDL source. Human-edited. Add more `*.proto` files
  here as you grow.
- **`multi_p_g/pb/`** — generated stubs. `gen.sh` writes here; never
  edit by hand. Living inside the Python package means imports look
  like `from multi_p_g.pb import service_pb2`, which clearly says
  "generated stuff" at the import site.

### Stage 1 — small service (more RPCs, helpers)

Most growth at this stage is inside `handler.py` and a few helpers.
Still flat:

    multi_p_g/
    ├── __init__.py
    ├── main.py
    ├── core.py
    ├── config.py
    ├── handler.py             grows as you add RPCs
    ├── pb/
    │   ├── __init__.py
    │   ├── service_pb2.py
    │   └── service_pb2_grpc.py
    ├── auth.py                gRPC interceptor for auth metadata
    └── metrics.py             prometheus / statsd

### Stage 2 — multiple services / handlers / interceptors

When `handler.py` grows past ~300 lines, or you have multiple gRPC
services, split:

    project_root/
    ├── proto/                 multiple .proto files all live here
    │   ├── service.proto
    │   ├── billing.proto
    │   └── common.proto       shared types, imported by others
    └── multi_p_g/
        ├── __init__.py
        ├── main.py
        ├── config.py
        ├── core.py
        ├── pb/                generated for every .proto under proto/
        │   ├── __init__.py
        │   ├── service_pb2.py / service_pb2_grpc.py
        │   ├── billing_pb2.py / billing_pb2_grpc.py
        │   └── common_pb2.py
        ├── handlers/          one file per gRPC service
        │   ├── __init__.py
        │   ├── ping.py
        │   └── billing.py
        ├── interceptors/      authn, authz, logging, tracing
        │   ├── __init__.py
        │   └── auth.py
        ├── services/          business logic shared across handlers
        │   ├── __init__.py
        │   └── billing.py
        └── clients/           outbound API / DB clients
            ├── __init__.py
            └── stripe.py

`gen.sh` already iterates over `proto/*.proto`, so adding a new IDL
file is just dropping it in `proto/` and re-running.

### Stage 3 — large service (20+ modules)

Subpackages grow subpackages. Sibling top-level dirs:

    project_root/
    ├── pyproject.toml
    ├── settings.yaml
    ├── control.sh
    ├── gen.sh
    ├── proto/                IDL source (one file or many)
    │   ├── service.proto
    │   ├── billing.proto
    │   └── common.proto
    ├── multi_p_g/            Python package (with pb/ inside)
    ├── tests/                pytest tree, mirrors multi_p_g/
    │   ├── unit/
    │   └── integration/
    ├── scripts/              one-off ops scripts (NOT a package)
    ├── docs/                 arch notes, runbook
    └── ops/                  Dockerfile, k8s manifests, terraform

### What does NOT change as you grow

1. `main.py`, `config.py`, `core.py` stay at the package root.
2. `control.sh`, `pyproject.toml`, `settings.yaml`, `gen.sh` stay at
   the project root.
3. `proto/` lives at project root — IDL is project-level, not
   package-level.
4. `multi_p_g/pb/` lives inside the Python package — generated stubs
   need to be importable as `multi_p_g.pb.<x>`.
5. The Python package is the only directory under the project root
   with an `__init__.py`.

## gRPC-specific notes

- **Sync vs async servicer.** This template uses sync handlers
  (`def Ping(self, ...)`). Each worker has a `ThreadPoolExecutor`
  serving concurrent RPCs. For pure async, use `grpc.aio.server` and
  `async def` handlers — but be aware async gRPC is a separate code
  path; don't mix sync and async in one server.
- **Status codes are how you signal errors.** Don't raise random
  Python exceptions to the client — they become `INTERNAL`. Use
  `context.abort(grpc.StatusCode.NOT_FOUND, "user not found")` for
  expected failures. The full taxonomy: `OK`, `CANCELLED`, `UNKNOWN`,
  `INVALID_ARGUMENT`, `DEADLINE_EXCEEDED`, `NOT_FOUND`,
  `ALREADY_EXISTS`, `PERMISSION_DENIED`, `RESOURCE_EXHAUSTED`,
  `FAILED_PRECONDITION`, `ABORTED`, `OUT_OF_RANGE`, `UNIMPLEMENTED`,
  `INTERNAL`, `UNAVAILABLE`, `DATA_LOSS`, `UNAUTHENTICATED`.
- **Deadlines are client-driven.** Clients set a deadline; the server
  sees it via `context.time_remaining()`. Long-running handlers should
  check periodically and abort with `DEADLINE_EXCEEDED` if exceeded.
- **Streaming RPCs.** `rpc StreamLogs(LogRequest) returns (stream LogEntry);`
  generates a handler that yields entries:
  ```python
  def StreamLogs(self, request, context):
      for entry in tail_log():
          if not context.is_active(): return
          yield entry
  ```
- **Reflection for grpcurl.** Add `pip install grpcio-reflection` and
  in `_worker_main` after binding:
  ```python
  from grpc_reflection.v1alpha import reflection
  from multi_p_g.pb import service_pb2
  reflection.enable_server_reflection(
      [service_pb2.DESCRIPTOR.services_by_name["PingService"].full_name,
       reflection.SERVICE_NAME], server)
  ```
  Then `grpcurl -plaintext 127.0.0.1:50051 list` works without the
  client knowing the .proto.
- **TLS.** Switch `add_insecure_port` → `add_secure_port` with
  `grpc.ssl_server_credentials([(key_pem, cert_pem)])`. For mTLS, pass
  `root_certificates_pem` and set `require_client_auth=True`.
- **Workers don't share state.** Each worker is its own process. For
  cross-worker state (caches, counters, rate limit tokens), use Redis
  or another external store — same constraint as `multi_p`.
- **`grpc.so_reuseport=1`** is gRPC's way of passing the socket option
  through to its internal listener. If you instead want exactly one
  listener (e.g. for testing concurrency limits), use
  `("grpc.so_reuseport", 0)` and run a single worker.
- **Don't use `daemon=True` on workers.** This template uses default
  `daemon=False` so shutdown actually waits for clean drain. Daemon
  workers would die mid-RPC.
- **Long-running RPCs need `context.is_active()` checks.** If the
  client cancels, the server learns of it via `context.is_active()`
  returning False. Code that does long loops should check periodically
  and exit early; otherwise the worker stays busy on a phantom RPC.
