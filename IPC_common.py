import abc
import concurrent.futures
import dataclasses
import enum
from typing import Generic, Optional, TypeVar


CFuture = concurrent.futures.Future
T = TypeVar("T")

class FailureReason(enum.Enum):
    OtherFailure = enum.auto()
    Aborted = enum.auto()

    NotExisting = enum.auto()
    AlreadyExisting = enum.auto()

    OtherSideCongested = enum.auto()
    OtherSideClosed = enum.auto()
@dataclasses.dataclass
class IPCResult(Generic[T]):
    Success: bool
    Reason: Optional[FailureReason]
    Value: T

    def __bool__(self):
        return self.Success

def IPCOk(value = None) -> IPCResult:
    return IPCResult(True, None, value)
def IPCFailed(reason: FailureReason) -> IPCResult:
    return IPCResult(False, reason, None)
def IPCAborted() -> IPCResult:
    return IPCResult(False, FailureReason.Aborted, None)

class NamedPipe(abc.ABC):
    def __init__(self, name: str):
        self.Name = name
        self.Opened = False

    def Create(self) -> IPCResult[bool]: pass
    def Connect(self) -> CFuture[IPCResult]: pass
    def Disconnect(self): pass
    def Close(self): pass

    def Read(self) -> CFuture[IPCResult[bytes]]: pass
    def Write(self, data: bytes) -> CFuture[IPCResult]: pass
    def Peek(self) -> CFuture[IPCResult[bool]]: pass

    def AbortPendingOperations(self): pass

    def __enter__(self) -> ("NamedPipe", CFuture[IPCResult[bool]]): pass
    def __exit__(self, exc_type, exc_val, exc_tb): pass
