import typing

import IPC_common

import pywintypes
import win32api
import win32file
import win32pipe
import winerror

import concurrent.futures
import enum


class Win32NamedPipe(IPC_common.NamedPipe):
    def __init__(self, name: str):
        super().__init__(name)

        self._PipePath = fr"\\.\pipe\{name}"
        self.PipeHandle = None
        self._InternalThreadPool = concurrent.futures.ThreadPoolExecutor()

    def Create(self) -> typing.Tuple[bool, typing.Optional[IPC_common.FailureReason]]:
        if self.Opened:
            raise RuntimeError("Pipe is already open.")

        handle = None
        try:
            handle = win32pipe.CreateNamedPipe(
                self._PipePath,
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                1,
                65536, 65536,
                0,
                None
            )
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_PIPE_BUSY:
                return (False, IPC_common.FailureReason.AlreadyExisting)
            else:
                raise
        if handle == win32file.INVALID_HANDLE_VALUE:
            return (False, IPC_common.FailureReason.OtherFailure)

        self.PipeHandle = handle
        return (True, None)

    def _serverConnect(self) -> bool:
        result = win32pipe.ConnectNamedPipe(self.PipeHandle, None)
        if result != 0:
            self.Opened = True
        return result != 0

    def _clientConnect(self) -> bool:
        handle = win32file.CreateFile(
            self._PipePath,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None
        )
        if handle == win32file.INVALID_HANDLE_VALUE:
            return False
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        self.PipeHandle = handle
        self.Opened = True
        return True

    def Connect(self) -> concurrent.futures.Future[bool]:
        if self.PipeHandle is not None:
            return self._InternalThreadPool.submit(self._serverConnect)
        else:
            return self._InternalThreadPool.submit(self._clientConnect)

    def AbortConnect(self):
        raise NotImplementedError()

    def Disconnect(self):
        if not self.Opened:
            return
        win32pipe.DisconnectNamedPipe(self.PipeHandle)

    def Close(self):
        if self.PipeHandle is None:
            return
        win32file.CloseHandle(self.PipeHandle)

    def Read(self) -> concurrent.futures.Future[bytes]:
        pass

    def Write(self, data: bytes) -> concurrent.futures.Future:
        pass

    def _peek(self) -> bool:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")

        result = win32pipe.PeekNamedPipe(
            self.PipeHandle,
            None,
            0,
            None,
            ????
        )

    def Peek(self) -> concurrent.futures.Future[bool]:
        return self._InternalThreadPool.submit(self._peek)

    def __enter__(self): pass

    def __exit__(self, exc_type, exc_val, exc_tb): pass





if __name__ == "__main__":
    p = Win32NamedPipe("testing_pipe")
    p.Create()
