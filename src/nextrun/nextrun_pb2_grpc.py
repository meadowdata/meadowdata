# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
"""Client and server classes corresponding to protobuf-defined services."""
import grpc

from nextrun import nextrun_pb2 as nextrun_dot_nextrun__pb2


class NextRunServerStub(object):
    """Missing associated documentation comment in .proto file."""

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.run_py_func = channel.unary_unary(
            "/nextrun.NextRunServer/run_py_func",
            request_serializer=nextrun_dot_nextrun__pb2.RunPyFuncRequest.SerializeToString,
            response_deserializer=nextrun_dot_nextrun__pb2.ProcessState.FromString,
        )
        self.get_process_states = channel.unary_unary(
            "/nextrun.NextRunServer/get_process_states",
            request_serializer=nextrun_dot_nextrun__pb2.ProcessStatesRequest.SerializeToString,
            response_deserializer=nextrun_dot_nextrun__pb2.ProcessStates.FromString,
        )


class NextRunServerServicer(object):
    """Missing associated documentation comment in .proto file."""

    def run_py_func(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Method not implemented!")
        raise NotImplementedError("Method not implemented!")

    def get_process_states(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Method not implemented!")
        raise NotImplementedError("Method not implemented!")


def add_NextRunServerServicer_to_server(servicer, server):
    rpc_method_handlers = {
        "run_py_func": grpc.unary_unary_rpc_method_handler(
            servicer.run_py_func,
            request_deserializer=nextrun_dot_nextrun__pb2.RunPyFuncRequest.FromString,
            response_serializer=nextrun_dot_nextrun__pb2.ProcessState.SerializeToString,
        ),
        "get_process_states": grpc.unary_unary_rpc_method_handler(
            servicer.get_process_states,
            request_deserializer=nextrun_dot_nextrun__pb2.ProcessStatesRequest.FromString,
            response_serializer=nextrun_dot_nextrun__pb2.ProcessStates.SerializeToString,
        ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
        "nextrun.NextRunServer", rpc_method_handlers
    )
    server.add_generic_rpc_handlers((generic_handler,))


# This class is part of an EXPERIMENTAL API.
class NextRunServer(object):
    """Missing associated documentation comment in .proto file."""

    @staticmethod
    def run_py_func(
        request,
        target,
        options=(),
        channel_credentials=None,
        call_credentials=None,
        insecure=False,
        compression=None,
        wait_for_ready=None,
        timeout=None,
        metadata=None,
    ):
        return grpc.experimental.unary_unary(
            request,
            target,
            "/nextrun.NextRunServer/run_py_func",
            nextrun_dot_nextrun__pb2.RunPyFuncRequest.SerializeToString,
            nextrun_dot_nextrun__pb2.ProcessState.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
        )

    @staticmethod
    def get_process_states(
        request,
        target,
        options=(),
        channel_credentials=None,
        call_credentials=None,
        insecure=False,
        compression=None,
        wait_for_ready=None,
        timeout=None,
        metadata=None,
    ):
        return grpc.experimental.unary_unary(
            request,
            target,
            "/nextrun.NextRunServer/get_process_states",
            nextrun_dot_nextrun__pb2.ProcessStatesRequest.SerializeToString,
            nextrun_dot_nextrun__pb2.ProcessStates.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
        )
