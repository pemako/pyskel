import logging
import logging.handlers
import multiprocessing as mp
import os
import signal
import time
from concurrent import futures
from pathlib import Path
from types import FrameType
from typing import Any

import grpc

from multi_p_g.pb import service_pb2_grpc
from multi_p_g.handler import PingServicer


def _init_child_logging(log_queue: "mp.Queue[Any]") -> None:
    """Replace child's root logger handlers with a single QueueHandler so
    log records flow back to the parent's listener (single log file, no
    interleaving, no fd race)."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(logging.handlers.QueueHandler(log_queue))
    root.setLevel(logging.DEBUG)


def _worker_main(
    log_queue: "mp.Queue[Any]",
    stop: "mp.synchronize.Event",
    worker_id: int,
    host: str,
    port: int,
    threads: int,
    grace: int,
) -> None:
    """Per-child entry point. Module-level so spawn can pickle it."""
    _init_child_logging(log_queue)
    logger = logging.getLogger(f"multi_p_g.worker.{worker_id}")

    # SO_REUSEPORT lets all workers bind the same port; the kernel
    # load-balances accepted connections across them.
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=threads),
        options=[("grpc.so_reuseport", 1)],
    )
    service_pb2_grpc.add_PingServiceServicer_to_server(PingServicer(), server)

    bind_addr = f"{host}:{port}"
    bound = server.add_insecure_port(bind_addr)
    if bound == 0:
        logger.error("worker %d failed to bind %s", worker_id, bind_addr)
        return

    server.start()
    logger.info("worker %d listening on %s (pid %d)", worker_id, bind_addr, os.getpid())

    # Sleep+poll loop instead of mp.Event.wait(): on macOS the C-level
    # sem_wait inside mp.Event.wait() may swallow signals, leaving the
    # Python signal handler unable to run.
    try:
        while not stop.is_set():
            time.sleep(0.5)
    finally:
        logger.info("worker %d draining (grace=%ds)", worker_id, grace)
        # server.stop returns a future that completes when in-flight RPCs
        # finish or grace elapses. .wait() blocks until it does.
        server.stop(grace=grace).wait()
        logger.info("worker %d exiting", worker_id)


class Multi_p_gService:
    """Worker pool orchestrator. Not the gRPC service itself —
    that's PingServicer in handler.py."""

    def __init__(
        self,
        cfg: Any,
        execute_dir: Path,
        log_queue: "mp.Queue[Any]",
    ) -> None:
        self.logger = logging.getLogger("multi_p_g")
        self.cfg = cfg
        self.execute_dir = execute_dir
        self.host: str = str(cfg.host)
        self.port: int = int(cfg.port)
        self.worker_count: int = int(cfg.workers)
        self.threads_per_worker: int = int(cfg.threads_per_worker)
        self.shutdown_grace: int = int(cfg.shutdown_grace)
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
            "multi_p_g service starting %d workers on %s:%d",
            self.worker_count,
            self.host,
            self.port,
        )
        for i in range(self.worker_count):
            p = mp.Process(
                target=_worker_main,
                name=f"worker-{i}",
                args=(
                    self._log_queue,
                    self._stop,
                    i,
                    self.host,
                    self.port,
                    self.threads_per_worker,
                    self.shutdown_grace,
                ),
            )
            p.start()
            self._workers.append(p)

        while not self._stop.is_set():
            time.sleep(0.5)

        # Workers see the stop event and call server.stop(grace=N) themselves.
        # Give them grace + 5s slack to finish draining before we escalate.
        deadline = time.monotonic() + self.shutdown_grace + 5
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

        self.logger.info("multi_p_g service stopped")

    def stop(self) -> None:
        self._stop.set()
