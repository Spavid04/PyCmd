import IPC_common

import pywintypes
import win32api
import win32file
import win32pipe
import winerror

import concurrent.futures
import enum
import io
import typing


class Win32NamedPipe(IPC_common.NamedPipe):
    def __init__(self, name: str):
        super().__init__(name)

        self._PipePath = fr"\\.\pipe\{name}"
        self.PipeHandle = None
        self.IsServer = False

        self._InternalThreadPool: concurrent.futures.ThreadPoolExecutor = None
        self._PendingReadCount = 0
        self._PendingWriteCount = 0
        self._PendingConnect = False

    def Create(self) -> IPC_common.IPCResult:
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
                return IPC_common.IPCResult(False, IPC_common.FailureReason.AlreadyExisting, None)
            else:
                raise
        if handle == win32file.INVALID_HANDLE_VALUE:
            return IPC_common.IPCResult(False, IPC_common.FailureReason.OtherFailure, None)

        self.PipeHandle = handle
        self.IsServer = True
        return IPC_common.IPCResult(True, None, None)

    def _serverConnect(self) -> IPC_common.IPCResult:
        try:
            win32pipe.ConnectNamedPipe(self.PipeHandle, None)
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_NO_DATA:
                return IPC_common.IPCResult(False, IPC_common.FailureReason.OtherSideClosed, None)
            elif e.winerror == winerror.ERROR_PIPE_CONNECTED:
                return IPC_common.IPCResult(False, IPC_common.FailureReason.AlreadyExisting, None)
            else:
                raise

        self.Opened = True
        return IPC_common.IPCResult(True, None, None)

    def _clientConnect(self) -> IPC_common.IPCResult:
        handle = win32file.INVALID_HANDLE_VALUE
        try:
            handle = win32file.CreateFile(
                self._PipePath,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None
            )
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_FILE_NOT_FOUND:
                return IPC_common.IPCResult(False, IPC_common.FailureReason.NotExisting, None)
            elif e.winerror == winerror.ERROR_PIPE_BUSY:
                return IPC_common.IPCResult(False, IPC_common.FailureReason.OtherSideCongested, None)
            else:
                raise
        if handle == win32file.INVALID_HANDLE_VALUE:
            return IPC_common.IPCResult(False, IPC_common.FailureReason.OtherFailure, None)
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        self.PipeHandle = handle
        self.Opened = True
        return IPC_common.IPCResult(True, None, None)

    def Connect(self) -> concurrent.futures.Future[IPC_common.IPCResult]:
        if self._InternalThreadPool is None:
            self._InternalThreadPool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        self._PendingConnect = True
        if self.IsServer:
            f = self._InternalThreadPool.submit(self._serverConnect)
        else:
            f = self._InternalThreadPool.submit(self._clientConnect)

        def done(_):
            self._PendingConnect = False
        f.add_done_callback(done)
        return f

    def Disconnect(self):
        if not self.Opened:
            return
        if self.IsServer:
            win32pipe.DisconnectNamedPipe(self.PipeHandle)
        else:
            self.Close()
        self.Opened = False

    def Close(self):
        if self.PipeHandle is None:
            return
        self._InternalThreadPool.shutdown(wait=False, cancel_futures=True)
        self.AbortPendingOperations()
        self._InternalThreadPool = None
        self.Opened = False

        win32file.CloseHandle(self.PipeHandle)
        self.PipeHandle = None

    def _read(self) -> IPC_common.IPCResult:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")

        self._PendingReadCount += 1
        buffer = io.BytesIO()
        while True:
            try:
                (errorcode, data) = win32file.ReadFile(self.PipeHandle, 4096, None)
            except pywintypes.error as e:
                if e.winerror == winerror.ERROR_BROKEN_PIPE:
                    return IPC_common.IPCResult(False, IPC_common.FailureReason.OtherSideClosed, None)
                else:
                    raise
            buffer.write(data)
            if errorcode == 0:
                break
        return IPC_common.IPCResult(True, None, buffer.getvalue())
    def Read(self) -> concurrent.futures.Future[IPC_common.IPCResult]:
        def done(_):
            self._PendingReadCount -= 1
        f = self._InternalThreadPool.submit(self._read)
        f.add_done_callback(done)
        return f

    def _write(self, data: bytes, waitForRead: bool) -> IPC_common.IPCResult:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")

        self._PendingWriteCount += 1
        try:
            (errorcode, written) = win32file.WriteFile(self.PipeHandle, data, None)
            if waitForRead:
                win32file.FlushFileBuffers(self.PipeHandle)
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_BROKEN_PIPE:
                return IPC_common.IPCResult(False, IPC_common.FailureReason.OtherSideClosed, None)
            else:
                raise
        return IPC_common.IPCResult(True, None, None)
    def Write(self, data: bytes, waitForRead: bool = False) -> concurrent.futures.Future[IPC_common.IPCResult]:
        def done(_):
            self._PendingWriteCount -= 1
        f = self._InternalThreadPool.submit(self._write, data, waitForRead)
        f.add_done_callback(done)
        return f

    def _peek(self) -> IPC_common.IPCResult:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")

        try:
            result = win32pipe.PeekNamedPipe(self.PipeHandle, 0)
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_BROKEN_PIPE:
                return IPC_common.IPCResult(False, IPC_common.FailureReason.OtherSideClosed, None)
            else:
                raise

        if result is None:
            return IPC_common.IPCResult(True, None, False)
        (data, total, left) = result
        return IPC_common.IPCResult(True, None, len(data) > 0 or left > 0)

    def Peek(self) -> concurrent.futures.Future[IPC_common.IPCResult]:
        return self._InternalThreadPool.submit(self._peek)

    def AbortPendingOperations(self):
        if self._PendingConnect:
            pass
        #with Win32NamedPipe(self.Name) as (throwawayPipe, connectdFuture):
        #    connectdFuture.result()

    def __enter__(self) -> ("Win32NamedPipe", concurrent.futures.Future[IPC_common.IPCResult]):
        self.Create()
        return (self, self.Connect())

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.Disconnect()
        self.Close()
