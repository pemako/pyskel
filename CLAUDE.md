# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A bash scaffolder for Python 3.12 service templates. The deliverable is `./pyskel` (the generator script at the repo root) plus the seven templates under `tpl/`. There is no Python application here — every directory under `tpl/<template>/` is a complete project that the generator copies and rewrites for the user.

There is no `templates/` directory and no `pygen.sh` — both were the previous generation. The current generator is `./pyskel` and the templates live under `tpl/`.

## Layout

```
.
├── pyskel                the generator (pure bash 4+, executable)
├── tpl/
│   ├── simple/           single-process loop
│   ├── multi_t/          N threads, no shared queue
│   ├── multi_t_q/        producer/consumer + retry + durable replay
│   ├── multi_p/          multi-process workers (mp.Process pool)
│   ├── multi_p_h/        FastAPI + uvicorn (HTTP)
│   ├── multi_p_g/        grpcio + protobuf (gRPC)
│   └── multi_p_t/        Apache Thrift
├── README.md / README_zh.md
├── CLAUDE.md (this file)
└── LICENSE
```

Each template has the same shape:

```
tpl/<name>/
├── pyproject.toml         PEP 621, Python 3.12+
├── settings.yaml          Dynaconf + stdlib logging dictConfig
├── control.sh             start/stop/restart/status, pid file in logs/
├── README.md / README_zh.md
├── .gitignore / .editorconfig
└── <name>/                inner Python package
    ├── __init__.py
    ├── main.py            entry: argparse, logging, Service.run()
    ├── config.py          Dynaconf loader
    └── core.py            Service class
```

Network templates (`multi_p_h`, `multi_p_g`, `multi_p_t`) add `handler.py`. RPC templates add `proto/` (project root) and `<pkg>/pb/` (inside the package). `multi_t_q` adds `tasks.py`.

## Common commands

There is no test suite, lint task, or build step in this repo. Validation is end-to-end: generate a project from a template, install, run, exercise it, stop it.

```bash
# List templates
./pyskel --list

# Generate a project (interactive)
./pyskel
# or non-interactive
./pyskel simple my_service

# Generate + install + run a single template (smoke test)
cd /tmp && rm -rf demo && mkdir demo && cd demo
/path/to/repo/pyskel simple my_service
cd my_service
python3 -m venv .venv
.venv/bin/pip install -q -e .
PATH=".venv/bin:$PATH" ./control.sh start
PATH=".venv/bin:$PATH" ./control.sh status
PATH=".venv/bin:$PATH" ./control.sh stop
```

Network templates need their respective ports free during testing: `multi_p_h:8000`, `multi_p_g:50051`, `multi_p_t:9090`.

## How `./pyskel` works

The generator uses **only POSIX essentials and bash 4+ builtins** — no `gsed`, no `rename`, no `tput`, no platform branches. Reads each template file via `cat ...; printf x` (the trick that preserves trailing newlines through command substitution), substitutes via `${content//$from/$to}`, writes back. Capitalization for class names uses `${var^}` (first-letter-uppercase, bash 4+).

Two literal substitutions per file:

- `<template>` → `<name>` (e.g. `multi_p_g` → `my_rpc`)
- `<Template>` → `<Name>` (e.g. `Multi_p_gService` → `My_rpcService`)

The CamelCase form is just the first letter capitalized; underscores are not word boundaries in Python identifiers, so `multi_p_g` becomes `Multi_p_g` not `MultiPG`. Class names in templates follow this pattern; preserve it.

