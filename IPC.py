import os
import typing

import IPC_common
if os.name == "nt":
    import IPC_win32
    NP = IPC_win32.Win32NamedPipe
else:
    raise NotImplementedError()


class AutoresetNamedPipeStarNetwork():
    def __init__(self, networkName: str):
        self.NetworkName = networkName
        self.NPInstance: IPC_common.NamedPipe = None

        self.MessageReceivedHandlers: typing.List[typing.Callable[[bytes], ...]] = []

        self._Server_ConnectHandler: typing.Callable[[bytes], bytes] = None
        self._Client_ServerGoneHandler: typing.Callable[[bytes], bytes] = None
        self._Client_WeBecameServerHandler: typing.Callable[[bytes], bytes] = None

    def SendMessage(self, data: bytes):
        pass
    def AddMessageReceivedHandler(self, handler: typing.Callable[[bytes], ...]):
        pass


if __name__ == "__main__":
    pass
