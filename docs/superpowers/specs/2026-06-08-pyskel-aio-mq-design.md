# pyskel: `aio` + `mq` 模板与跨模板共用层

**日期**：2026-06-08
**作者**：brainstorm with Claude
**状态**：草案，待 review

## 1. 背景与目标

`pyskel` 当前提供 7 个 Python 服务模板：`simple` / `multi_t` / `multi_t_q` / `multi_p` / `multi_p_h` / `multi_p_g` / `multi_p_t`。覆盖了同步 / 多线程 / 多进程 / HTTP / gRPC / Thrift 几个维度，但缺两块：

1. **asyncio 维度完全空白**：现有模板全是同步循环 + sleep / 阻塞 IO，没有"asyncio-first 长跑服务"的演示
2. **外部消息队列消费者**这一最常见的 Python 后端服务形态没有覆盖

同时所有模板都缺"开箱即生产"的共用基线：没有 `Dockerfile`、没有 `tests/`、没有 `ruff/mypy` 配置。

本设计目标：

- 新增 2 个模板（`aio`、`mq`）填上述维度空白
- 给全部 9 个模板（7 旧 + 2 新）统一加 3 项共用能力：`Dockerfile`、`pytest` 骨架、`ruff/mypy` 预配置
- 不动 `./pyskel` 生成器代码（全是字面量替换能处理的文本）

非目标（明确不做）：

- 结构化 JSON 日志（讨论后认定为"按需特性"，不属于普世必备，留给用户按需自行实现）
- OpenTelemetry / Prometheus 指标（生产观测层，留给后续路径）
- systemd unit / k8s manifests（部署层，留给后续路径）
- 模板继承 / 可组合特性（属于生成器路径 C，本次不动）

## 2. `aio` 模板设计

### 命名

模板目录：`tpl/aio/`。

> 不用 `async` 是因为它是 Python 保留字，会让 `tpl/async/async/__init__.py` 这条路径自身无法 import。`aio` 是合法标识符且语义清晰。用户生成时可以 `pyskel aio my_worker` 改成任意名字。

### 目录结构

```
tpl/aio/
├── pyproject.toml
├── settings.yaml
├── control.sh
├── Dockerfile
├── .dockerignore
├── tests/test_smoke.py
├── README.md / README_zh.md
├── .gitignore / .editorconfig
└── aio/
    ├── __init__.py
    ├── main.py
    ├── config.py
    └── core.py
```

### 关键设计

**`core.py`**

- `class Service`：`async def run()` 用 `asyncio.TaskGroup`（3.11+，项目基线 3.12+）启 N 个 `_worker(i)` 协程
- 信号处理：`loop.add_signal_handler(SIGTERM, self._request_stop)` + 同样装 `SIGINT`。**不能用** `signal.signal`，asyncio 上下文里它在 `await` 期间不可靠
- `_request_stop` 只 set 一个 `asyncio.Event`
- worker 用 `await asyncio.wait_for(stop.wait(), tick)` 替代 `sleep`：stop 被 set 立刻退出循环；超时则进入下一轮工作
- 30s 兜底：`asyncio.wait_for(tg_run, timeout=30)`，超时后由 TaskGroup 的 `__aexit__` 取消未完成 task。和现有同步模板的 30s 兜底语义对齐
- 不用 `asyncio.gather`：TaskGroup 异常传播 + 结构化清理是 3.11+ 的现代写法

**`main.py`**

```python
def main():
    args = parse_args()
    logging.config.dictConfig(settings.logs.to_dict())
    asyncio.run(Service(settings).run())
```

**`settings.yaml`**

```yaml
service:
  tasks: 3
  tick_interval: 1.0

logs:
  # 沿用现有 base formatter 模式，调整字段：异步无 thread/process 概念，只留 name + msg
  formatters:
    base:
      format: "%(asctime)s %(levelname)s %(name)s - %(message)s"
  # ...其余同 simple
```

### 与现有模板的关系

`aio` 是 `simple` 的 asyncio 版（单进程、无外部 IO）。不和任何已有模板冗余：

| 模板 | 并发模型 | 外部 IO |
|---|---|---|
| simple | 单进程同步 loop | 无 |
| multi_t | 多线程 | 无 |
| multi_p | 多进程 | 无 |
| **aio** | **单进程 asyncio** | **无** |

