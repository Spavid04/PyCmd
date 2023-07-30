# todo: when the server goes away, we shouldn't need to recreate the client pipes; only the client that gets promoted closes its pipe

import concurrent.futures
import collections
import dataclasses
import os
import threading
import time
import typing

from IPC_common import *
if os.name == "nt":
    import IPC_win32
    NP = IPC_win32.Win32NamedPipe
else:
    raise NotImplementedError()


def _ResultIsUnrecoverable(result: IPCResult) -> bool:
    return result.Reason == FailureReason.OtherFailure or result.Reason == FailureReason.Aborted
def _DisposePipes(*pipes: NP):
    for p in pipes:
        p.Disconnect()
        p.Close()
@dataclasses.dataclass()
class _PackedRW():
    Connected: bool
    FConnect: typing.Optional[CFuture[IPCResult[bool]]]
    FInbound: typing.Optional[CFuture[IPCResult[bytes]]]
    FOutbound: typing.Optional[CFuture[IPCResult]]
    OutboundQueue: collections.deque[bytes]
def _FutureOf(pipes: typing.Dict[NP, _PackedRW], f) -> (typing.Optional[NP], _PackedRW):
    return next(((p, rwq) for (p, rwq) in pipes.items() if rwq.FInbound == f or rwq.FOutbound == f or rwq.FConnect == f), None)

