import asyncio
import logging
import signal
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from mq.handler import EchoHandler, MessageHandler


class MqService:
    def __init__(self, settings: Any, execute_dir: Path) -> None:
        self.logger = logging.getLogger("mq")
        self.settings = settings
        self.execute_dir = execute_dir
        self._stop = asyncio.Event()
        self._redis: Redis | None = None
        self.handler: MessageHandler = EchoHandler()

    def request_stop(self) -> None:
        if not self._stop.is_set():
            self.logger.info("mq service stopping")
            self._stop.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_stop)

    async def run(self) -> None:
        self.logger.info("mq service starting")
        self._install_signal_handlers()

        url = self.settings.redis.get("url", "redis://localhost:6379/0")
        self._stream = self.settings.redis.get("stream", "mq:tasks")
        self._group = self.settings.redis.get("group", "mq-workers")
        self._consumers_n = int(self.settings.service.get("consumers", 4))
        self._max_retries = int(self.settings.service.get("max_retries", 3))
        self._claim_idle_ms = int(self.settings.service.get("claim_idle_ms", 30000))
        self._reaper_interval_s = float(self.settings.service.get("reaper_interval_s", 5.0))

        self._redis = Redis.from_url(url, decode_responses=True)

        try:
            await self._redis.xgroup_create(
                self._stream, self._group, id="$", mkstream=True
            )
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        try:
            async with asyncio.TaskGroup() as tg:
                for i in range(self._consumers_n):
                    tg.create_task(self._consumer(i), name=f"consumer-{i}")
                tg.create_task(self._reaper(), name="reaper")
                tg.create_task(self._stop_watcher(), name="stop-watcher")
        except* asyncio.CancelledError:
            pass
        finally:
            if self._redis is not None:
                await self._redis.aclose()

        self.logger.info("service stopped")

    async def _consumer(self, i: int) -> None:
        name = f"consumer-{i}"
        self.logger.info("%s starting", name)
        try:
            while not self._stop.is_set():
                try:
                    resp = await self._redis.xreadgroup(
                        self._group, name, {self._stream: ">"}, count=10, block=1000
                    )
                except Exception:
                    self.logger.exception("%s xreadgroup failed", name)
                    await asyncio.sleep(1.0)
                    continue
                for _stream, messages in resp or []:
                    for msg_id, fields in messages:
                        if self._stop.is_set():
                            break
                        try:
                            await self.handler.handle(msg_id, fields)
                            await self._redis.xack(self._stream, self._group, msg_id)
                        except Exception:
                            self.logger.exception(
                                "%s handler failed for %s; leaving in PEL", name, msg_id
                            )
        finally:
            self.logger.info("%s exiting", name)

    async def _reaper(self) -> None:
        self.logger.info("reaper starting")
        try:
            cursor = "0-0"
            while not self._stop.is_set():
                try:
                    cursor, claimed, _deleted = await self._redis.xautoclaim(
                        self._stream,
                        self._group,
                        "reaper",
                        min_idle_time=self._claim_idle_ms,
                        start_id=cursor,
                        count=100,
                    )
                except Exception:
                    self.logger.exception("reaper xautoclaim failed")
                    cursor = "0-0"
                    await self._sleep_or_stop(self._reaper_interval_s)
                    continue

                for msg_id, fields in claimed:
                    if self._stop.is_set():
                        break
                    try:
                        pending = await self._redis.xpending_range(
                            self._stream, self._group, min=msg_id, max=msg_id, count=1
                        )
                        deliveries = pending[0]["times_delivered"] if pending else 0
                    except Exception:
                        self.logger.exception("reaper xpending failed for %s", msg_id)
                        continue

                    if deliveries > self._max_retries:
                        try:
                            payload = {"orig_id": msg_id, "deliveries": str(deliveries)}
                            payload.update({str(k): str(v) for k, v in fields.items()})
                            await self._redis.xadd(f"{self._stream}:dlq", payload)
                            await self._redis.xack(self._stream, self._group, msg_id)
                            self.logger.warning(
                                "reaper sent %s to DLQ after %d deliveries",
                                msg_id,
                                deliveries,
                            )
                        except Exception:
                            self.logger.exception("reaper DLQ failed for %s", msg_id)
                    else:
                        try:
                            await self.handler.handle(msg_id, fields)
                            await self._redis.xack(self._stream, self._group, msg_id)
                        except Exception:
                            self.logger.exception("reaper retry failed for %s", msg_id)

                if cursor == "0-0":
                    await self._sleep_or_stop(self._reaper_interval_s)
        finally:
            self.logger.info("reaper exiting")

    async def _sleep_or_stop(self, secs: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=secs)
        except asyncio.TimeoutError:
            pass

    async def _stop_watcher(self) -> None:
        await self._stop.wait()
        deadline = asyncio.get_running_loop().time() + 30.0
        while True:
            await asyncio.sleep(0.1)
            live = [
                t for t in asyncio.all_tasks()
                if not t.done() and (
                    (t.get_name() or "").startswith("consumer-")
                    or (t.get_name() or "") == "reaper"
                )
            ]
            if not live:
                return
            if asyncio.get_running_loop().time() > deadline:
                self.logger.warning(
                    "shutdown deadline exceeded; cancelling %d tasks", len(live)
                )
                for t in live:
                    t.cancel()
                return
