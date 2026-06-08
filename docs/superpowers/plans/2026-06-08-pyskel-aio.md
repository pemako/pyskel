# PR 1: `aio` Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `aio` template to pyskel — an asyncio-first single-process Python service skeleton — and wire it into the e2e test suite.

**Architecture:** New `tpl/aio/` directory mirroring the shape of existing templates (`tpl/simple/` is the closest reference). Inner package `aio/` contains `__init__.py`, `config.py`, `core.py`, `main.py`. `Service.run()` is `async`, uses `asyncio.TaskGroup` to launch N worker coroutines, and uses `loop.add_signal_handler` for SIGTERM/SIGINT. Stop is requested via `Service.request_stop()` (sets an `asyncio.Event`); the same method is used by both signal handler and tests.

**Tech Stack:** Python 3.12+, `asyncio`, `dynaconf>=3.2`. No new framework dependencies.

**Spec reference:** `docs/superpowers/specs/2026-06-08-pyskel-aio-mq-design.md` §2.

---

## File Structure (new files only)

```
tpl/aio/
├── pyproject.toml
├── settings.yaml
├── control.sh
├── README.md
├── README_zh.md
├── .gitignore
├── .editorconfig
└── aio/
    ├── __init__.py
    ├── config.py
    ├── core.py
    └── main.py
```

Modified files:
- `tests/e2e.sh` — add `run_aio` function and call site

Each new file has one responsibility. The inner `aio/` package mirrors `tpl/simple/simple/`: `core.py` holds the `Service` class, `main.py` is the entry point, `config.py` loads Dynaconf, `__init__.py` exposes `__version__`.

---

## Task 1: Add failing e2e branch for `aio`

**Files:**
- Modify: `tests/e2e.sh:189-198` (add `run_aio` call) and add `run_aio` function after `run_multi_p_t`

- [ ] **Step 1.1: Add `run_aio` function to `tests/e2e.sh`**

Insert after the existing `run_multi_p_t()` function (currently ends at line 187), before the `echo "${Y}pyskel e2e suite${N}"` line at line 189:

```bash
run_aio() {
  local n=aio gen=aio_test
  prepare "$n" "$gen"
  PATH=".venv/bin:$PATH" ./control.sh start >/dev/null
  sleep 2
  local ticks=$(grep -c "tick" "logs/$gen.log" 2>/dev/null || echo 0)
  PATH=".venv/bin:$PATH" ./control.sh stop >/dev/null
  sleep 0.5
  if ! grep -q "service stopped" "logs/$gen.log" 2>/dev/null; then
    check "$n" FAIL "no clean stop log"; return
  fi
  check "$n" OK "$ticks ticks across workers, clean stop"
}
```

- [ ] **Step 1.2: Wire the call into the suite runner**

Add `run_aio` after `run_multi_p_t` in the call sequence:

```bash
run_simple
run_multi_t
run_multi_t_q
run_multi_p
run_multi_p_h
run_multi_p_g
run_multi_p_t
run_aio
```

- [ ] **Step 1.3: Run e2e to confirm `aio` fails**

Run: `bash tests/e2e.sh`

Expected: 7 templates pass, then `aio` fails because `tpl/aio/` does not exist. The `prepare` helper will hit "ERROR: $PYSKEL: tpl/aio not found" or similar, and the `check "$n" FAIL` branch fires. This is our RED state — confirms the test would catch a missing template.

- [ ] **Step 1.4: Commit the failing test**

```bash
git add tests/e2e.sh
git commit -m "test: add e2e branch for aio template (failing)"
```

---

## Task 2: Inner package skeleton — `__init__.py` and `config.py`

**Files:**
- Create: `tpl/aio/aio/__init__.py`
- Create: `tpl/aio/aio/config.py`

- [ ] **Step 2.1: Create `tpl/aio/aio/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 2.2: Create `tpl/aio/aio/config.py`**

```python
from dynaconf import Dynaconf

