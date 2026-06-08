import logging


class MessageHandler:
    """Base class for message handlers. Override `handle` with your business logic.

    `handle` is awaited per-message; raise to indicate failure (the message
    will be left in the consumer group's PEL and retried by the reaper task,
    which will eventually push it to a dead-letter stream after max_retries).
    """

    async def handle(self, msg_id: str, fields: dict[str, str]) -> None:
        raise NotImplementedError


class EchoHandler(MessageHandler):
    """Default handler: log the message. Replace with your own subclass."""

    def __init__(self) -> None:
        self.logger = logging.getLogger("mq")

    async def handle(self, msg_id: str, fields: dict[str, str]) -> None:
        self.logger.info("got %s: %s", msg_id, fields)
