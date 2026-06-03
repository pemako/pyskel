import argparse
import logging.config
import logging.handlers
import multiprocessing as mp
import os
from pathlib import Path

from multi_p_t.config import settings
from multi_p_t.core import Multi_p_tService


def _start_log_listener(
    log_queue: "mp.Queue",
) -> logging.handlers.QueueListener:
    """Drain children's log records into the parent's real handlers.

    Pulls handlers off both root and the multi_p_t logger (since
    settings.yaml sets multi_p_t.propagate=false, leaving it without
    handlers would drop every parent-side log)."""
    handlers: list[logging.Handler] = []
    targets = (logging.getLogger(), logging.getLogger("multi_p_t"))
    for lg in targets:
        for h in list(lg.handlers):
            handlers.append(h)
            lg.removeHandler(h)

    listener = logging.handlers.QueueListener(
        log_queue, *handlers, respect_handler_level=True
    )
    for lg in targets:
        lg.addHandler(logging.handlers.QueueHandler(log_queue))
    listener.start()
    return listener


def main() -> int:
    parser = argparse.ArgumentParser(description="multi_p_t service")
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

    log_queue: "mp.Queue" = mp.Queue(maxsize=10000)
    listener = _start_log_listener(log_queue)

    try:
        Multi_p_tService(settings.service, args.execute_dir, log_queue).run()
    finally:
        listener.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
