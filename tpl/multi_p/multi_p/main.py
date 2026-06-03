import argparse
import logging.config
import logging.handlers
import multiprocessing as mp
import os
from pathlib import Path

from multi_p.config import settings
from multi_p.core import Multi_pService


def _start_log_listener(
    log_queue: "mp.Queue",
) -> logging.handlers.QueueListener:
    """Drain children's log records into the parent's real handlers.

    After dictConfig, the parent's loggers have file + console handlers
    attached. We capture those, hand them to a QueueListener, and replace
    the originals with a single QueueHandler so the parent's own logs flow
    through the same path — preserving message ordering across processes.

    We have to install a QueueHandler on *every* logger we plundered, not
    just root: settings.yaml deliberately keeps `multi_p.propagate = false`
    so its records don't reach root, and removing its handlers without
    replacing them would silently drop every parent-side log.
    """
    handlers: list[logging.Handler] = []
    targets = (logging.getLogger(), logging.getLogger("multi_p"))
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
    parser = argparse.ArgumentParser(description="multi_p service")
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
        Multi_pService(settings.service, args.execute_dir, log_queue).run()
    finally:
        listener.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
