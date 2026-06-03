import logging
import signal
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any


class Multi_tService:
    def __init__(self, cfg: Any, execute_dir: Path) -> None:
        self.logger = logging.getLogger("multi_t")
        self.cfg = cfg
        self.execute_dir = execute_dir
        self.worker_count: int = int(cfg.workers)
        self._workers: list[threading.Thread] = []
        self._stop = threading.Event()
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle_signal)

    def _handle_signal(self, sig: int, frame: FrameType | None) -> None:
        self.logger.info("received signal %d, stopping", sig)
        self.stop()

    def _work_loop(self) -> None:
        name = threading.current_thread().name
        self.logger.info("%s started", name)
        while not self._stop.is_set():
            self._do_work()
            # wait() returns True if Event was set during the wait — exits
            # immediately on shutdown rather than burning the full second.
            if self._stop.wait(timeout=1.0):
                break
        self.logger.info("%s exiting", name)

    def _do_work(self) -> None:
        # Replace this with the per-iteration unit of work.
        self.logger.info("running")
        time.sleep(0)  # placeholder; real work goes here

    def run(self) -> None:
        self.logger.info("multi_t service starting with %d workers", self.worker_count)
        for i in range(self.worker_count):
            t = threading.Thread(
                target=self._work_loop,
                name=f"worker-{i}",
                daemon=False,
            )
            t.start()
            self._workers.append(t)

        # Block main thread until stop is signaled, then join workers.
        # Using Event.wait() instead of a busy loop keeps the main thread idle.
        self._stop.wait()

        deadline = time.monotonic() + 30
        for t in self._workers:
            remaining = max(0.0, deadline - time.monotonic())
            t.join(timeout=remaining)
            if t.is_alive():
                self.logger.warning("%s did not exit within shutdown deadline", t.name)

        self.logger.info("multi_t service stopped")

    def stop(self) -> None:
        self._stop.set()
