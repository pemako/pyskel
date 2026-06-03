import logging
from typing import Any


class Service:
    """Business logic. Stateless across requests in this template — but a
    real service usually owns clients (DB pool, HTTP clients, caches) and
    methods that combine them."""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.logger = logging.getLogger("multi_p_h")

    def ping(self) -> str:
        return "pong"

    def echo(self, message: str) -> str:
        self.logger.info("echo: %s", message)
        return message
