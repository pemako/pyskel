import logging
import logging.handlers
import multiprocessing as mp
import os
import signal
import socket
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any

from thrift.protocol import TBinaryProtocol
from thrift.server import TServer
from thrift.transport import TSocket, TTransport

from multi_p_t.handler import make_processor


class _StoppableThriftServer(TServer.TThreadedServer):
    """TThreadedServer with a real `stop()`.

    Apache Thrift's stock TThreadedServer wraps `accept()` in a broad
    `except Exception` that swallows the OSError you'd get when closing
    the listen socket — so `serve()` keeps re-accepting forever.
    Subclassing to add a `_stopped` flag and break out of the loop is
    the clean fix.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def serve(self) -> None:
        self.serverTransport.listen()
        while not self._stopped:
            try:
                client = self.serverTransport.accept()
                if not client:
                    continue
                t = threading.Thread(target=self.handle, args=(client,))
                t.setDaemon(self.daemon)
                t.start()
            except KeyboardInterrupt:
                raise
            except Exception:
                if self._stopped:
                    return
                logging.getLogger("multi_p_t").exception(
                    "error during accept; continuing"
                )


class _ReusePortServerSocket(TSocket.TServerSocket):
    """TServerSocket with SO_REUSEPORT enabled before bind.

    Apache Thrift's stock TServerSocket sets SO_REUSEADDR but not
    SO_REUSEPORT, so multiple workers bound to the same port would fail
    with EADDRINUSE. Setting SO_REUSEPORT lets the kernel load-balance
    accepted connections across all workers.
    """

    def listen(self) -> None:
        res0 = self._resolveAddr()
        for res in res0:
            if self.handle is not None:
                self.handle.close()
            try:
                self.handle = socket.socket(res[0], res[1])
                self.handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    self.handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                if res[0] == socket.AF_INET6:
                    self.handle.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                self.handle.settimeout(None)
                self.handle.bind(res[4])
                self.handle.listen(128)
                break
            except OSError:
                if res is res0[-1]:
                    raise


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
    logger = logging.getLogger(f"multi_p_t.worker.{worker_id}")

    processor = make_processor()
    transport = _ReusePortServerSocket(host=host, port=port)
    pfactory = TBinaryProtocol.TBinaryProtocolFactory()
    tfactory = TTransport.TBufferedTransportFactory()

    # daemon=True so the process can exit even if a connection thread is
    # mid-RPC — see README's caveats about Apache Thrift Python's
    # incomplete graceful-shutdown story.
    server = _StoppableThriftServer(
        processor, transport, tfactory, pfactory, daemon=True
    )

    def _serve() -> None:
        try:
            server.serve()
        except Exception:
            # serve() raises when the listening socket is closed during
            # shutdown — that's expected; not an error to log loudly.
            logger.debug("serve thread exiting", exc_info=True)

    serve_thread = threading.Thread(
        target=_serve, name=f"thrift-serve-{worker_id}", daemon=True
    )
    serve_thread.start()
    logger.info(
        "worker %d listening on %s:%d (max %d connection threads)",
        worker_id,
        host,
        port,
        threads,
    )

    try:
        # Sleep+poll loop instead of mp.Event.wait(): on macOS the C-level
        # sem_wait inside mp.Event.wait() may swallow signals, leaving the
        # Python signal handler unable to run.
        while not stop.is_set():
            time.sleep(0.5)
    finally:
        logger.info("worker %d draining (grace=%ds)", worker_id, grace)
        # Three-step shutdown:
        # 1. Set the stop flag so accept() errors are treated as graceful exit.
        # 2. shutdown(SHUT_RDWR) before close — on Linux a plain close() does
        #    NOT wake another thread blocked in accept(); shutdown() does.
        #    macOS happens to wake accept on close() too, but Linux does not.
        # 3. close() to release the fd.
        # Plus: serve_thread is daemon=True so even if accept() somehow stays
        # stuck, the worker process can still exit when _worker_main returns.
        server.stop()
        try:
            if transport.handle is not None:
                try:
                    transport.handle.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # already shutdown / not connected
                transport.handle.close()
        except Exception:
            pass
        serve_thread.join(timeout=grace)
        if serve_thread.is_alive():
            logger.warning(
                "worker %d serve thread didn't exit within grace", worker_id
            )
        logger.info("worker %d exiting", worker_id)


class Multi_p_tService:
    """Worker pool orchestrator. Not the Thrift service itself —
    that's PingHandler in handler.py."""

    def __init__(
        self,
        cfg: Any,
        execute_dir: Path,
        log_queue: "mp.Queue[Any]",
    ) -> None:
        self.logger = logging.getLogger("multi_p_t")
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
            "multi_p_t service starting %d workers on %s:%d",
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

        self.logger.info("multi_p_t service stopped")

    def stop(self) -> None:
        self._stop.set()
