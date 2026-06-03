"""Task data shape + processor base class.

Replace the body of `TaskProcessor.process()` with your real logic.
Subclass `Task` if you need more fields, but keep it pickle-friendly:
the leftover task list is pickled to disk on shutdown so workers can
resume after restart.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    """One unit of work. Pickle-friendly so it can survive restarts.

    `attempts` travels with the task — when a worker retries, it
    increments this; if it crosses the retry threshold, the task is
    moved to the failed queue and persisted on shutdown.
    """
    payload: Any = None
    attempts: int = 0
    created_at: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return f"Task(payload={self.payload!r}, attempts={self.attempts})"


class TaskProcessor:
    """Override `process()` with your real work.

    Return True on success → task is dropped from the queue.
    Return False on transient failure → task is retried up to
    settings.service.retry_attempts times.
    Raise an exception → treated as a transient failure (caught and
    counted as a retry).
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("multi_t_q.processor")

    def process(self, task: Task) -> bool:
        # Replace this with the real per-task work. The default just
        # logs the payload so the template runs end-to-end with no edits.
        self.logger.info("processing %s", task)
        return True
