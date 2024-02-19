import abc
import concurrent.futures
import dataclasses
import enum
from typing import Any, Callable, Generic, Optional, TypeVar


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

class FutureResult(abc.ABC, Generic[T]):
    @abc.abstractmethod
    def wait(self, timeout: float = None): ...

    @abc.abstractmethod
    def result(self, timeout: float = None) -> T: ...

    @abc.abstractmethod
    def abort(self) -> bool: ...

    @abc.abstractmethod
    def continueWith(self, func: Callable[["FutureResult", Any], None], context: Any = None): ...

    @property
    @abc.abstractmethod
    def running(self) -> bool: ...
    @property
    @abc.abstractmethod
    def aborted(self) -> bool: ...
    @property
    @abc.abstractmethod
    def finished(self) -> bool: ...
    @property
    @abc.abstractmethod
    def exception(self) -> Optional[Exception]: ...

class NamedPipe(abc.ABC):
    @abc.abstractmethod
    def __init__(self, name: str):
        self.Name = name
        self.Opened = False

    @abc.abstractmethod
    def Create(self) -> IPCResult[bool]: ...

    @abc.abstractmethod
    def Connect(self) -> CFuture[IPCResult]: ...

    @abc.abstractmethod
    def Disconnect(self): ...

    @abc.abstractmethod
    def Close(self): ...

    @abc.abstractmethod
    def Read(self) -> CFuture[IPCResult[bytes]]: ...

    @abc.abstractmethod
    def Write(self, data: bytes) -> CFuture[IPCResult]: ...

    @abc.abstractmethod
    def Peek(self) -> CFuture[IPCResult[bool]]: ...

    @abc.abstractmethod
    def AbortPendingOperations(self): ...

    @abc.abstractmethod
    def __enter__(self) -> ("NamedPipe", CFuture[IPCResult[bool]]): ...

    @abc.abstractmethod
    def __exit__(self, exc_type, exc_val, exc_tb): ...
