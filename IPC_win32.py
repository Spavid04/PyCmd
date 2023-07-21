import IPC_common

import pywintypes
import win32api
import win32file
import win32pipe


class Win32NamedPipe(IPC_common.NamedPipe):
    def __init__(self, name: str):
        super().__init__(name)

        self.PipeHandle = None

    def Open(self) -> bool:
        if self.Opened:
            raise RuntimeError("Pipe is already open.")

        handle = win32pipe.CreateNamedPipe(
            fr"\\.\pipe\{self.Name}",
            win32pipe.PIPE_ACCESS_DUPLEX | win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            1,
            65536, 65536,
            0,
            None)

        if handle == win32file.INVALID_HANDLE_VALUE:
            last_error = win32api.GetLastError()
            raise OSError(last_error)

        pass

    def __enter__(self): pass

    def __exit__(self, exc_type, exc_val, exc_tb): pass





if __name__ == "__main__":
    p = Win32NamedPipe("testing_pipe")
    p.Open()
