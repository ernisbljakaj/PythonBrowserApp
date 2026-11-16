#!/usr/bin/env python3
"""Automatische, lokale Ollama-Zwischenablage fuer Wayland und Windows."""
from __future__ import annotations
import ctypes, ctypes.wintypes, ipaddress, json, locale, os, re, shutil, subprocess, sys, threading, time
import urllib.request, urllib.error
from dataclasses import dataclass
from typing import Callable, Optional

URL="http://127.0.0.1:11434"; FALLBACK="qwen2.5:0.5b"; MAX_TEXT=12000
PROMPT="Antworte auf Deutsch, knapp und korrekt. Erfinde keine Fakten. Linux-Tipps zuerst sicher; kill -9 nur zuletzt."

def memory(available=False):
    if os.name=="nt":
        class S(ctypes.Structure): _fields_=[("n",ctypes.c_ulong),("load",ctypes.c_ulong),("total",ctypes.c_ulonglong),("avail",ctypes.c_ulonglong)]
        s=S(); s.n=ctypes.sizeof(s)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)): return (s.avail if available else s.total)/2**30
    try:
        key="MemAvailable:" if available else "MemTotal:"
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith(key): return int(line.split()[1])/1048576
    except OSError: pass
    return 4

def subnet(text):
    if not (re.search(r"\b(subnetz|broadcast|netzwerkadresse|hostbereich|subnetzmaske|cidr)\b",text,re.I) or re.search(r"\b(berechne|rechne)\b",text,re.I) and "/" in text): return None
    ips=re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b",text); cidr=re.search(r"/(\d{1,2})\b",text)
    if not ips or (not cidr and len(ips)<2): return None
    try: n=ipaddress.IPv4Network(f"{ips[0]}/{cidr.group(1) if cidr else ips[1]}",strict=False)
    except ValueError: return None
    if n.prefixlen<=30: first,last,usable=n.network_address+1,n.broadcast_address-1,n.num_addresses-2
    elif n.prefixlen==31: first,last,usable=n.network_address,n.broadcast_address,2
    else: first=last=n.network_address; usable=1
    return f"Netzwerkadresse: {n.network_address}\nBroadcast-Adresse: {n.broadcast_address}\nSubnetzmaske: {n.netmask}\nCIDR: /{n.prefixlen}\nErster Host: {first}\nLetzter Host: {last}\nNutzbare Hosts: {usable}"

