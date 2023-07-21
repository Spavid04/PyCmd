import abc
import enum
import typing


class FailureReason(enum.Enum):
    NotExisting = enum.auto()
    AlreadyExisting = enum.auto()
    OtherFailure = enum.auto()

class NamedPipe(abc.ABC):
    def __init__(self, name: str):
        self.Name = name
        self.Opened = False

    def Create(self) -> typing.Tuple[bool, typing.Optional[FailureReason]]: pass
    def Connect(self) -> typing.Tuple[bool, typing.Optional[FailureReason]]: pass

    def Disconnect(self): pass

    def Close(self): pass

    def __enter__(self): pass
    def __exit__(self, exc_type, exc_val, exc_tb): pass
