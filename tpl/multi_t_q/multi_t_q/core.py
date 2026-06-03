import logging
import pickle
import queue
import signal
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any

from multi_t_q.tasks import Task, TaskProcessor


class Multi_t_qService:
    """Producer-consumer task pipeline with retry + durable replay.

    One producer thread feeds tasks into a bounded queue; N worker
    threads consume them. On shutdown, both the in-flight queue and the
    failed-task list are pickled to a todo file. Next start reloads
    that file before producing new tasks, so no work is lost on a
    clean restart.
    """

    def __init__(self, cfg: Any, execute_dir: Path) -> None:
        self.logger = logging.getLogger("multi_t_q")
        self.cfg = cfg
        self.execute_dir = execute_dir
        self.worker_count: int = int(cfg.workers)
        self.retry_attempts: int = int(cfg.retry_attempts)
        self.retry_interval: float = float(cfg.retry_interval)
        self.todo_file: Path = execute_dir / cfg.todo_file

        self.task_queue: queue.Queue[Task] = queue.Queue(maxsize=int(cfg.queue_max))
        self.failed_queue: queue.Queue[Task] = queue.Queue()
        self.processor = TaskProcessor()

        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self._producer: threading.Thread | None = None
        self._task_counter = 0  # default producer's monotonic id source

        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle_signal)

    def _handle_signal(self, sig: int, frame: FrameType | None) -> None:
        self.logger.info("received signal %d, stopping", sig)
        self.stop()

    # ------------------------------------------------------------ producer

    def _produce_next(self) -> Task | None:
        """Return the next Task to enqueue, or None if there's no work
        right now (the producer will sleep 1s before asking again).

        Default: emit a synthetic task once per second so the template
        runs end-to-end out of the box. Replace this with your real
        source — poll a queue, watch a directory, read from a stream.
        """
        time.sleep(1.0)
        self._task_counter += 1
        return Task(payload=f"work-{self._task_counter}")

    def _produce_loop(self) -> None:
        self.logger.info("producer started")
        while not self._stop.is_set():
            task = self._produce_next()
            if task is None:
                if self._stop.wait(timeout=1.0):
                    break
                continue
            try:
                # Bounded put with timeout so a backed-up queue doesn't
                # block shutdown — we periodically wake to recheck _stop.
                self.task_queue.put(task, timeout=1.0)
            except queue.Full:
                # Queue saturated: workers are slow. Don't drop the task,
                # just retry on next tick (producer naturally back-pressured).
                continue
        self.logger.info("producer exiting")

    # ------------------------------------------------------------ consumer

    def _worker_loop(self) -> None:
        name = threading.current_thread().name
        self.logger.info("%s started", name)
        while not self._stop.is_set():
            try:
                task = self.task_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process_with_retry(task)
            finally:
                self.task_queue.task_done()
        self.logger.info("%s exiting", name)

    def _process_with_retry(self, task: Task) -> None:
        while task.attempts <= self.retry_attempts:
            try:
                if self.processor.process(task):
                    return
            except Exception:
                self.logger.exception("processor raised on %s", task)
            task.attempts += 1
            if task.attempts > self.retry_attempts:
                break
            # Wait, but break early if we're shutting down.
            if self._stop.wait(timeout=self.retry_interval):
                # Re-enqueue so it gets persisted on shutdown rather than
                # silently dropped.
                self.failed_queue.put(task)
                return
        self.logger.warning("max retries exceeded, moving to failed: %s", task)
        self.failed_queue.put(task)

    # ------------------------------------------------------------ lifecycle

    def run(self) -> None:
        loaded = self._load_todo()
        if loaded:
            self.logger.info("resumed %d task(s) from %s", loaded, self.todo_file)

        self.logger.info(
            "starting %d worker(s), queue max %d, retry %d × %ds",
            self.worker_count,
            self.task_queue.maxsize,
            self.retry_attempts,
            self.retry_interval,
        )

        for i in range(self.worker_count):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"worker-{i}",
                daemon=False,
            )
            t.start()
            self._workers.append(t)

        self._producer = threading.Thread(
            target=self._produce_loop,
            name="producer",
            daemon=False,
        )
        self._producer.start()

        # Block on the stop event with short polls so the signal handler
        # has a chance to run between waits.
        while not self._stop.is_set():
            self._stop.wait(timeout=1.0)

        self._join_threads()
        self._dump_todo()
        self.logger.info("multi_t_q service stopped")

    def _join_threads(self) -> None:
        deadline = time.monotonic() + 30
        threads = ([self._producer] if self._producer else []) + self._workers
        for t in threads:
            remaining = max(0.0, deadline - time.monotonic())
            t.join(timeout=remaining)
            if t.is_alive():
                self.logger.warning("%s did not exit within deadline", t.name)

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ persistence

    def _drain_to_list(self, q: queue.Queue[Task]) -> list[Task]:
        out: list[Task] = []
        while True:
            try:
                out.append(q.get_nowait())
            except queue.Empty:
                return out

    def _dump_todo(self) -> None:
        leftover = self._drain_to_list(self.task_queue)
        failed = self._drain_to_list(self.failed_queue)
        if not leftover and not failed:
            self.logger.info("nothing to persist")
            return
        self.todo_file.parent.mkdir(parents=True, exist_ok=True)
        # Tag each task so the next run can tell whether to retry or skip.
        bundle = {"pending": leftover, "failed": failed}
        with open(self.todo_file, "wb") as fp:
            pickle.dump(bundle, fp, protocol=pickle.HIGHEST_PROTOCOL)
        self.logger.info(
            "persisted %d pending + %d failed to %s",
            len(leftover),
            len(failed),
            self.todo_file,
        )

    def _load_todo(self) -> int:
        if not self.todo_file.exists():
            return 0
        try:
            with open(self.todo_file, "rb") as fp:
                bundle = pickle.load(fp)
        except (OSError, pickle.PickleError, EOFError):
            self.logger.exception("failed to load todo file %s", self.todo_file)
            return 0

        # Pending tasks rejoin the queue immediately (workers will pick them
        # up). Failed tasks go into the failed_queue and are NOT retried by
        # default — they're surfaced for the operator. Change this if you
        # want auto-retry across restarts.
        pending = bundle.get("pending", [])
        failed = bundle.get("failed", [])
        for t in pending:
            try:
                self.task_queue.put_nowait(t)
            except queue.Full:
                self.failed_queue.put(t)
        for t in failed:
            self.failed_queue.put(t)

        # Remove the file so a crash before next dump doesn't replay these
        # tasks twice.
        try:
            self.todo_file.unlink()
        except OSError:
            pass

        return len(pending) + len(failed)