## 3. `mq` 模板设计

### 命名与定位

模板目录：`tpl/mq/`。asyncio + Redis Streams 消费者。和 `multi_t_q`（多线程 + 内存 queue + 重试 + 持久化重放）形成清晰对照：`mq` 是"外部 broker 替代内存 queue"。

### 目录结构

```
tpl/mq/
├── pyproject.toml         # 依赖：dynaconf, redis>=5; dev: +pytest, +pytest-asyncio, +fakeredis
├── settings.yaml
├── control.sh
├── Dockerfile             # 不 EXPOSE；ENV REDIS_URL=...
├── .dockerignore
├── tests/
│   ├── conftest.py        # fakeredis fixture
│   └── test_smoke.py
├── README.md / README_zh.md
└── mq/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── core.py            # Service: 连接、ensure group、TaskGroup 启 N+1 个 task
    └── handler.py         # MessageHandler 基类 + EchoHandler 示例
```

### 核心架构

**1 个 `redis.asyncio` 连接 + N 个 consumer task + 1 个 reaper task**，全部包在单个 `asyncio.TaskGroup` 里。

`Service.run()` 启动顺序：

1. `XGROUP CREATE MKSTREAM <stream> <group> $`，捕获 `BUSYGROUP` 异常忽略（group 已存在视为正常）
2. `loop.add_signal_handler` 装 SIGTERM / SIGINT
3. `async with TaskGroup as tg:` 启 N 个 `_consumer(i)` + 1 个 `_reaper()`
4. 关闭：set stop event → consumer 当前 block 完结后退出 → reaper 同 → TaskGroup `__aexit__` 等收尾 → 30s 兜底 `wait_for`

**`_consumer(i)`**：

```
loop:
  msgs = await redis.xreadgroup(group, f"consumer-{i}", {stream: ">"}, count=10, block=1000)
  if stop.is_set(): break
  for (id, fields) in msgs:
    try:
      await handler.handle(id, fields)
      await redis.xack(stream, group, id)
    except Exception as e:
      log.exception(...)
      # 不 ACK，留给 reaper
```

- 1s block 是为了让 stop event 能快速感知（不用 0 阻塞，不用过长）

**`_reaper()`**（每 5s 一次）：

```
loop:
  pending = await redis.xpending_range(stream, group, idle=claim_idle_ms, min="-", max="+", count=100)
  for entry in pending:
    if entry.times_delivered > max_retries:
      await redis.xadd(f"{stream}:dlq", {"orig_id": entry.id, "err": ..., **fields})
      await redis.xack(stream, group, entry.id)
    else:
      await redis.xautoclaim(stream, group, this_consumer, min_idle_time=claim_idle_ms, start=entry.id, count=1)
      # autoclaim 后下一轮 consumer 用 "0" id 形式拿到 owned 消息再处理
  await asyncio.sleep(5)
```

**为什么 reaper 单独 task**：retry / DLQ 是 cross-cutting 关注点，不应该混在 worker 主循环里；让 worker 只负责"读 → handler → ack"，reaper 单独处理 PEL 老消息。这是 Redis Streams 教科书 pattern。

**为什么 `redis.asyncio` 而不是 `aioredis`**：`aioredis` 已废弃并合并进 `redis>=4.2`，`from redis.asyncio import Redis` 是当前唯一推荐路径，单一 dep。本项目锁 `redis>=5` 是为了同时覆盖 `XAUTOCLAIM`（redis-py 4.5+ / Redis server 6.2+）和稳定的 `xpending_range` API。

**关于 monkeypatch**：`core.py` 顶端 `from redis.asyncio import Redis`，`Service.__init__` 里 `self._redis_cls = Redis` 或直接用 `Redis(...)`。测试通过 `monkeypatch.setattr("mq.core.Redis", FakeRedis)` 替换 —— 因此 `core.py` **必须**用 `Redis(...)` 而不是 `redis.asyncio.Redis(...)` 形式调用，以保证替换生效。

### `handler.py`

```python
class MessageHandler:
    async def handle(self, msg_id: str, fields: dict[str, str]) -> None:
        raise NotImplementedError

class EchoHandler(MessageHandler):
    async def handle(self, msg_id, fields):
        log.info("got %s: %s", msg_id, fields)
```

