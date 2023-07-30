from IPC_common import *

import pywintypes
import win32event
import win32file
import win32pipe
import winerror

import concurrent.futures
import ctypes
import io


# CancelIoEx is not in pywin32
CancelIoEx = ctypes.windll.kernel32.CancelIoEx
CancelIoEx.restype = ctypes.wintypes.BOOL
CancelIoEx.argtypes = (
    ctypes.wintypes.LPVOID,  # HANDLE
    ctypes.wintypes.LPVOID   # LPOVERLAPPED
)

STATUS_PIPE_DISCONNECTED = 0xC00000B0
STATUS_PIPE_CLOSING = 0xC00000B1
STATUS_CANCELLED = 0xC0000120

class Win32NamedPipe(NamedPipe):
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

        # yeah... it's easier than implementing a custom Executor()
        self._InternalReaderThreadPool: concurrent.futures.ThreadPoolExecutor = None
        self._InternalWriterThreadPool: concurrent.futures.ThreadPoolExecutor = None

    def Create(self) -> IPCResult[bool]:
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
                return IPCFailed(FailureReason.AlreadyExisting)
            else:
                raise
        if handle == win32file.INVALID_HANDLE_VALUE:
            return IPCFailed(FailureReason.OtherFailure)

        self.PipeHandle = handle
        self.IsServer = True
        return IPCOk()

    def _serverConnect(self) -> IPCResult:
        if self._SignaledAbort:
            return IPCAborted()

        win32event.ResetEvent(self._IoEvent)
        try:
            errorcode = win32pipe.ConnectNamedPipe(self.PipeHandle, self._IoOverlapped)
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_NO_DATA:
                return IPCFailed(FailureReason.OtherSideClosed)
            elif e.winerror == winerror.ERROR_PIPE_CONNECTED:
                return IPCFailed(FailureReason.AlreadyExisting)
            else:
                raise
        if errorcode == winerror.ERROR_IO_PENDING:
            win32event.WaitForSingleObject(self._IoEvent, win32event.INFINITE)
            if self._SignaledAbort:
                return IPCAborted()
            if self._IoOverlapped.Internal == STATUS_CANCELLED:
                return IPCAborted()
            if self._IoOverlapped.Internal == STATUS_PIPE_DISCONNECTED:
                return IPCFailed(FailureReason.OtherSideClosed)
            if self._IoOverlapped.Internal == STATUS_PIPE_CLOSING:
                return IPCAborted()

        self.Opened = True
        return IPCOk()

    def _clientConnect(self) -> IPCResult:
        if self._SignaledAbort:
            return IPCAborted()

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
                return IPCFailed(FailureReason.NotExisting)
            elif e.winerror == winerror.ERROR_PIPE_BUSY:
                return IPCFailed(FailureReason.OtherSideCongested)
            else:
                raise
        if handle == win32file.INVALID_HANDLE_VALUE:
            return IPCFailed(FailureReason.OtherFailure)
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        self.PipeHandle = handle
        self.Opened = True
        return IPCOk()

    def Connect(self) -> CFuture[IPCResult]:
        self._SignaledAbort = False
        if self._InternalWriterThreadPool is None:
            self._InternalWriterThreadPool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        if self._InternalReaderThreadPool is None:
            self._InternalReaderThreadPool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        # any pool works fine here
        if self.IsServer:
            return self._InternalWriterThreadPool.submit(self._serverConnect)
        else:
            return self._InternalWriterThreadPool.submit(self._clientConnect)

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

        self._InternalReaderThreadPool.shutdown(wait=True, cancel_futures=True)
        self._InternalWriterThreadPool.shutdown(wait=True, cancel_futures=True)
        win32file.CloseHandle(self._IoEvent)
        win32file.CloseHandle(self.PipeHandle)

    def _read(self) -> IPCResult[bytes]:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")
        if self._SignaledAbort:
            return IPCAborted()

        buffer = io.BytesIO()
        win32event.ResetEvent(self._IoEvent)
        while True:
            try:
                (errorcode, data) = win32file.ReadFile(self.PipeHandle, self._IoReadBuffer, self._IoOverlapped)
            except pywintypes.error as e:
                if e.winerror == winerror.ERROR_BROKEN_PIPE:
                    return IPCFailed(FailureReason.OtherSideClosed)
                else:
                    raise
            if errorcode == winerror.ERROR_IO_PENDING:
                win32event.WaitForSingleObject(self._IoEvent, win32event.INFINITE)
                if self._SignaledAbort:
                    return IPCAborted()
                if self._IoOverlapped.Internal == STATUS_CANCELLED:
                    return IPCAborted()
                if self._IoOverlapped.Internal == STATUS_PIPE_DISCONNECTED:
                    return IPCFailed(FailureReason.OtherSideClosed)
                if self._IoOverlapped.Internal == STATUS_PIPE_CLOSING:
                    return IPCAborted()

            buffer.write(data[0:self._IoOverlapped.InternalHigh])
            moreData = (self._IoOverlapped.Internal == winerror.ERROR_MORE_DATA)
            if not moreData:
                break

        return IPCOk(buffer.getvalue())
    def Read(self) -> CFuture[IPCResult[bytes]]:
        return self._InternalReaderThreadPool.submit(self._read)

    def _write(self, data: bytes) -> IPCResult:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")
        if self._SignaledAbort:
            return IPCAborted()

        win32event.ResetEvent(self._IoEvent)
        try:
            (errorcode, written) = win32file.WriteFile(self.PipeHandle, data, self._IoOverlapped)
            win32file.FlushFileBuffers(self.PipeHandle)
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_BROKEN_PIPE:
                return IPCFailed(FailureReason.OtherSideClosed)
            elif e.winerror == winerror.ERROR_OPERATION_ABORTED:
                return IPCAborted()
            else:
                raise
        if errorcode == winerror.ERROR_IO_PENDING:
            win32event.WaitForSingleObject(self._IoEvent, win32event.INFINITE)
            if self._SignaledAbort:
                return IPCAborted()
            if self._IoOverlapped.Internal == STATUS_CANCELLED:
                return IPCAborted()
            if self._IoOverlapped.Internal == STATUS_PIPE_DISCONNECTED:
                return IPCFailed(FailureReason.OtherSideClosed)
            if self._IoOverlapped.Internal == STATUS_PIPE_CLOSING:
                return IPCAborted()

        return IPCOk()
    def Write(self, data: bytes) -> CFuture[IPCResult]:
        return self._InternalWriterThreadPool.submit(self._write, data)

    def _peek(self) -> IPCResult[bool]:
        if self.PipeHandle is None or not self.Opened:
            raise ValueError("Pipe is not open.")

        try:
            result = win32pipe.PeekNamedPipe(self.PipeHandle, 0)
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_BROKEN_PIPE:
                return IPCFailed(FailureReason.OtherSideClosed)
            else:
                raise

        if result is None:
            return IPCOk(False)
        (data, total, left) = result
        return IPCOk(len(data) > 0 or left > 0)

    def Peek(self) -> CFuture[IPCResult[bool]]:
        return self._InternalReaderThreadPool.submit(self._peek)

    def AbortPendingOperations(self):
        self._SignaledAbort = True
        if self.PipeHandle is not None:
            CancelIoEx(int(self.PipeHandle), None)

    def __enter__(self) -> ("Win32NamedPipe", CFuture[IPCResult]):
        self.Create()
        return (self, self.Connect())

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.Disconnect()
        self.Close()
