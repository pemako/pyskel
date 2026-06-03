import logging
import logging.handlers
import multiprocessing as mp
import os
import signal
import time
from pathlib import Path
from types import FrameType
from typing import Any


def _init_child_logging(log_queue: "mp.Queue[Any]") -> None:
    """Replace child's root logger handlers with a single QueueHandler.

    With `fork`, children inherit parent's file/console handlers and would
    race writing to the same file. With `spawn`, children start with the
    default config. Either way, route everything through the parent's queue.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(logging.handlers.QueueHandler(log_queue))
    root.setLevel(logging.DEBUG)


def _worker_main(
    log_queue: "mp.Queue[Any]",
    stop: "mp.synchronize.Event",
    worker_id: int,
) -> None:
    """Per-child entry point. Module-level so `spawn` can pickle it."""
    _init_child_logging(log_queue)
    logger = logging.getLogger(f"multi_p.worker.{worker_id}")
    logger.info("worker %d started (pid %d)", worker_id, os.getpid())
    try:
        while not stop.is_set():
            _do_work(logger)
            # Returns True when stop is set during the wait — exits without
            # waiting out the full second.
            if stop.wait(timeout=1.0):
                break
    except Exception:
        logger.exception("worker %d crashed", worker_id)
        raise
    finally:
        logger.info("worker %d exiting (pid %d)", worker_id, os.getpid())


def _do_work(logger: logging.Logger) -> None:
    """Replace this with the per-iteration unit of work."""
    logger.info("running")


class Multi_pService:
    def __init__(
        self,
        cfg: Any,
        execute_dir: Path,
        log_queue: "mp.Queue[Any]",
    ) -> None:
        self.logger = logging.getLogger("multi_p")
        self.cfg = cfg
        self.execute_dir = execute_dir
        self.worker_count: int = int(cfg.workers)
        self._workers: list[mp.Process] = []
        self._stop = mp.Event()
        self._log_queue = log_queue
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle_signal)

    def _handle_signal(self, sig: int, frame: FrameType | None) -> None:
        self.logger.info("received signal %d, stopping", sig)
        self.stop()

    def run(self) -> None:
        self.logger.info(
            "multi_p service starting with %d workers", self.worker_count
        )
        for i in range(self.worker_count):
            p = mp.Process(
                target=_worker_main,
                name=f"worker-{i}",
                args=(self._log_queue, self._stop, i),
            )
            p.start()
            self._workers.append(p)

        # Sleep+check loop instead of mp.Event.wait(): on macOS the C-level
        # sem_wait inside mp.Event.wait() may retry EINTR before yielding to
        # the Python signal handler, so SIGTERM appears to be ignored. A
        # plain time.sleep is reliably interrupted, the signal handler runs,
        # and on the next iteration we see the event is set.
        while not self._stop.is_set():
            time.sleep(0.5)

        deadline = time.monotonic() + 30
        for p in self._workers:
            remaining = max(0.0, deadline - time.monotonic())
            p.join(timeout=remaining)
            if p.is_alive():
                self.logger.warning(
                    "%s did not exit within deadline; terminating", p.name
                )
                p.terminate()
                p.join(timeout=5)
                if p.is_alive():
                    self.logger.error("%s ignored terminate; killing", p.name)
                    p.kill()
                    p.join(timeout=5)

        self.logger.info("multi_p service stopped")

    def stop(self) -> None:
        self._stop.set()