settings = Dynaconf(
    envvar_prefix="DYNACONF",
    settings_files=["settings.yaml", ".secrets.yaml"],
)
```

This matches `tpl/simple/simple/config.py` byte-for-byte except for being inside `aio/`.

- [ ] **Step 2.3: Commit**

```bash
git add tpl/aio/aio/__init__.py tpl/aio/aio/config.py
git commit -m "feat(aio): inner package skeleton"
```

---

## Task 3: `aio/core.py` — async `Service` class

**Files:**
- Create: `tpl/aio/aio/core.py`

- [ ] **Step 3.1: Write `tpl/aio/aio/core.py`**

```python
import asyncio
import logging
import signal
from pathlib import Path
from typing import Any


class AioService:
    def __init__(self, cfg: Any, execute_dir: Path) -> None:
        self.logger = logging.getLogger("aio")
        self.cfg = cfg
        self.execute_dir = execute_dir
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        """Signal the service to shut down. Safe to call from signal handlers and tests."""
        if not self._stop.is_set():
            self.logger.info("aio service stopping")
            self._stop.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_stop)

    async def run(self) -> None:
        self.logger.info("aio service starting")
        self._install_signal_handlers()
        tasks_n = int(self.cfg.get("tasks", 3))
        tick = float(self.cfg.get("tick_interval", 1.0))
        try:
            async with asyncio.TaskGroup() as tg:
                for i in range(tasks_n):
                    tg.create_task(self._worker(i, tick), name=f"worker-{i}")
                tg.create_task(self._stop_watcher(), name="stop-watcher")
        except* asyncio.CancelledError:
            pass
        self.logger.info("service stopped")

    async def _worker(self, i: int, tick: float) -> None:
        self.logger.info("worker-%d starting", i)
        try:
            while not self._stop.is_set():
                self.logger.info("worker-%d tick", i)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=tick)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.logger.info("worker-%d exiting", i)

    async def _stop_watcher(self) -> None:
        """Wait for stop, then enforce a 30s deadline on worker shutdown."""
        await self._stop.wait()
        deadline = asyncio.get_running_loop().time() + 30.0
        while True:
            await asyncio.sleep(0.1)
            live = [
                t for t in asyncio.all_tasks()
                if not t.done() and (t.get_name() or "").startswith("worker-")
            ]
            if not live:
                return
            if asyncio.get_running_loop().time() > deadline:
                self.logger.warning(
                    "shutdown deadline exceeded; cancelling %d workers", len(live)
                )
                for t in live:
                    t.cancel()
                return
```

> **Why `loop.add_signal_handler` not `signal.signal`**: in asyncio context, `signal.signal` callbacks may not run reliably during `await`. `loop.add_signal_handler` routes signals through the loop scheduler.
>
> **Why `TaskGroup` not `asyncio.gather`**: TaskGroup propagates exceptions and provides structured cleanup. `gather` requires manual `return_exceptions` and explicit cancellation handling.
>
> **Why `_stop_watcher` cancels workers itself instead of raising TimeoutError**: if `_stop_watcher` raised TimeoutError to break out of the TaskGroup, the exception would propagate from `run()` to the caller. Cancelling workers from inside `_stop_watcher` and returning cleanly lets the TaskGroup collect a clean ExceptionGroup of `CancelledError`s, which `except* asyncio.CancelledError:` swallows. Workers' `finally` blocks still run.

- [ ] **Step 3.2: Sanity-check the file is valid Python**

Run: `python3 -c "import ast; ast.parse(open('tpl/aio/aio/core.py').read())"`

Expected: no output (parse succeeded).

- [ ] **Step 3.3: Commit**

```bash
git add tpl/aio/aio/core.py
git commit -m "feat(aio): Service class with TaskGroup + asyncio signal handling"
```

---

## Task 4: `aio/main.py` — entry point

**Files:**
- Create: `tpl/aio/aio/main.py`

- [ ] **Step 4.1: Write `tpl/aio/aio/main.py`**

```python
import argparse
import asyncio
import logging.config
import os
from pathlib import Path

from aio.config import settings
from aio.core import AioService


