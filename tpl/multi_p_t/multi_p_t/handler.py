import logging

from multi_p_t.pb.tsvc import PingService


class PingHandler:
    """Apache Thrift handler for PingService.

    Thrift dispatches RPCs by method name. Method signatures must match
    the .thrift IDL — argument names, return types. The handler does not
    inherit from PingService.Iface explicitly (it's duck-typed against
    the Processor), but you can subclass `PingService.Iface` for IDE
    completion and mypy support.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("multi_p_t.handler")

    def Ping(self) -> str:
        return "pong"

    def Echo(self, message: str) -> str:
        self.logger.info("echo: %s", message)
        return message


def make_processor() -> "PingService.Processor":
    return PingService.Processor(PingHandler())