class AutoresetNamedPipeStarNetwork():
    def __init__(self, networkName: str):
        self.NetworkName = networkName
        self.NetworkInstanceId = str(os.getpid())

        self.IsServer = False
        self.OurPipe: NP = None

        # server only
        self.PipesToClients: typing.Dict[NP, _PackedRW] = dict()

        # client only
        self._OutboundQueue: collections.deque[bytes] = collections.deque()

        self._RunnerThread: threading.Thread = None
        self._SignaledAbort = False

        self.MessageReceivedHandlers: typing.List[typing.Callable[[bytes], ...]] = []

        self._MessageAvailableFromUs = threading.Semaphore(value=0)
        self._MessageAvailableThreadpool: concurrent.futures.ThreadPoolExecutor = None

    def _GetPipeName(self, id: str) -> str:
        return f"{self.NetworkName}_star_{id}"

    def Start(self):
        if self._RunnerThread is not None:
            return
        while self._MessageAvailableFromUs.acquire(timeout=0): pass # clear the semaphore
        self._MessageAvailableThreadpool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._RunnerThread = threading.Thread(target=self._MainLoop, daemon=True)
        self._RunnerThread.start()

    def Stop(self):
        self._SignaledAbort = True
        if self.OurPipe:
            self.OurPipe.AbortPendingOperations()
        for pipe in self.PipesToClients:
            pipe.AbortPendingOperations()
        _DisposePipes(self.OurPipe, *self.PipesToClients.keys())
        self.PipesToClients.clear()
        self._MessageAvailableFromUs.release()
        self._MessageAvailableThreadpool.shutdown(wait=True, cancel_futures=True)
        self._MessageAvailableThreadpool = None
        self._RunnerThread = None

    def _MainLoop(self):
        while True:
            if self._SignaledAbort:
                break

            shouldReconnectToServer = False
            if self.OurPipe is None:
                shouldReconnectToServer = True
            else:
                if self.IsServer:
                    # what
                    raise Exception("Invalid state")

                result = self.OurPipe.Peek().result()
                if not result:
                    _DisposePipes(self.OurPipe)
                    self.OurPipe = None
                    self.IsServer = False
                    if _ResultIsUnrecoverable(result):
                        break
                    else:
                        shouldReconnectToServer = True

            if shouldReconnectToServer:
                (result, pipe) = self._ConnectOrPromoteToServer()
                if not result:
                    raise Exception("Failed to promote or connect to server")

                if result == "promoted":
                    self.IsServer = True
                    self.OurPipe = pipe
                elif result == "connected":
                    self.IsServer = False
                    self.OurPipe = self._ClientReverseConnectServer(pipe)
                    _DisposePipes(pipe)
                else:
                    raise Exception("Invalid state")

            if self.IsServer:
                self._ServerLoop()
            else:
                self._ClientLoop()

    def _ConnectOrPromoteToServer(self, retries=5) -> (typing.Literal["connected", "promoted", False], typing.Optional[NP]):
        for i in range(retries):
            pipe = NP(self._GetPipeName("server"))

            if pipe.Create():
                # promoted to server
                return ("promoted", pipe)

            # server exists already; connect to it
            result = pipe.Connect().result()
            if result:
                # connection ok
                return ("connected", pipe)
            elif result.Reason == FailureReason.OtherSideCongested:
                # timeout for a bit
                time.sleep(0.01)
            elif _ResultIsUnrecoverable(result):
                return (False, None)

            pipe.Close()
        return (False, None)

    def _ClientReverseConnectServer(self, pipeToServer: NP) -> NP:
        # we have to open a new pipe and tell the server to connect to it
        ourPipe = NP(self._GetPipeName(self.NetworkInstanceId))
        if not ourPipe.Create():
            raise Exception("Invalid state")

        pipeToServer.Write(self.NetworkInstanceId.encode("utf-8")).result()
        return ourPipe

    def _ServerLoop(self):
        serverFConnect: CFuture[IPCResult[bool]] = None
        fMessageAvailable: CFuture[IPCResult] = None

        while True:
            # we have 4 things to await: new message from us, new client connected or send/recv message from network

            if fMessageAvailable is None:
                fMessageAvailable = self._MessageAvailableThreadpool.submit(self._MessageAvailableFutureSource)

            if serverFConnect is None:
                serverFConnect = self.OurPipe.Connect()
                """
                                    if not result:
                                        if _ResultIsUnrecoverable(result):
                                            # most likely aborted
                                            self.OurPipe.Disconnect()
                                            self.OurPipe.Close()
                                            self.OurPipe = None
                                            for np in self.PipesToClients:
                                                np.Disconnect()
                                                np.Close()
                                            self.PipesToClients.clear()
                                            return
                                        else:
                                            # try again
                                            continue
                                    # new client, connect to it
                """
            for (clientPipe, rwq) in self.PipesToClients.items():
                if rwq.Connected:
                    if rwq.FInbound is None:
                        rwq.FInbound = clientPipe.Read()
                    if rwq.FOutbound is None and len(rwq.OutboundQueue) > 0:
                        rwq.FOutbound = clientPipe.Write(rwq.OutboundQueue[0])
                elif rwq.FConnect is None:
                    rwq.FConnect = clientPipe.Connect()

            awaitableFutures = [fMessageAvailable, serverFConnect]
            for rwq in self.PipesToClients.values():
                awaitableFutures.append(rwq.FConnect)
                awaitableFutures.append(rwq.FInbound)
                awaitableFutures.append(rwq.FOutbound)

            (done, _) = concurrent.futures.wait((x for x in awaitableFutures if x is not None), return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                result: IPCResult = f.result()

                if f == fMessageAvailable:
                    # just loop again
                    fMessageAvailable = None
                    continue

                if _ResultIsUnrecoverable(result):
                    _DisposePipes(self.OurPipe, *self.PipesToClients.keys())
                    self.OurPipe = None
                    self.PipesToClients.clear()
                    return
                elif not result and (result.Reason == FailureReason.OtherSideClosed or result.Reason == FailureReason.NotExisting):
                    if f == serverFConnect:
                        # connect "too slow"; ignore and retry
                        serverFConnect = None
                    else:
                        # client went away
                        (pipe, _) = _FutureOf(self.PipesToClients, f)
                        self.PipesToClients.pop(pipe)
                        _DisposePipes(pipe)
                    continue
                elif not result:
                    raise Exception("Invalid state")

                # a connect, read or write succeeded; find which and which pipe
                if f == serverFConnect:
                    # new client
                    pipe = self._ConnectToClient()
                    self.PipesToClients[pipe] = _PackedRW(False, pipe.Connect(), None, None, collections.deque())
                    self.OurPipe.Disconnect()
                    serverFConnect = None
                else:
                    # event from client
                    (pipe, rwq) = _FutureOf(self.PipesToClients, f)
                    if f == rwq.FInbound:
                        self._MessageReceivedEvent(result.Value)
                        self._SendMessageInternal(result.Value, pipe)
                        rwq.FInbound = None
                    elif f == rwq.FOutbound:
                        rwq.OutboundQueue.popleft()
                        rwq.FOutbound = None
                    elif f == rwq.FConnect:
                        rwq.Connected = True
                        rwq.FConnect = None
                    else:
                        raise Exception("Invalid state")

    def _ConnectToClient(self) -> NP:
        result = self.OurPipe.Read().result()
        pid = result.Value.decode("utf-8")
        newPipe = NP(self._GetPipeName(pid))
        return newPipe

    def _ClientLoop(self):
        result = self.OurPipe.Connect().result()
        if not result:
            _DisposePipes(self.OurPipe)
            self.OurPipe = None
            return

        fInbound: CFuture[IPCResult[bytes]] = None
        fOutbound: CFuture[IPCResult] = None
        fMessageAvailable: CFuture[IPCResult] = None

        # connected to server, normal loop: send/recv message from network
        while True:
            if fInbound is None:
                fInbound = self.OurPipe.Read()
            if fOutbound is None and len(self._OutboundQueue) > 0:
                fOutbound = self.OurPipe.Write(self._OutboundQueue[0])
            if fMessageAvailable is None:
                fMessageAvailable = self._MessageAvailableThreadpool.submit(self._MessageAvailableFutureSource)

            awaitableFutures = [fMessageAvailable, fInbound]
            if fOutbound is not None:
                awaitableFutures.append(fInbound)
            (done, _) = concurrent.futures.wait(awaitableFutures, return_when=concurrent.futures.FIRST_COMPLETED)

            unrecoverable = False
            for f in done:
                result: IPCResult = f.result()

                if f == fMessageAvailable:
                    # just loop again
                    fMessageAvailable = None
                    continue

                # we are only interested whether the send/recv succeeded
                if not result:
                    unrecoverable = True
                    break
                else:
                    if f == fInbound:
                        self._MessageReceivedEvent(result.Value)
                        fInbound = None
                    else:
                        self._OutboundQueue.popleft()
                        fOutbound = None
            if unrecoverable:
                _DisposePipes(self.OurPipe)
                self.OurPipe = None
                return

    def RegisterMessageReceivedEventHandler(self, handler: typing.Callable[[bytes], ...]):
        self.MessageReceivedHandlers.append(handler)

    def _MessageReceivedEvent(self, data: bytes):
        for handler in self.MessageReceivedHandlers:
            handler(data)

    def _MessageAvailableFutureSource(self) -> IPCResult:
        if self._SignaledAbort:
            return IPCAborted()
        self._MessageAvailableFromUs.acquire()
        return IPCOk()

    def _SendMessageInternal(self, data: bytes, exceptPipe: NP = None):
        if self.IsServer:
            for (pipe, rwq) in self.PipesToClients.items():
                if pipe == exceptPipe:
                    continue
                rwq.OutboundQueue.append(data)
        else:
            self._OutboundQueue.append(data)
        self._MessageAvailableFromUs.release()
    def SendMessage(self, data: bytes):
        return self._SendMessageInternal(data, None)


if __name__ == "__main__":
    net1 = AutoresetNamedPipeStarNetwork("testing")
    net1.NetworkInstanceId = "net1"
    net1.RegisterMessageReceivedEventHandler(lambda x: print("1: " + x.decode("utf-8")))

    net2 = AutoresetNamedPipeStarNetwork("testing")
    net2.NetworkInstanceId = "net2"
    net2.RegisterMessageReceivedEventHandler(lambda x: print("2: " + x.decode("utf-8")))

    net3 = AutoresetNamedPipeStarNetwork("testing")
    net3.NetworkInstanceId = "net3"
    net3.RegisterMessageReceivedEventHandler(lambda x: print("3: " + x.decode("utf-8")))

    net1.Start()
    net2.Start()
    net3.Start()

    time.sleep(1)

    net1.SendMessage(b"from net1")
    net2.SendMessage(b"from net2")
    net2.SendMessage(b"from net3")
    input()