def main() -> int:
    parser = argparse.ArgumentParser(description="aio service")
    parser.add_argument(
        "-d",
        "--execute-dir",
        type=Path,
        default=Path.cwd(),
        help="working directory for runtime files (logs, etc.)",
    )
    args = parser.parse_args()

    os.chdir(args.execute_dir)
    Path("logs").mkdir(exist_ok=True)
    logging.config.dictConfig(settings.logs)

    asyncio.run(AioService(settings.service, args.execute_dir).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

This mirrors `tpl/simple/simple/main.py` with the only difference being `asyncio.run(AioService(...).run())` instead of `SimpleService(...).run()`.

- [ ] **Step 4.2: Sanity-check parse**

Run: `python3 -c "import ast; ast.parse(open('tpl/aio/aio/main.py').read())"`

Expected: no output.

- [ ] **Step 4.3: Commit**

```bash
git add tpl/aio/aio/main.py
git commit -m "feat(aio): main entry with asyncio.run"
```

---

## Task 5: `settings.yaml`

**Files:**
- Create: `tpl/aio/settings.yaml`

- [ ] **Step 5.1: Write `tpl/aio/settings.yaml`**

```yaml
---
service:
  tasks: 3
  tick_interval: 1.0

logs:
  version: 1
  disable_existing_loggers: false
  formatters:
    base:
      format: "%(asctime)s %(levelname)s %(name)s [%(taskName)s] - %(message)s"
  handlers:
    console:
      class: logging.StreamHandler
      level: INFO
      formatter: base
      stream: ext://sys.stdout
    timefile:
      class: logging.handlers.TimedRotatingFileHandler
      level: DEBUG
      formatter: base
      filename: logs/aio.log
      when: D
      interval: 1
      backupCount: 7
      encoding: utf-8
  loggers:
    aio:
      level: INFO
      handlers: [timefile, console]
      propagate: false
  root:
    level: WARNING
    handlers: [console]
```

> **Why `%(taskName)s` in the formatter**: Python 3.12 added `taskName` to LogRecord (the asyncio task name). It naturally fills with `worker-0` / `worker-1` / etc., matching the multi_t template's `[%(threadName)s]` style. This is the asyncio analog.

- [ ] **Step 5.2: Commit**

```bash
git add tpl/aio/settings.yaml
git commit -m "feat(aio): settings.yaml with taskName-aware formatter"
```

---

## Task 6: `pyproject.toml`

**Files:**
- Create: `tpl/aio/pyproject.toml`

- [ ] **Step 6.1: Write `tpl/aio/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "aio"
version = "0.1.0"
description = "Asyncio Python service"
requires-python = ">=3.12"
dependencies = [
  "dynaconf>=3.2",
]

[project.scripts]
aio = "aio.main:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["aio*"]
```

This mirrors `tpl/simple/pyproject.toml` with `simple` → `aio`. No extra dependencies (the asyncio template uses only stdlib).

- [ ] **Step 6.2: Commit**

```bash
git add tpl/aio/pyproject.toml
git commit -m "feat(aio): pyproject.toml"
```

---

## Task 7: `control.sh`

**Files:**
- Create: `tpl/aio/control.sh` (executable)

- [ ] **Step 7.1: Write `tpl/aio/control.sh`**

Copy `tpl/simple/control.sh` byte-for-byte, then change `readonly NAME="simple"` to `readonly NAME="aio"`. The full content:

```bash
#!/usr/bin/env bash
set -euo pipefail

readonly NAME="aio"
readonly BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

EXECUTE_DIR="$BASE_DIR"
while [[ $# -gt 0 ]]; do
  case $1 in
    -d) shift; EXECUTE_DIR=$1; shift ;;
    *) break ;;
  esac
done

[[ -d $EXECUTE_DIR ]] || { echo "ERROR: $EXECUTE_DIR is not a directory" >&2; exit 1; }
mkdir -p "$EXECUTE_DIR/logs"
readonly PID_FILE="$EXECUTE_DIR/logs/$NAME.pid"

is_running() {
  [[ -f $PID_FILE ]] || return 1
  local pid
  pid=$(<"$PID_FILE")
  [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null
}

start() {
  if is_running; then
    echo "$NAME already running (pid $(<"$PID_FILE"))"
    return 0
  fi
  echo "starting $NAME..."
  (
    cd "$EXECUTE_DIR"
    nohup python3 -m "$NAME.main" -d "$EXECUTE_DIR" \
      >"$EXECUTE_DIR/logs/$NAME.out" \
      2>"$EXECUTE_DIR/logs/$NAME.err" &
    echo $! > "$PID_FILE"
  )
  sleep 1
  status
}

stop() {
  if ! is_running; then
    echo "$NAME not running"
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid=$(<"$PID_FILE")
  echo "stopping $NAME (pid $pid)..."
  kill -TERM "$pid" 2>/dev/null || true
  local i
  for ((i=0; i<30; i++)); do
    is_running || break
    sleep 1
  done
  if is_running; then
    echo "force killing $NAME"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  echo "stopped"
}

status() {
  if is_running; then
    echo "$NAME running (pid $(<"$PID_FILE"))"
  else
    echo "$NAME not running"
  fi
}

case ${1:-} in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  *) echo "usage: $0 [-d EXECUTE_DIR] {start|stop|restart|status}" >&2; exit 1 ;;
esac
```

- [ ] **Step 7.2: Make executable**

Run: `chmod +x tpl/aio/control.sh`

- [ ] **Step 7.3: Commit**

```bash
git add tpl/aio/control.sh
git commit -m "feat(aio): control.sh"
```

---

## Task 8: README files and dotfiles

**Files:**
- Create: `tpl/aio/README.md`
- Create: `tpl/aio/README_zh.md`
- Create: `tpl/aio/.gitignore`
- Create: `tpl/aio/.editorconfig`

- [ ] **Step 8.1: Write `tpl/aio/README.md`**

```markdown
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
```

- [ ] **Step 8.2: Write `tpl/aio/README_zh.md`**

```markdown
# aio

Asyncio-first 单进程 Python 服务骨架。

## 结构

```
.
├── pyproject.toml
├── settings.yaml
├── control.sh
└── aio/
    ├── __init__.py
    ├── config.py
    ├── core.py        # AioService，使用 asyncio.TaskGroup
    └── main.py        # 入口：argparse → dictConfig → asyncio.run
```

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
PATH=".venv/bin:$PATH" ./control.sh start
PATH=".venv/bin:$PATH" ./control.sh status
PATH=".venv/bin:$PATH" ./control.sh stop
```

日志位于 `logs/aio.log`（每日轮转，保留 7 份）和 `logs/aio.out`（stdout 捕获）。

## 配置

`settings.yaml`：

- `service.tasks` — worker 协程数（默认 3）
- `service.tick_interval` — worker 循环间隔（秒，默认 1.0）

## 演示要点

- `asyncio.TaskGroup` 实现结构化并发（3.11+）
- 信号处理用 `loop.add_signal_handler`（asyncio 上下文里唯一可靠的方式）
- `Service.request_stop()` 接口 —— 信号处理和测试都用这一个方法
- 有界关闭：worker 看到 `_stop.set()` 后协作退出；watcher task 强制 30s 截止

## 何时选这个模板

服务主体是 async IO（HTTP 客户端、DB 驱动、外部 API），希望自己掌控 event loop。要做 HTTP 服务用 `multi_p_h`；要消费外部消息队列用 `mq`。
```

- [ ] **Step 8.3: Write `tpl/aio/.gitignore`**

Copy from `tpl/simple/.gitignore` (verify with `cat tpl/simple/.gitignore` first; if it doesn't exist or is missing, use this minimum):

```gitignore
__pycache__/
*.pyc
*.pyo
.venv/
venv/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
logs/
data/
.secrets.yaml
```

- [ ] **Step 8.4: Write `tpl/aio/.editorconfig`**

Copy from `tpl/simple/.editorconfig` byte-for-byte (this file is identical across all templates).

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 4

[*.{yaml,yml,toml}]
indent_size = 2

[*.sh]
indent_size = 2

[Makefile]
indent_style = tab
```

If `tpl/simple/.editorconfig` differs, use the existing template's content verbatim.

- [ ] **Step 8.5: Commit**

```bash
git add tpl/aio/README.md tpl/aio/README_zh.md tpl/aio/.gitignore tpl/aio/.editorconfig
git commit -m "feat(aio): README + dotfiles"
```

---

## Task 9: Verify `pyskel --list` picks up `aio`

**Files:** none (verification only)

- [ ] **Step 9.1: Run `pyskel --list`**

Run: `./pyskel --list`

Expected: `aio` appears in the list alongside the existing 7 templates. The generator scans `tpl/*/` so no code change is needed; if `aio` is missing, the directory layout is wrong (verify `tpl/aio/` is a directory containing `tpl/aio/aio/` package).

- [ ] **Step 9.2: Generate a test project and inspect**

Run:
```bash
cd /tmp && rm -rf aio-smoke && mkdir aio-smoke && cd aio-smoke
/Users/mako/Work/git.mako.local/pemako/pyskel/pyskel aio my_worker
ls my_worker
ls my_worker/my_worker
```

Expected: project generated with inner package renamed `my_worker/`. Files like `my_worker/main.py` should have `from my_worker.config import settings` (substitution worked).

Run: `cat my_worker/my_worker/main.py | head -10`

Expected: imports use `my_worker`, not `aio`.

- [ ] **Step 9.3: Install and boot the generated project**

```bash
cd /tmp/aio-smoke/my_worker
python3 -m venv .venv
.venv/bin/pip install -q -e .
PATH=".venv/bin:$PATH" ./control.sh start
sleep 3
PATH=".venv/bin:$PATH" ./control.sh status
PATH=".venv/bin:$PATH" ./control.sh stop
cat logs/my_worker.log
```

Expected:
- `start` succeeds and reports a pid
- `status` confirms running
- `stop` reports "stopped"
- `logs/my_worker.log` contains lines like `aio service starting`, multiple `worker-N tick` entries, `aio service stopping`, `service stopped`

(The logger name is hardcoded `"aio"` in `core.py`. After substitution it's still `aio` because the literal `"aio"` inside the string is the logger name — but wait, pyskel substitutes ALL occurrences of `aio` → `my_worker`. So `logging.getLogger("aio")` becomes `logging.getLogger("my_worker")`. That's actually correct behavior — generated projects should log under their own name. But settings.yaml's `loggers.aio:` block also gets substituted to `loggers.my_worker:`, so it still matches. Verify both are consistent.)

- [ ] **Step 9.4: If anything in 9.2/9.3 fails, fix the template and re-run**

Common issues:
- `from aio.config import settings` not substituted → verify `pyskel`'s substitution covers all `.py` files (it does by default; only `*.pyc` and `__pycache__` are skipped)
- Generated project has stray `aio/` artifacts → clean and re-run

- [ ] **Step 9.5: Commit any fixes**

```bash
git add -A
git commit -m "fix(aio): <describe issue>"
```

(Skip if nothing to commit.)

---

## Task 10: Run e2e suite — verify aio passes

**Files:** none (verification only)

- [ ] **Step 10.1: Run the full e2e suite**

Run: `bash tests/e2e.sh`

Expected: all 8 templates pass (the original 7 + new `aio`). The output line for aio should look like:
```
✓ aio          <N> ticks across workers, clean stop
```

- [ ] **Step 10.2: If aio fails, diagnose**

Common failure modes and fixes:

| Symptom | Cause | Fix |
|---|---|---|
| `no clean stop log` | `core.py` doesn't log `service stopped` | Verify the final `self.logger.info("service stopped")` line in `run()` |
| `0 ticks across workers` | log messages don't match grep pattern | Verify formatter outputs `worker-N tick` somewhere; the e2e greps for "tick" |
| Process didn't stop in 30s | signal handler not installed or `_stop_watcher` not raising | Verify `loop.add_signal_handler` is called inside `run()`, not `__init__` |
| `pid not exiting after kill -TERM` | uvloop or other issue | n/a — we use stdlib asyncio only |

- [ ] **Step 10.3: Commit any fixes from diagnosis**

```bash
git add -A
git commit -m "fix(aio): <describe>"
```

(Skip if nothing to commit.)

---

## Task 11: Update root README to advertise `aio`

**Files:**
- Modify: `README.md`
- Modify: `README_zh.md`

- [ ] **Step 11.1: Read current README to find the template list**

Run: `grep -n "simple\|multi_t\|multi_p" README.md | head -20`

Find the section that lists templates (likely a table or bulleted list).

- [ ] **Step 11.2: Add `aio` row/entry to `README.md`**

Insert `aio` into the template list immediately after `multi_p_t` (or at the end of the list, depending on existing layout). Use a one-line description:

```
- `aio` — single-process asyncio service loop with TaskGroup
```

If the README uses a table, match the column structure. Read the file first to confirm format.

- [ ] **Step 11.3: Mirror the change in `README_zh.md`**

Add the same row in Chinese:

```
- `aio` — 单进程 asyncio 服务循环（TaskGroup）
```

- [ ] **Step 11.4: Commit**

```bash
git add README.md README_zh.md
git commit -m "docs: list aio template in root READMEs"
```

---

## Task 12: Open PR

**Files:** none (git/gh operations)

- [ ] **Step 12.1: Verify branch state is clean and ahead of main**

Run: `git status && git log --oneline main..HEAD`

Expected: clean working tree; commits like:
```
feat(aio): list aio template in root READMEs
feat(aio): README + dotfiles
feat(aio): control.sh
feat(aio): pyproject.toml
feat(aio): settings.yaml with taskName-aware formatter
feat(aio): main entry with asyncio.run
feat(aio): Service class with TaskGroup + asyncio signal handling
feat(aio): inner package skeleton
test: add e2e branch for aio template (failing)
```

- [ ] **Step 12.2: Push the branch**

If working in a feature branch:
```bash
git push -u origin <branch-name>
```

If on `main` and the workflow allows direct push: skip and proceed to step 12.3 with a different command (the user manages this).

- [ ] **Step 12.3: Open PR via `gh`**

```bash
gh pr create --title "feat: add aio template (asyncio + TaskGroup)" --body "$(cat <<'EOF'
## Summary
- New `aio` template: asyncio-first single-process Python service skeleton
- Uses `asyncio.TaskGroup` (3.11+) for structured concurrency
- Signal handling via `loop.add_signal_handler` (the asyncio-correct path)
- `Service.request_stop()` is the public stop interface — used by signal handler and tests

## Test plan
- [x] `./pyskel --list` shows `aio` alongside existing 7 templates
- [x] `./pyskel aio my_worker` generates a working project
- [x] Generated project: `pip install -e .` + `./control.sh start/stop` works clean
- [x] `bash tests/e2e.sh` — all 8 templates pass

Spec: `docs/superpowers/specs/2026-06-08-pyskel-aio-mq-design.md` §2.
EOF
)"
```

- [ ] **Step 12.4: Wait for CI to go green, then merge**

Run: `gh pr checks --watch`

Expected: e2e workflow passes. If it fails on CI but passed locally, suspect environment differences (Python version, signal handling on Linux vs macOS) and diagnose from CI logs.

---

## What this PR explicitly does NOT do

The following are scoped to later PRs (per `docs/superpowers/specs/2026-06-08-pyskel-aio-mq-design.md` §5):

- **PR 2** — `mq` template (Redis Streams consumer)
- **PR 3** — `Dockerfile` + `.dockerignore` rolled out across all 9 templates
- **PR 4** — `tests/test_smoke.py` + dev deps + pytest config across all 9 templates
- **PR 5** — `[tool.ruff]` + `[tool.mypy]` blocks + lint cleanup across all 9 templates

PR 4 is where the spec's "`Service.request_stop()` exposed publicly" requirement gets exercised by tests for the other 7 templates. This PR (PR 1) only adds it to `aio` because the asyncio template uses it internally for signal handling — it's not a retrofit, it's part of the natural design.

---

## Self-Review Checklist (run before opening PR)

- [ ] All 12 tasks above are complete (`grep "- \[ \]" docs/superpowers/plans/2026-06-08-pyskel-aio.md` returns nothing)
- [ ] `bash tests/e2e.sh` passes locally with `aio` in the output
- [ ] No leftover `logs/` or `data/` directories committed under `tpl/aio/`
- [ ] No `__pycache__/` or `*.pyc` under `tpl/aio/`
- [ ] `git log main..HEAD` is a clean commit history (no fixup commits, no "wip")
- [ ] CLAUDE.md is unchanged (the post-rollout doc PR will update it after PR 1+2 land)
