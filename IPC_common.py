import abc
import concurrent.futures
import dataclasses
import enum
import typing


class FailureReason(enum.Enum):
    OtherFailure = enum.auto()

    NotExisting = enum.auto()
    AlreadyExisting = enum.auto()

    OtherSideCongested = enum.auto()
    OtherSideClosed = enum.auto()
@dataclasses.dataclass
class IPCResult():
    Success: bool
    Reason: typing.Optional[FailureReason]

class NamedPipe(abc.ABC):
    def __init__(self, name: str):
        self.Name = name
        self.Opened = False

    def Create(self) -> IPCResult: pass
    def Connect(self) -> concurrent.futures.Future[IPCResult]: pass
    def Disconnect(self): pass
    def Close(self): pass

    def Read(self) -> concurrent.futures.Future[bytes]: pass
    def Write(self, data: bytes, waitForRead: bool = False) -> concurrent.futures.Future: pass
    def Peek(self) -> concurrent.futures.Future[bool]: pass

    def AbortPendingOperations(self): pass

    def __enter__(self) -> ("NamedPipe", concurrent.futures.Future[IPCResult]): pass
    def __exit__(self, exc_type, exc_val, exc_tb): pass
