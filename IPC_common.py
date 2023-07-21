import abc
import enum
import typing


class NamedPipe(abc.ABC):
    def __init__(self, name: str):
        self.Name = name
        self.Opened = False

    def Open(self) -> bool: pass

    def __enter__(self): pass
    def __exit__(self, exc_type, exc_val, exc_tb): pass