Files inside `__pycache__/` and `*.pyc` files are skipped (their NUL bytes don't survive bash variable round-trip). `logs/` and `data/` directories are wiped after copy — runtime state shouldn't bleed into a generated project.

The script lives at the repo root and resolves templates via `$SCRIPT_DIR/tpl/`. Output goes to `$PWD`. So `pyskel` runs from any CWD.

## Cross-template conventions

These hold across all seven templates. If you're modifying one, keep these consistent:

- **`control.sh` lives at the project root**, not in `scripts/`. It uses `kill -0 $pid` for portable process-existence checks, `python3 -m <pkg>.main` to start (not direct script invocation), and bounded shutdown (TERM → wait → KILL).
- **`pyproject.toml`** uses PEP 621, `requires-python = ">=3.12"`, `[project.scripts]` entry point, single dep `dynaconf>=3.2` (plus framework-specific deps for network templates).
- **`settings.yaml`** has `service:` for app config, `logs:` for stdlib `logging.config.dictConfig` input. The dictConfig always sets `disable_existing_loggers: false` (to avoid disabling library loggers loaded before dictConfig runs), `propagate: false` on the app logger (to prevent duplicate writes), `encoding: utf-8` on file handlers.
- **`from <pkg>.config import settings`** — never `from config import settings`. All imports are full-package paths so `python3 -m <pkg>.main` works regardless of CWD.
- **Bounded shutdown** — every Service class has a 30s deadline on `join()`, with logging on threads/processes that don't exit, and (multi-process only) `terminate()` → `kill()` escalation.
- **Cross-process logging via `QueueHandler` + `QueueListener`** — used in `multi_p`, `multi_p_g`, `multi_p_t`. The parent's `dictConfig` installs file/console handlers; `_start_log_listener` peels them off and wraps them in a QueueListener; children install a `QueueHandler` on their root logger via `_init_child_logging`. This is the [Python logging cookbook recipe](https://docs.python.org/3/howto/logging-cookbook.html#logging-to-a-single-file-from-multiple-processes); same shape across all three templates. `multi_p_h` deliberately doesn't do this — uvicorn manages the worker processes and we let stdout interleaving (POSIX-atomic for short writes) handle merging.

## Per-template specifics worth knowing

- **`multi_p`**: parent waits on `mp.Event` via `time.sleep(0.5)` polling, **not** `Event.wait()`. On macOS the C-level `sem_wait` inside `mp.Event.wait` swallows EINTR and signal handlers don't get to run — `time.sleep` does (PEP 475). The `multi_t` template uses `threading.Event.wait()` directly because that one's implemented in pure Python and works correctly. **Don't simplify `multi_p`'s loop to `self._stop.wait()` — this regresses signal handling.**

- **`multi_p_g`**: `proto/service.proto` deliberately uses `package service;` (not `package multi_p_g;`) because protobuf's binary descriptor has length prefixes baked in — substituting `multi_p_g` (9 bytes) → user's project name (different length) corrupts the descriptor. Keeping the protobuf package name as a literal that `pyskel` doesn't touch keeps the binary self-consistent.

- **`multi_p_t`**: two custom subclasses in `core.py` work around Apache Thrift Python's gaps:
  - `_ReusePortServerSocket` — Thrift's stock `TServerSocket` only sets `SO_REUSEADDR`, so multi-worker binding to one port fails. Override `listen()` to add `SO_REUSEPORT` before `bind()`.
  - `_StoppableThriftServer` — Thrift's `TThreadedServer.serve()` has a blanket `except Exception` that swallows the OSError from closing the listening socket; without an explicit `_stopped` flag, `serve()` re-accepts forever and the worker hangs.
    Also: the Thrift namespace is `tsvc` (not `service` — that's a Thrift IDL reserved word, parser fails) and not `multi_p_t` (so it survives `pyskel` substitution unchanged).

- **`multi_p_h`**: doesn't have a `core.py`-level worker pool. uvicorn manages multi-process via `--workers N` / `uvicorn.run(workers=...)`. We just define `app = FastAPI()` and call `uvicorn.run("multi_p_h.main:app", workers=N)`. The string-based app reference is required so workers can re-import the module after fork/spawn.

- **`multi_t_q`**: `tasks.py` has a `Task` dataclass and `TaskProcessor` base class. The Service class in `core.py` runs one producer thread + N worker threads. On clean shutdown it pickles `task_queue + failed_queue` to `data/todo.pickle`; on next start `_load_todo()` reloads them. `task.attempts` is part of the dataclass so retries persist across restarts. Pickle is the format because it preserves arbitrary Task subclasses.

- **`multi_p_g` and `multi_p_t`** both use the same layout: `proto/<name>.thrift|.proto` at project root, generated stubs in `<pkg>/pb/`. Both have a `gen.sh` that regenerates from IDL and patches the generator's bare imports to relative ones (Apache Thrift and protoc both emit `import service_pb2` style absolute imports that don't resolve when the generated tree is nested inside a package).

## Pitfalls when editing templates

- **Don't run `control.sh start` with the template directory as CWD.** It writes `logs/` into the template tree. The first version of the generator choked because a template's `logs/multi_p_t.out` had grown to 355 MB and substitution took forever. The current generator deletes `logs/` and `data/` after copy, but that doesn't help if you accidentally commit them. Smoke test in `/tmp/`.

- **Don't import a template package from inside the template directory.** Python writes `__pycache__/*.pyc` next to the source on import; pyc files have NUL bytes that bash variables don't preserve. The generator skips `*.pyc`/`__pycache__` defensively, but committed pyc files in the template are still bad hygiene.

- **Anywhere a template uses its own template name as an identifier or class prefix, it's intentional** — those tokens are `pyskel`'s substitution targets. Don't rename them to something "cleaner"; they must match the template directory name exactly.

- **`pyproject.toml`'s `[project.scripts]` entry is `<template> = "<template>.main:main"`** — both halves get substituted to `<user_name> = "<user_name>.main:main"` at generation time. Keep them aligned.

- **Don't change the protobuf package name in `multi_p_g/proto/service.proto`** unless you also handle the byte-length corruption. The template's choice of `package service;` is load-bearing.

- **Adding a new template:** subdirectory under `tpl/`, must have `<name>/<name>/` inner package, must have `pyproject.toml` whose `name` field matches the directory. `./pyskel --list` will pick it up automatically by scanning `tpl/*/`.

## What's NOT in this repo

- **Tests**, **CI config**, **linting** — none configured. Smoke tests are end-to-end manual: generate, install, run, stop, inspect logs.
- **Hooks**, **pre-commit configs** — none.
- **Generated `_pb2*.py` regen at install time** — no. We commit pre-generated stubs in `multi_p_g/multi_p_g/pb/` and `multi_p_t/multi_p_t/pb/tsvc/` so `pip install -e .` immediately produces a runnable service. Users regenerate manually via `./gen.sh` after editing IDL.
