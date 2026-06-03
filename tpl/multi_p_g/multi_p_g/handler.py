import logging

from multi_p_g.pb import service_pb2, service_pb2_grpc


class PingServicer(service_pb2_grpc.PingServiceServicer):
    """gRPC servicer implementation. One instance per worker process."""

    def __init__(self) -> None:
        self.logger = logging.getLogger("multi_p_g.handler")

    def Ping(
        self,
        request: service_pb2.PingRequest,
        context: object,
    ) -> service_pb2.PongResponse:
        return service_pb2.PongResponse(message="pong")

    def Echo(
        self,
        request: service_pb2.EchoRequest,
        context: object,
    ) -> service_pb2.EchoResponse:
        self.logger.info("echo: %s", request.message)
        return service_pb2.EchoResponse(text=request.message)
