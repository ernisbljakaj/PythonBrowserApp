import ctypes
import ctypes.wintypes
import os

class SingleInstance:
    def __init__(self, name="Local\\AIClipboardCloud"):
        self.handle=None
        if os.name=="nt":
            k=ctypes.windll.kernel32;k.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.wintypes.BOOL,ctypes.wintypes.LPCWSTR];k.CreateMutexW.restype=ctypes.c_void_p
            self.handle=k.CreateMutexW(None,True,name)
            self.already_running=ctypes.get_last_error()==183
        else:self.already_running=False
    def close(self):
        if self.handle:ctypes.windll.kernel32.CloseHandle(self.handle);self.handle=None
