import os

if os.name == "nt":
    from IPC_win32 import *
else:
    raise NotImplementedError()

