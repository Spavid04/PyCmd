import IPC_common

import pywintypes
import win32api
import win32event
import win32file
import win32pipe
import winerror

import concurrent.futures
import ctypes
import enum
import io
import typing


# CancelIoEx is not in pywin32
CancelIoEx = ctypes.windll.kernel32.CancelIoEx
CancelIoEx.restype = ctypes.wintypes.BOOL
CancelIoEx.argtypes = (
    ctypes.wintypes.LPVOID,
    ctypes.wintypes.LPVOID
)

STATUS_PIPE_DISCONNECTED = 0xC00000B0
STATUS_PIPE_CLOSING = 0xC00000B1
STATUS_CANCELLED = 0xC0000120

def _Ok(value = None) -> IPC_common.IPCResult:
    return IPC_common.IPCResult(True, None, value)
def _Failed(reason: IPC_common.FailureReason) -> IPC_common.IPCResult:
    return IPC_common.IPCResult(False, reason, None)
def _Aborted() -> IPC_common.IPCResult:
    return IPC_common.IPCResult(False, IPC_common.FailureReason.Aborted, None)

class Win32NamedPipe(IPC_common.NamedPipe):
    def __init__(self, name: str):
        super().__init__(name)

        self._PipePath = fr"\\.\pipe\{name}"
        self.PipeHandle = None
        self.IsServer = False

        self._IoEvent = win32event.CreateEvent(None, False, False, None)
        self._IoOverlapped = pywintypes.OVERLAPPED()
        self._IoOverlapped.hEvent = self._IoEvent
        self._IoReadBuffer = win32file.AllocateReadBuffer(4096)
        self._SignaledAbort = False

        self._InternalThreadPool: concurrent.futures.ThreadPoolExecutor = None
        self._PendingReadCount = 0 # TODO: is this needed?
        self._PendingWriteCount = 0 # TODO: is this needed?
        self._PendingConnect = False # TODO: is this needed?

    def Create(self) -> IPC_common.IPCResult:
        if self.Opened:
            raise RuntimeError("Pipe is already open.")

        handle = None
        try:
            handle = win32pipe.CreateNamedPipe(
                self._PipePath,
                win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED,
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE,
                1,
                0, 0, # no buffering, mostly so that Write() doesn't succeed immediately
                0,
                None
            )
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_PIPE_BUSY:
                return _Failed(IPC_common.FailureReason.AlreadyExisting)
            else:
                raise
        if handle == win32file.INVALID_HANDLE_VALUE:
            return _Failed(IPC_common.FailureReason.OtherFailure)

        self.PipeHandle = handle
        self.IsServer = True
        return _Ok()

    def _serverConnect(self) -> IPC_common.IPCResult:
        if self._SignaledAbort:
            return _Aborted()

        win32event.ResetEvent(self._IoEvent)
        try:
            errorcode = win32pipe.ConnectNamedPipe(self.PipeHandle, self._IoOverlapped)
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_NO_DATA:
                return _Failed(IPC_common.FailureReason.OtherSideClosed)
            elif e.winerror == winerror.ERROR_PIPE_CONNECTED:
                return _Failed(IPC_common.FailureReason.AlreadyExisting)
            else:
                raise
        if errorcode == winerror.ERROR_IO_PENDING:
            win32event.WaitForSingleObject(self._IoEvent, win32event.INFINITE)
            if self._SignaledAbort:
                return _Aborted()
            if self._IoOverlapped.Internal == STATUS_CANCELLED:
                return _Aborted()
            if self._IoOverlapped.Internal == STATUS_PIPE_DISCONNECTED:
                return _Failed(IPC_common.FailureReason.OtherSideClosed)
            if self._IoOverlapped.Internal == STATUS_PIPE_CLOSING:
                return _Aborted()

        self.Opened = True
        return _Ok()

    def _clientConnect(self) -> IPC_common.IPCResult:
        if self._SignaledAbort:
            return _Aborted()

        handle = win32file.INVALID_HANDLE_VALUE
        win32event.ResetEvent(self._IoEvent)
        try:
            handle = win32file.CreateFile(
                self._PipePath,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                win32file.FILE_FLAG_OVERLAPPED,
                None
            )
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_FILE_NOT_FOUND:
                return _Failed(IPC_common.FailureReason.NotExisting)
            elif e.winerror == winerror.ERROR_PIPE_BUSY:
                return _Failed(IPC_common.FailureReason.OtherSideCongested)
            else:
                raise
        if handle == win32file.INVALID_HANDLE_VALUE:
            return _Failed(IPC_common.FailureReason.OtherFailure)
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        self.PipeHandle = handle
        self.Opened = True
        return _Ok()

    def Connect(self) -> concurrent.futures.Future[IPC_common.IPCResult]:
        self._SignaledAbort = False
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

        self.AbortPendingOperations()
        if self.IsServer:
            win32pipe.DisconnectNamedPipe(self.PipeHandle)
        else:
            win32file.CloseHandle(self.PipeHandle)
            self.PipeHandle = None

        self.Opened = False

    def Close(self):
        if self.PipeHandle is None:
            return
        self.Disconnect()

        self._InternalThreadPool.shutdown(wait=True, cancel_futures=True)
        win32file.CloseHandle(self._IoEvent)
        win32file.CloseHandle(self.PipeHandle)

    def _read(self) -> IPC_common.IPCResult:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")
        if self._SignaledAbort:
            return _Aborted()

        self._PendingReadCount += 1
        buffer = io.BytesIO()
        win32event.ResetEvent(self._IoEvent)
        while True:
            try:
                (errorcode, data) = win32file.ReadFile(self.PipeHandle, self._IoReadBuffer, self._IoOverlapped)
            except pywintypes.error as e:
                if e.winerror == winerror.ERROR_BROKEN_PIPE:
                    return _Failed(IPC_common.FailureReason.OtherSideClosed)
                else:
                    raise
            if errorcode == winerror.ERROR_IO_PENDING:
                win32event.WaitForSingleObject(self._IoEvent, win32event.INFINITE)
                if self._SignaledAbort:
                    return _Aborted()
                if self._IoOverlapped.Internal == STATUS_CANCELLED:
                    return _Aborted()
                if self._IoOverlapped.Internal == STATUS_PIPE_DISCONNECTED:
                    return _Failed(IPC_common.FailureReason.OtherSideClosed)
                if self._IoOverlapped.Internal == STATUS_PIPE_CLOSING:
                    return _Aborted()

            buffer.write(data[0:self._IoOverlapped.InternalHigh])
            moreData = (self._IoOverlapped.Internal == winerror.ERROR_MORE_DATA)
            if not moreData:
                break

        return _Ok(buffer.getvalue())
    def Read(self) -> concurrent.futures.Future[IPC_common.IPCResult]:
        def done(_):
            self._PendingReadCount -= 1
        f = self._InternalThreadPool.submit(self._read)
        f.add_done_callback(done)
        return f

    def _write(self, data: bytes) -> IPC_common.IPCResult:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")
        if self._SignaledAbort:
            return _Aborted()

        self._PendingWriteCount += 1
        win32event.ResetEvent(self._IoEvent)
        try:
            (errorcode, written) = win32file.WriteFile(self.PipeHandle, data, self._IoOverlapped)
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_BROKEN_PIPE:
                return _Failed(IPC_common.FailureReason.OtherSideClosed)
            else:
                raise
        if errorcode == winerror.ERROR_IO_PENDING:
            win32event.WaitForSingleObject(self._IoEvent, win32event.INFINITE)
            if self._SignaledAbort:
                return _Aborted()
            if self._IoOverlapped.Internal == STATUS_CANCELLED:
                return _Aborted()
            if self._IoOverlapped.Internal == STATUS_PIPE_DISCONNECTED:
                return _Failed(IPC_common.FailureReason.OtherSideClosed)
            if self._IoOverlapped.Internal == STATUS_PIPE_CLOSING:
                return _Aborted()

        return _Ok()
    def Write(self, data: bytes) -> concurrent.futures.Future[IPC_common.IPCResult]:
        def done(_):
            self._PendingWriteCount -= 1
        f = self._InternalThreadPool.submit(self._write, data)
        f.add_done_callback(done)
        return f

    def _peek(self) -> IPC_common.IPCResult:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")

        try:
            result = win32pipe.PeekNamedPipe(self.PipeHandle, 0)
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_BROKEN_PIPE:
                return _Failed(IPC_common.FailureReason.OtherSideClosed)
            else:
                raise

        if result is None:
            return _Ok(False)
        (data, total, left) = result
        return _Ok(len(data) > 0 or left > 0)

    def Peek(self) -> concurrent.futures.Future[IPC_common.IPCResult]:
        return self._InternalThreadPool.submit(self._peek)

    def AbortPendingOperations(self):
        self._SignaledAbort = True
        if self.PipeHandle is not None:
            CancelIoEx(int(self.PipeHandle), None)

    def __enter__(self) -> ("Win32NamedPipe", concurrent.futures.Future[IPC_common.IPCResult]):
        self.Create()
        return (self, self.Connect())

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.Disconnect()
        self.Close()