模板默认装 EchoHandler；用户继承 `MessageHandler` 替换业务逻辑。

### `settings.yaml`

```yaml
service:
  consumers: 4
  max_retries: 3
  claim_idle_ms: 30000
  reaper_interval_s: 5

redis:
  url: "redis://localhost:6379/0"
  stream: "mq:tasks"
  group: "mq-workers"

logs:
  # 同其他模板的 base formatter
```

### Smoke 测试

`tests/conftest.py`：

```python
import pytest
from fakeredis.aioredis import FakeRedis

@pytest.fixture(autouse=True)
def patch_redis(monkeypatch):
    monkeypatch.setattr("mq.core.Redis", FakeRedis)
```

`tests/test_smoke.py`：

```python
@pytest.mark.asyncio
async def test_consume_and_ack():
    # XADD 2 messages → start service → wait → assert handler called twice + acked
```

不依赖真实 Redis → 本地 + CI 都跑得动。

### CI 集成

`.github/workflows/e2e.yml` 加：

```yaml
services:
  redis:
    image: redis:7-alpine
    ports: ['6379:6379']
```

`tests/e2e.sh` 跑 `mq` 时检测 `REDIS_URL`：
- 无 → 仅冒烟（import + boot + 立即停）
- 有 → 完整 producer→consumer→DLQ 测

## 4. 跨模板共用层（3 项）

应用范围：全部 9 个模板（7 旧 + 2 新）。

### 4a. Dockerfile

每模板根放一份 `Dockerfile`，结构一致：

```dockerfile
FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml ./
COPY <template>/ ./<template>/
RUN pip install --no-cache-dir .

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN useradd -r -u 1000 app
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY <template>/ ./<template>/
COPY settings.yaml ./
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE <port>
CMD ["python", "-m", "<template>.main"]
```

`<template>` 走 pyskel 字面量替换。`EXPOSE` 行按模板分类：

| 模板 | EXPOSE 行 |
|---|---|
| simple, multi_t, multi_t_q, multi_p, aio, mq | （删除该行） |
| multi_p_h | `EXPOSE 8000` |
| multi_p_g | `EXPOSE 50051` |
| multi_p_t | `EXPOSE 9090` |

`.dockerignore`（统一一份）：

```
.git
.venv
__pycache__
*.pyc
logs/
data/
tests/
.pytest_cache
.mypy_cache
.ruff_cache
*.egg-info
```

### 4b. pytest 骨架

每个模板加 `tests/test_smoke.py`，验证 import + boot + clean stop。三种形态：

**同步非网络模板**（`simple` / `multi_t` / `multi_t_q` / `multi_p`）：

```python
import threading, time
from <template>.core import Service
from <template>.config import settings

def test_boots_and_stops_clean():
    svc = Service(settings)
    t = threading.Thread(target=svc.run, daemon=True)
    t.start()
    time.sleep(0.5)
    svc.request_stop()       # 直接调用，不发 SIGTERM 给 pytest 进程
    t.join(timeout=5)
    assert not t.is_alive()
```

> 不用 `os.kill(os.getpid(), SIGTERM)` 触发停机，因为信号会送到 pytest 自身进程，可能影响 runner。所有 `Service` 类需要暴露 `request_stop()` 方法（同步模板内部 set `threading.Event`，asyncio 模板 set `asyncio.Event`）。这是本次落地中对现有 `Service` 类**唯一**的接口扩展 —— 可以在 PR 4 内顺手补上，单行改动。

**asyncio 模板**（`aio` / `mq`）：

```python
import asyncio, pytest
from <template>.core import Service
from <template>.config import settings

@pytest.mark.asyncio
async def test_boots_and_stops_clean():
    svc = Service(settings)
    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.3)
    svc.request_stop()
    await asyncio.wait_for(task, timeout=5.0)
```

**网络模板**（`multi_p_h` / `multi_p_g` / `multi_p_t`）：用 `127.0.0.1:0`（OS 分配端口）启 server，发一条 client 请求验证回环，然后停。**不踩固定端口**，CI 友好。

