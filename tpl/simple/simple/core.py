import logging
import signal
import time
from pathlib import Path
from types import FrameType
from typing import Any


class SimpleService:
    def __init__(self, cfg: Any, execute_dir: Path) -> None:
        self.logger = logging.getLogger("simple")
        self.cfg = cfg
        self.execute_dir = execute_dir
        self.running = False
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle_signal)

    def _handle_signal(self, sig: int, frame: FrameType | None) -> None:
        self.logger.info("received signal %d, stopping", sig)
        self.stop()
        raise SystemExit(0)

    def run(self) -> None:
        self.logger.info("simple service starting")
        self.running = True
        while self.running:
            self.logger.info("running")
            time.sleep(1)

    def stop(self) -> None:
        self.logger.info("simple service stopping")
        self.running = False