class Ollama:
    def api(self,path,payload=None,timeout=30):
        r=urllib.request.Request(URL+path, data=json.dumps(payload).encode() if payload else None, headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(r,timeout=timeout) as response:return json.load(response)
    def start(self):
        try:self.api("/api/tags",timeout=2); print("[OK] Ollama läuft bereits."); return
        except (OSError,urllib.error.URLError,TimeoutError):pass
        exe=shutil.which("ollama")
        if not exe: raise RuntimeError("Ollama ist nicht installiert oder nicht im PATH.")
        print("[INFO] Starte Ollama …"); kw={"stdin":subprocess.DEVNULL,"stdout":subprocess.DEVNULL,"stderr":subprocess.DEVNULL}
        if os.name=="nt":kw["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP
        else:kw["start_new_session"]=True
        subprocess.Popen([exe,"serve"],**kw)
        for _ in range(30):
            try:self.api("/api/tags",timeout=2); return
            except (OSError,urllib.error.URLError,TimeoutError):time.sleep(1)
        raise RuntimeError("Ollama konnte nicht gestartet werden.")
    def model(self):
        models=self.api("/api/tags").get("models",[]); names={m.get("name") for m in models}
        wanted="qwen2.5:0.5b" if memory()<6 else "qwen3:1.7b" if memory()<12 else "qwen3:4b" if memory()<24 else "qwen3:8b"
        if wanted in names:return wanted
        if models:
            fit=[m for m in models if m.get("size",0)<max(.5,(memory()-2)*.55)*2**30]
            return max(fit or models,key=lambda m:m.get("size",0))["name"]
        print(f"[INFO] Lade {FALLBACK} …"); subprocess.run(["ollama","pull",FALLBACK],check=True); return FALLBACK
    def ask(self,model,text):
        if result:=subnet(text):return result
        p={"model":model,"messages":[{"role":"system","content":PROMPT},{"role":"user","content":text}],"stream":False,"keep_alive":"2m","options":{"temperature":.1,"num_ctx":4096 if memory(True)>=4 else 2048}}
        if model.startswith("qwen3:"):p["think"]=bool(re.search(r"\b(berechne|beweise|analysiere|debug)\b",text,re.I)) or len(text)>250
        return self.api("/api/chat",p,300)["message"]["content"].strip()

class Clipboard:
    def read(self):raise NotImplementedError
    def write(self,text):raise NotImplementedError
    def start(self,callback):raise NotImplementedError
    def stop(self):pass

class Wayland(Clipboard):
    """GNOME-kompatibles Clipboard-Polling ohne data-control-Watcher."""
    POLL_SECONDS=.3
    WARNING_INTERVAL=30.0

    def __init__(self):
        self.done=threading.Event(); self.thread=None
        self._last_warning=None; self._last_warning_at=0.0

    def _warn_once(self,message):
        now=time.monotonic()
        if message!=self._last_warning or now-self._last_warning_at>=self.WARNING_INTERVAL:
            print(f"[WAYLAND-CLIPBOARD] {message}",file=sys.stderr)
            self._last_warning,self._last_warning_at=message,now

    def _decode_text(self,data):
        """Dekodiert falsch deklarierte text/plain-Daten ohne den Poller zu stoppen."""
        try:return data.decode("utf-8")
        except UnicodeDecodeError:
            preferred=locale.getpreferredencoding(False).lower()
            for encoding in (preferred,"cp1252","latin-1"):
                if encoding=="utf-8":continue
                try:
                    text=data.decode(encoding)
                    self._warn_once(f"Ungültiges UTF-8; Zwischenablage als {encoding} gelesen.")
                    return text
                except UnicodeDecodeError:pass
            # latin-1 sollte immer funktionieren; dieser Pfad ist reine Absicherung.
            self._warn_once("Ungültige Textdaten; Ersatzzeichen wurden verwendet.")
            return data.decode("utf-8",errors="replace")

    def read(self):
        try:
            p=subprocess.run(["wl-paste","--no-newline","--type","text/plain"],capture_output=True,timeout=1)
            if p.returncode!=0:
                self._warn_once(f"wl-paste endete mit Status {p.returncode}.")
                return None
            return self._decode_text(p.stdout)
        except subprocess.TimeoutExpired:
            self._warn_once("wl-paste-Zeitüberschreitung; Polling wird fortgesetzt.")
            return None
        except OSError as e:
            self._warn_once(f"wl-paste konnte nicht gelesen werden: {e}")
            return None
        except Exception as e:
            self._warn_once(f"Unerwarteter Lesefehler: {e}")
            return None
    def write(self,text):subprocess.run(["wl-copy","--type","text/plain;charset=utf-8"],input=text,text=True,check=True,timeout=5)
    def start(self,callback):
        if not shutil.which("wl-paste") or not shutil.which("wl-copy"):raise RuntimeError("wl-clipboard fehlt: sudo dnf install wl-clipboard")
        def run():
            # GNOME unterstützt nicht das data-control-Protokoll, das
            # wl-paste --watch verlangt. Ein einzelner, schlafender Thread
            # fragt deshalb nur die normale Zwischenablage ab.
            previous=self.read()
            while not self.done.is_set():
                if self.done.wait(self.POLL_SECONDS):break
                previous=self._poll_once(previous,callback)
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()

    def _poll_once(self,previous,callback):
        current=self.read()
        if current!=previous:
            callback()
        return current

    def stop(self):
        self.done.set()
        if self.thread:self.thread.join(2)

class Windows(Clipboard):
    """Ereignisgesteuerter Win32-Backend; keine Polling-Schleife."""
    CF_UNICODETEXT=13
    GMEM_MOVEABLE=0x0002
    WM_CLIPBOARDUPDATE=0x031D
    WM_CLOSE=0x0010
    WM_DESTROY=0x0002

    def __init__(self):
        if os.name!="nt":raise RuntimeError("Das Windows-Backend kann nur unter Windows gestartet werden.")
        self.hwnd=None;self.thread=None;self._wndproc=None;self._wndclass=None
        self.u,self.k=ctypes.windll.user32,ctypes.windll.kernel32
        self._bind_winapi()

    def _bind_winapi(self):
        """Bindet Zeiger- und Handle-Werte auch auf 64-Bit-Windows korrekt."""
        u,k=self.u,self.k; BOOL=ctypes.wintypes.BOOL; HWND=ctypes.wintypes.HWND
        HANDLE=ctypes.c_void_p; SIZE_T=ctypes.c_size_t; UINT=ctypes.wintypes.UINT
        u.OpenClipboard.argtypes=[HWND];u.OpenClipboard.restype=BOOL
        u.CloseClipboard.argtypes=[];u.CloseClipboard.restype=BOOL
        u.EmptyClipboard.argtypes=[];u.EmptyClipboard.restype=BOOL
        u.GetClipboardData.argtypes=[UINT];u.GetClipboardData.restype=HANDLE
        u.SetClipboardData.argtypes=[UINT,HANDLE];u.SetClipboardData.restype=HANDLE
        u.AddClipboardFormatListener.argtypes=[HWND];u.AddClipboardFormatListener.restype=BOOL
        u.RemoveClipboardFormatListener.argtypes=[HWND];u.RemoveClipboardFormatListener.restype=BOOL
        u.PostMessageW.argtypes=[HWND,UINT,ctypes.c_size_t,ctypes.c_ssize_t];u.PostMessageW.restype=BOOL
        u.PostQuitMessage.argtypes=[ctypes.c_int];u.PostQuitMessage.restype=None
        u.DefWindowProcW.argtypes=[HWND,UINT,ctypes.c_size_t,ctypes.c_ssize_t];u.DefWindowProcW.restype=ctypes.c_ssize_t
        u.GetMessageW.argtypes=[ctypes.POINTER(ctypes.wintypes.MSG),HWND,UINT,UINT];u.GetMessageW.restype=ctypes.c_int
        u.TranslateMessage.argtypes=[ctypes.POINTER(ctypes.wintypes.MSG)];u.TranslateMessage.restype=BOOL
        u.DispatchMessageW.argtypes=[ctypes.POINTER(ctypes.wintypes.MSG)];u.DispatchMessageW.restype=ctypes.c_ssize_t
        k.GlobalAlloc.argtypes=[UINT,SIZE_T];k.GlobalAlloc.restype=HANDLE
        k.GlobalLock.argtypes=[HANDLE];k.GlobalLock.restype=ctypes.c_void_p
        k.GlobalUnlock.argtypes=[HANDLE];k.GlobalUnlock.restype=BOOL
        k.GlobalFree.argtypes=[HANDLE];k.GlobalFree.restype=HANDLE
        k.GetModuleHandleW.argtypes=[ctypes.wintypes.LPCWSTR];k.GetModuleHandleW.restype=ctypes.wintypes.HMODULE

    def _open(self):
        for _ in range(10):
            if self.u.OpenClipboard(None):return
            time.sleep(.02)
        raise RuntimeError("Windows-Zwischenablage ist belegt.")

    def read(self):
        try:self._open()
        except RuntimeError:return None
        try:
            h=self.u.GetClipboardData(self.CF_UNICODETEXT);p=self.k.GlobalLock(h) if h else None
            try:return ctypes.wstring_at(p) if p else None
            finally:
                if p:self.k.GlobalUnlock(h)
        finally:self.u.CloseClipboard()

    def write(self,text):
        self._open();h=None
        try:
            if not self.u.EmptyClipboard():raise RuntimeError("EmptyClipboard fehlgeschlagen.")
            b=ctypes.create_unicode_buffer(text);h=self.k.GlobalAlloc(self.GMEM_MOVEABLE,ctypes.sizeof(b))
            if not h:raise MemoryError("GlobalAlloc fehlgeschlagen.")
            p=self.k.GlobalLock(h)
            if not p:raise MemoryError("GlobalLock fehlgeschlagen.")
            try:ctypes.memmove(p,b,ctypes.sizeof(b))
            finally:self.k.GlobalUnlock(h)
            if not self.u.SetClipboardData(self.CF_UNICODETEXT,h):raise RuntimeError("SetClipboardData fehlgeschlagen.")
            h=None # Nach Erfolg besitzt Windows das Handle.
        finally:
            if h:self.k.GlobalFree(h)
            self.u.CloseClipboard()

    def start(self,callback):
        def run():
            u,k=self.u,self.k;P=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,ctypes.wintypes.HWND,ctypes.wintypes.UINT,ctypes.c_size_t,ctypes.c_ssize_t)
            @P
            def proc(h,m,w,l):
                if m==self.WM_CLIPBOARDUPDATE:callback()
                if m==self.WM_DESTROY:u.PostQuitMessage(0)
                return u.DefWindowProcW(h,m,w,l)
            class C(ctypes.Structure):_fields_=[("style",ctypes.wintypes.UINT),("lpfnWndProc",P),("cbClsExtra",ctypes.c_int),("cbWndExtra",ctypes.c_int),("hInstance",ctypes.wintypes.HINSTANCE),("hIcon",ctypes.c_void_p),("hCursor",ctypes.c_void_p),("hbrBackground",ctypes.c_void_p),("lpszMenuName",ctypes.wintypes.LPCWSTR),("lpszClassName",ctypes.wintypes.LPCWSTR)]
            u.RegisterClassW.argtypes=[ctypes.POINTER(C)];u.RegisterClassW.restype=ctypes.wintypes.ATOM
            u.CreateWindowExW.argtypes=[ctypes.wintypes.DWORD,ctypes.wintypes.LPCWSTR,ctypes.wintypes.LPCWSTR,ctypes.wintypes.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.wintypes.HWND,ctypes.c_void_p,ctypes.wintypes.HINSTANCE,ctypes.c_void_p];u.CreateWindowExW.restype=ctypes.wintypes.HWND
            i=k.GetModuleHandleW(None);name=f"AIClipboardListener-{id(self)}";self._wndproc=proc;self._wndclass=C(0,proc,0,0,i,None,None,None,None,name)
            if not u.RegisterClassW(ctypes.byref(self._wndclass)):return
            self.hwnd=u.CreateWindowExW(0,name,name,0,0,0,0,0,ctypes.c_void_p(-3),None,i,None)
            if self.hwnd and u.AddClipboardFormatListener(self.hwnd):
                msg=ctypes.wintypes.MSG()
                while u.GetMessageW(ctypes.byref(msg),None,0,0)>0:
                    u.TranslateMessage(ctypes.byref(msg));u.DispatchMessageW(ctypes.byref(msg))
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
    def stop(self):
        if self.hwnd:self.u.RemoveClipboardFormatListener(self.hwnd);self.u.PostMessageW(self.hwnd,self.WM_CLOSE,0,0)
        if self.thread:self.thread.join(3)

@dataclass(frozen=True)
class Request:
    generation: int
    text: str


class Controller:
    # Ein Clipboard-Event enthaelt keine Herkunft. Dieses kurze Zeitfenster
    # unterdrueckt ausschliesslich das erwartete Event unseres eigenen Writes.
    SELF_ECHO_SECONDS = 1.0

    def __init__(self, clip, ai, model):
        self.clip, self.ai, self.model = clip, ai, model
        self.lock = threading.RLock()
        self.event = threading.Event()
        self.stop_event = threading.Event()
        self.gen = 0
        self.pending = None
        self.own = None
        self.own_until = 0.0
        self.worker = threading.Thread(target=self.work, daemon=True)

    def changed(self):
        text = self.clip.read()
        if text is None or not text.strip() or len(text) > MAX_TEXT:
            return
        with self.lock:
            now = time.monotonic()
            if self.own is not None and now > self.own_until:
                self.own = None
            if text == self.own:
                self.own = None
                return
            self.gen += 1
            self.pending = Request(self.gen, text)
            self.event.set()

    def start(self):
        self.clip.start(self.changed)
        self.worker.start()

    def work(self):
        while not self.stop_event.is_set():
            self.event.wait()
            self.event.clear()
            with self.lock:
                r, self.pending = self.pending, None
            if not r:
                continue
            print("[VERARBEITE] Anfrage empfangen.")
            try:
                a = self.ai.ask(self.model, r.text)
            except Exception as e:
                print("[KI-FEHLER]", e)
                continue
            # Kein langsamer externer Clipboard-Aufruf innerhalb des Locks:
            # neue Events koennen die laufende Anfrage sofort ersetzen.
            clipboard_text = self.clip.read()
            with self.lock:
                if r.generation != self.gen:
                    print("[INFO] Neuere Kopie vorhanden; Ergebnis verworfen.")
                    continue
                if not a or clipboard_text != r.text:
                    print("[INFO] Zwischenablage geändert; Ergebnis verworfen.")
                    continue
                self.own = a
                self.own_until = time.monotonic() + self.SELF_ECHO_SECONDS
                try:
                    self.clip.write(a)
                except Exception as e:
                    self.own = None
                    self.own_until = 0.0
                    print("[CLIPBOARD-FEHLER]", e)
                    continue
            print("[FERTIG] Antwort liegt in der Zwischenablage; jetzt Strg+V.")

    def stop(self):
        self.stop_event.set()
        self.event.set()
        self.clip.stop()
        self.worker.join(3)

def main():
    print("KI-Clipboard – nur lokale Verarbeitung mit Ollama.")
    print("[DATENSCHUTZ] Kopierte Texte (auch Passwörter/Tokens) können verarbeitet werden.")
    ai = Ollama()
    ai.start()
    model = ai.model()
    clip = (
        Windows()
        if os.name == "nt"
        else Wayland()
        if os.getenv("WAYLAND_DISPLAY")
        else (_ for _ in ()).throw(RuntimeError("Windows oder Wayland erforderlich."))
    )
    c = Controller(clip, ai, model)
    c.start()
    print(f"[BEREIT] Modell: {model}. Text kopieren → warten → einfügen. Beenden: Strg+C")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Beende …")
    finally:
        c.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[STARTFEHLER]", e, file=sys.stderr)
        sys.exit(1)