`pyproject.toml` 加（**最终的 `dev` 块由 PR 4 + PR 5 合并产生**，下面是合并后的形态）：

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8",
  "ruff>=0.5",
  "mypy>=1.10",
  # asyncio 模板（aio / mq）追加：
  # "pytest-asyncio>=0.23",
  # mq 模板再追加：
  # "fakeredis[json]>=2.20",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
# asyncio 模板加 asyncio_mode = "auto"
```

### 4c. ruff + mypy 预配置

每模板的 `pyproject.toml` 加（`dev` 块见 4b 的合并形态）：

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "RUF"]

[tool.mypy]
python_version = "3.12"
warn_unused_ignores = true
warn_redundant_casts = true
warn_return_any = true
```

**不**改 `control.sh` 加 lint / test 子命令 —— 保持职责单一（生命周期 only）。开发命令在每模板的 README 写明：

```
pip install -e .[dev]
ruff check .
mypy <template>
pytest
```

### CI 更新（汇总）

`.github/workflows/e2e.yml`：

- 加 `services.redis` 容器（mq 用）
- 每个生成项目跑：`pip install -e .[dev]` → `ruff check` → `mypy` → `pytest tests/` → `docker build`
- docker build 用 buildx + `--load` 跑本地 image，不 push
- 网络模板的 docker build 后再跑一次 `docker run` + 端口探活

## 5. 落地计划

### PR 拆分（5 个 PR）

| # | PR | 内容 | 依赖 | 估时 |
|---|---|---|---|---|
| 1 | `aio` 模板 | 完整模板 + e2e 分支 | — | 0.5 天 |
| 2 | `mq` 模板 | 完整模板 + handler.py + redis CI service + e2e | PR 1 | 1.5 天 |
| 3 | Dockerfile 全量 | 9 模板 Dockerfile + .dockerignore + CI docker build | PR 1+2 合并后 | 0.5 天 |
| 4 | pytest 骨架全量 | 9 模板 tests/test_smoke.py + dev 依赖 + CI pytest | PR 1+2 合并后 | 1 天 |
| 5 | ruff/mypy 预配置 | 9 模板 pyproject.toml 配置块 + CI lint + 修干净已有 warning | PR 1+2 合并后 | 0.5 天 |

总计 ~4 天集中开发。

### 顺序逻辑

- **PR 1 → PR 2**：`mq` 复用 `aio` 的 asyncio + signal + TaskGroup pattern，先把 `aio` 形态定下来再开 `mq`
- **PR 1+2 → PR 3+4+5**：跨模板层在新模板进来之后再统一加，避免新模板进来时还要追修 Dockerfile / tests
- **PR 3 / 4 / 5 三者独立**：可以并行。同一个 `pyproject.toml` 的修改通过 rebase 合解

### 文档更新

每个 PR 顺手更对应模板的 `README.md` / `README_zh.md`。全部合完后单独一次"文档收尾"PR：

- 顶层 `README.md` / `README_zh.md` 加 `aio`、`mq` 入口 + 跨模板共用层介绍
- `CLAUDE.md` 扩"Per-template specifics"加 `aio`、`mq` 节；扩"Cross-template conventions"加 Dockerfile / pytest / ruff/mypy 节

## 6. 验收标准

- `./pyskel --list` 输出 9 个模板
- `./pyskel aio my_worker` / `./pyskel mq my_consumer` 都生成可立即 `pip install -e .[dev]` 的项目
- 9 个模板都通过：`docker build` / `pytest` / `ruff check .` / `mypy <pkg>`
- e2e CI 全绿，含 redis service container
- 文档（顶层 README + CLAUDE.md）覆盖到所有新增内容

## 7. 风险与回滚

- **`fakeredis` 不支持某些 Streams 命令**：备选用 `pytest.mark.skipif` 跳过 mq smoke，依赖真 Redis；不阻塞 PR 2 落地
- **mypy 在某些现有模板暴露真问题**：PR 5 顺手修；如果牵连过广，单独开 issue 后做，不阻塞 PR 5
- **docker build 在 CI 时间过长**：可降级为 lint-only（`docker build --check` / hadolint），保留构建本地手测
- 每 PR 独立可回滚（git revert）；新模板进 `tpl/` 不影响旧模板生成路径
