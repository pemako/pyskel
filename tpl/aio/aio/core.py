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
