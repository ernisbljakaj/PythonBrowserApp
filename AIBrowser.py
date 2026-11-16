#!/usr/bin/env python3
"""Automatische, lokale Ollama-Zwischenablage fuer Wayland und Windows."""
from __future__ import annotations
import ctypes, ctypes.wintypes, ipaddress, json, os, re, shutil, subprocess, sys, threading, time
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
    def __init__(self):self.done=threading.Event(); self.process=None; self.thread=None
    def read(self):
        try:
            p=subprocess.run(["wl-paste","--no-newline","--type","text/plain"],capture_output=True,text=True,timeout=3)
            return p.stdout if p.returncode==0 else None
        except (OSError,subprocess.TimeoutExpired):return None
    def write(self,text):subprocess.run(["wl-copy","--type","text/plain;charset=utf-8"],input=text,text=True,check=True,timeout=5)
    def start(self,callback):
        if not shutil.which("wl-paste") or not shutil.which("wl-copy"):raise RuntimeError("wl-clipboard fehlt: sudo dnf install wl-clipboard")
        def run():
            backoff=1
            while not self.done.is_set():
                try:
                    # wl-paste startet printf pro Ereignis; dessen Exit flusht die Zeile sicher.
                    self.process=subprocess.Popen(["wl-paste","--watch","--type","text/plain","printf","changed\\n"],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
                    for line in self.process.stdout:
                        if self.done.is_set():break
                        if line=="changed\n":callback()
                    if not self.done.is_set():print("[WATCHER] beendet; Neustart …")
                    backoff=min(backoff*2,15)
                except OSError as e: print("[WATCHER-FEHLER]",e)
                finally:
                    p,self.process=self.process,None
                    if p and p.poll() is None:p.terminate()
                self.done.wait(backoff)
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
    def stop(self):
        self.done.set()
        if self.process and self.process.poll() is None:self.process.terminate()
        if self.thread:self.thread.join(3)

class Windows(Clipboard):
    """Ereignisgesteuerter Win32-Backend; keine Polling-Schleife."""
    def __init__(self):self.hwnd=None;self.thread=None
    def read(self):
        u,k=ctypes.windll.user32,ctypes.windll.kernel32
        for _ in range(5):
            if u.OpenClipboard(None):
                try:
                    h=u.GetClipboardData(13); p=k.GlobalLock(h) if h else None
                    try:return ctypes.wstring_at(p) if p else None
                    finally:
                        if p:k.GlobalUnlock(h)
                finally:u.CloseClipboard()
            time.sleep(.05)
    def write(self,text):
        u,k=ctypes.windll.user32,ctypes.windll.kernel32
        if not u.OpenClipboard(None):raise RuntimeError("Windows-Zwischenablage ist belegt.")
        try:
            u.EmptyClipboard(); b=ctypes.create_unicode_buffer(text);h=k.GlobalAlloc(2,ctypes.sizeof(b));p=k.GlobalLock(h);ctypes.memmove(p,b,ctypes.sizeof(b));k.GlobalUnlock(h)
            if not u.SetClipboardData(13,h):k.GlobalFree(h);raise RuntimeError("SetClipboardData fehlgeschlagen.")
        finally:u.CloseClipboard()
    def start(self,callback):
        def run():
            u,k=ctypes.windll.user32,ctypes.windll.kernel32; P=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t)
            @P
            def proc(h,m,w,l):
                if m==0x031D:callback()
                if m==2:u.PostQuitMessage(0)
                return u.DefWindowProcW(h,m,w,l)
            class C(ctypes.Structure):_fields_=[("style",ctypes.c_uint),("proc",P),("a",ctypes.c_int),("b",ctypes.c_int),("i",ctypes.c_void_p),("x",ctypes.c_void_p),("y",ctypes.c_void_p),("z",ctypes.c_void_p),("menu",ctypes.c_wchar_p),("name",ctypes.c_wchar_p)]
            i=k.GetModuleHandleW(None); name="AIClipboardListener";u.RegisterClassW(ctypes.byref(C(0,proc,0,0,i,None,None,None,None,name)));self.hwnd=u.CreateWindowExW(0,name,name,0,0,0,0,0,-3,None,i,None)
            if self.hwnd and u.AddClipboardFormatListener(self.hwnd):
                msg=ctypes.wintypes.MSG()
                while u.GetMessageW(ctypes.byref(msg),None,0,0)>0:u.DispatchMessageW(ctypes.byref(msg))
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
    def stop(self):
        if self.hwnd:ctypes.windll.user32.PostMessageW(self.hwnd,2,0,0)
        if self.thread:self.thread.join(3)

@dataclass(frozen=True)
class Request: generation:int;text:str
class Controller:
    def __init__(self,clip,ai,model):self.clip,self.ai,self.model=clip,ai,model;self.lock=threading.RLock();self.event=threading.Event();self.stop_event=threading.Event();self.gen=0;self.pending=None;self.own=None;self.worker=threading.Thread(target=self.work,daemon=True)
    def changed(self):
        text=self.clip.read()
        if text is None or not text.strip() or len(text)>MAX_TEXT:return
        with self.lock:
            if text==self.own:self.own=None;return
            self.gen+=1;self.pending=Request(self.gen,text);self.event.set()
    def start(self):self.clip.start(self.changed);self.worker.start()
    def work(self):
        while not self.stop_event.is_set():
            self.event.wait(.5);self.event.clear()
            with self.lock:r,self.pending=self.pending,None
            if not r:continue
            print("[VERARBEITE] Anfrage empfangen.")
            try:a=self.ai.ask(self.model,r.text)
            except Exception as e:print("[KI-FEHLER]",e);continue
            with self.lock:
                if r.generation!=self.gen:print("[INFO] Neuere Kopie vorhanden; Ergebnis verworfen.");continue
                if not a or self.clip.read()!=r.text:print("[INFO] Zwischenablage geändert; Ergebnis verworfen.");continue
                self.own=a
                try:self.clip.write(a)
                except Exception as e:self.own=None;print("[CLIPBOARD-FEHLER]",e);continue
            print("[FERTIG] Antwort liegt in der Zwischenablage; jetzt Strg+V.")
    def stop(self):self.stop_event.set();self.event.set();self.clip.stop();self.worker.join(3)

def main():
    print("KI-Clipboard – nur lokale Verarbeitung mit Ollama.")
    print("[DATENSCHUTZ] Kopierte Texte (auch Passwörter/Tokens) können verarbeitet werden.")
    ai=Ollama();ai.start();model=ai.model();clip=Windows() if os.name=="nt" else Wayland() if os.getenv("WAYLAND_DISPLAY") else (_ for _ in ()).throw(RuntimeError("Windows oder Wayland erforderlich."))
    c=Controller(clip,ai,model);c.start();print(f"[BEREIT] Modell: {model}. Text kopieren → warten → einfügen. Beenden: Strg+C")
    try:
        while True:time.sleep(1)
    except KeyboardInterrupt:print("\n[INFO] Beende …")
    finally:c.stop()
if __name__=="__main__":
    try:main()
    except Exception as e:print("[STARTFEHLER]",e,file=sys.stderr);sys.exit(1)
