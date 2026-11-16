import os
import logging
import ctypes
import ctypes.wintypes
import queue
import random
import shutil
import subprocess
import threading
import time
import requests
import tkinter as tk
from tkinter import ttk, messagebox
import keyring
from tray_app import TrayApp
from single_instance import SingleInstance

IS_WINDOWS = os.name == "nt"
LOG_DIR=os.path.join(os.getenv("LOCALAPPDATA",os.path.expanduser("~")),"AIClipboardCloud")
os.makedirs(LOG_DIR,exist_ok=True)
logging.basicConfig(filename=os.path.join(LOG_DIR,"app.log"),level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


def load_env(filepath=None):
    if filepath is None:
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(filepath):
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")


load_env()

# 1. API-Keys sicher aus der .env-Datei laden
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
KEYRING_SERVICE="AIClipboardCloud"; configured_provider=None

def load_settings():
    global GROQ_API_KEY,GEMINI_API_KEY,configured_provider
    configured_provider=keyring.get_password(KEYRING_SERVICE,"provider")
    if configured_provider=="Groq":GROQ_API_KEY=keyring.get_password(KEYRING_SERVICE,"Groq") or ""
    if configured_provider=="Google AI Studio":GEMINI_API_KEY=keyring.get_password(KEYRING_SERVICE,"Google AI Studio") or ""
    return bool(configured_provider and (GROQ_API_KEY or GEMINI_API_KEY))

def open_settings():
    root=tk.Tk();root.title("AI Clipboard – Cloud-Einstellungen");root.resizable(False,False)
    provider=tk.StringVar(value=configured_provider or "Groq");key=tk.StringVar()
    ttk.Label(root,text="Kopierte Texte werden an den gewählten Cloud-Anbieter übertragen.",wraplength=420).pack(padx=18,pady=(18,6))
    consent=tk.BooleanVar(value=False);ttk.Checkbutton(root,text="Ich stimme der Cloud-Verarbeitung zu.",variable=consent).pack(padx=18,anchor="w")
    ttk.Combobox(root,textvariable=provider,values=("Groq","Google AI Studio"),state="readonly").pack(padx=18,pady=8,fill="x")
    ttk.Entry(root,textvariable=key,show="●",width=48).pack(padx=18,pady=4)
    def save():
        global configured_provider,GROQ_API_KEY,GEMINI_API_KEY
        if not consent.get():messagebox.showerror("Zustimmung fehlt","Bitte stimme der Cloud-Verarbeitung zu.");return
        if not key.get().strip():messagebox.showerror("API-Key fehlt","Bitte füge einen API-Key ein.");return
        configured_provider=provider.get();keyring.set_password(KEYRING_SERVICE,"provider",configured_provider);keyring.set_password(KEYRING_SERVICE,configured_provider,key.get().strip())
        GROQ_API_KEY=key.get().strip() if configured_provider=="Groq" else "";GEMINI_API_KEY=key.get().strip() if configured_provider=="Google AI Studio" else "";root.destroy()
    ttk.Button(root,text="Speichern",command=save).pack(pady=(8,18));root.mainloop()

# 2. Anbieter-Konfiguration (Groq zuerst, weil es extrem schnell antwortet)
PROVIDERS = [
    {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "key": GROQ_API_KEY,
        "preferred": ["llama-3.3", "gpt-oss", "qwen", "llama"],
        "models": [],
    },
    {
        "name": "Google AI Studio",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key": GEMINI_API_KEY,
        "preferred": ["flash-lite", "flash", "gemma"],
        "models": [],
    },
]

# Modelle herausfiltern, die keine Text-Chats sind oder in der Free-Tier Quota=0 haben
EXCLUDE_KEYWORDS = (
    "whisper", "embed", "embedding", "imagen", "veo", "tts", "orpheus",
    "audio", "guard", "safeguard", "moderation", "aqa", "bisect",
    "vision-preview", "omni", "robotics", "computer-use", "deep-research"
)

POLL_INTERVAL = 1
RETRY_STATUS = {500, 502, 503, 504}
MAX_RETRIES_PER_MODEL = 2

last_text = ""
state_lock = threading.Lock()
WIN_CLIP = None
request_queue=queue.Queue(maxsize=1);generation=0;own_text=None
shutdown=threading.Event();tray=None
instance_lock=None

# Ausschließlich diese Modelle werden automatisch angesprochen. Die Liste ist
# bewusst fest verdrahtet, damit eine Modell-Liste des Anbieters nie unbemerkt
# auf ein kostenpflichtiges Modell umschaltet.
FREE_MODELS = {"Groq": ["llama-3.1-8b-instant"], "Google AI Studio": ["gemini-2.5-flash-lite"]}

class WindowsClipboard:
    CF_UNICODETEXT=13;GMEM_MOVEABLE=2;WM_CLIPBOARDUPDATE=0x031D;WM_CLOSE=0x10;WM_DESTROY=2
    def __init__(self):
        self.u,self.k=ctypes.windll.user32,ctypes.windll.kernel32;self.hwnd=None;self.thread=None;self.ready=threading.Event()
        H=ctypes.c_void_p;U=ctypes.wintypes.UINT;B=ctypes.wintypes.BOOL
        for f,args,res in (("OpenClipboard",[H],B),("CloseClipboard",[],B),("EmptyClipboard",[],B),("GetClipboardData",[U],H),("SetClipboardData",[U,H],H),("AddClipboardFormatListener",[H],B),("RemoveClipboardFormatListener",[H],B)):
            x=getattr(self.u,f);x.argtypes=args;x.restype=res
        for f,args,res in (("GlobalAlloc",[U,ctypes.c_size_t],H),("GlobalLock",[H],H),("GlobalUnlock",[H],B),("GlobalFree",[H],H)):
            x=getattr(self.k,f);x.argtypes=args;x.restype=res
    def read(self):
        if not self.u.OpenClipboard(None):return ""
        try:
            h=self.u.GetClipboardData(self.CF_UNICODETEXT);p=self.k.GlobalLock(h) if h else None
            try:return ctypes.wstring_at(p) if p else ""
            finally:
                if p:self.k.GlobalUnlock(h)
        finally:self.u.CloseClipboard()
    def write(self,text):
        if not self.u.OpenClipboard(None):return False
        h=None
        try:
            self.u.EmptyClipboard();b=ctypes.create_unicode_buffer(text);h=self.k.GlobalAlloc(self.GMEM_MOVEABLE,ctypes.sizeof(b));p=self.k.GlobalLock(h);ctypes.memmove(p,b,ctypes.sizeof(b));self.k.GlobalUnlock(h)
            if self.u.SetClipboardData(self.CF_UNICODETEXT,h):h=None;return True
            return False
        finally:
            if h:self.k.GlobalFree(h)
            self.u.CloseClipboard()
    def start(self,callback):
        def run():
            P=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,ctypes.c_void_p,ctypes.wintypes.UINT,ctypes.c_size_t,ctypes.c_ssize_t)
            @P
            def proc(h,m,w,l):
                if m==self.WM_CLIPBOARDUPDATE:callback()
                if m==self.WM_DESTROY:self.u.PostQuitMessage(0)
                return self.u.DefWindowProcW(h,m,w,l)
            self._proc=proc;name="AIClipboardCloud";self.u.CreateWindowExW.restype=ctypes.c_void_p;self.u.RegisterClassW.restype=ctypes.wintypes.ATOM
            class WC(ctypes.Structure):_fields_=[("style",ctypes.c_uint),("lpfnWndProc",P),("cbClsExtra",ctypes.c_int),("cbWndExtra",ctypes.c_int),("hInstance",ctypes.c_void_p),("hIcon",ctypes.c_void_p),("hCursor",ctypes.c_void_p),("hbrBackground",ctypes.c_void_p),("lpszMenuName",ctypes.c_wchar_p),("lpszClassName",ctypes.c_wchar_p)]
            self._wc=WC(0,proc,0,0,self.k.GetModuleHandleW(None),None,None,None,None,name);self.u.RegisterClassW(ctypes.byref(self._wc));self.hwnd=self.u.CreateWindowExW(0,name,name,0,0,0,0,0,ctypes.c_void_p(-3),None,self._wc.hInstance,None)
            if not self.hwnd or not self.u.AddClipboardFormatListener(self.hwnd):self.ready.set();return
            self.ready.set();msg=ctypes.wintypes.MSG()
            while self.u.GetMessageW(ctypes.byref(msg),None,0,0)>0:self.u.DispatchMessageW(ctypes.byref(msg))
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start();self.ready.wait(2)
    def stop(self):
        if self.hwnd:self.u.RemoveClipboardFormatListener(self.hwnd);self.u.PostMessageW(self.hwnd,self.WM_CLOSE,0,0)
        if self.thread:self.thread.join(2)


def get_clipboard():
    try:
        if IS_WINDOWS:
            if WIN_CLIP:
                return WIN_CLIP.read()
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-Clipboard -Raw",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            return r.stdout if r.returncode == 0 and r.stdout else ""

        # Linux: Wayland (wl-paste) oder X11 (xclip / xsel)
        if shutil.which("wl-paste"):
            cmd = ["wl-paste", "--no-newline"]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-o", "-selection", "clipboard"]
        elif shutil.which("xsel"):
            cmd = ["xsel", "--clipboard", "--output"]
        else:
            return ""

        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        return r.stdout if r.returncode == 0 and r.stdout else ""
    except Exception:
        return ""


def set_clipboard(text):
    try:
        if IS_WINDOWS:
            if WIN_CLIP:
                WIN_CLIP.write(text)
                return
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; $input | Set-Clipboard",
                ],
                input=text,
                text=True,
                encoding="utf-8",
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
            return

        # Linux: Wayland (wl-copy) oder X11 (xclip / xsel)
        if shutil.which("wl-copy"):
            cmd = ["wl-copy"]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-i", "-selection", "clipboard"]
        elif shutil.which("xsel"):
            cmd = ["xsel", "--clipboard", "--input"]
        else:
            print("Kein Clipboard-Tool gefunden (wl-copy, xclip oder xsel).")
            return

        subprocess.run(cmd, input=text.encode("utf-8"), check=False)
    except Exception as e:
        print(f"Fehler beim Schreiben in die Zwischenablage: {e}")


def notify(message):
    print(message)
    logging.info("Benachrichtigung: %s",message)
    try:
        if IS_WINDOWS:
            if tray: tray.icon.notify(message, "KI-Assistent")
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "KI-Assistent", message], check=False)
    except Exception:
        pass


def fetch_available_models(provider):
    headers = {"Authorization": f"Bearer {provider['key']}"}
    url = f"{provider['base_url']}/models"

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"Konnte Modelle für {provider['name']} nicht abrufen (HTTP {r.status_code}).")
            return []

        raw_models = r.json().get("data", [])
        valid_ids = []

        for item in raw_models:
            model_id = item.get("id", "").replace("models/", "")
            lower_id = model_id.lower()
            if not model_id or any(bad in lower_id for bad in EXCLUDE_KEYWORDS):
                continue
            valid_ids.append(model_id)

        def priority_score(mid):
            lower = mid.lower()
            for idx, kw in enumerate(provider["preferred"]):
                if kw in lower:
                    return idx
            return len(provider["preferred"])

        grouped = []
        for score in range(len(provider["preferred"]) + 1):
            group = [m for m in valid_ids if priority_score(m) == score]
            group.sort(reverse=True)
            grouped.extend(group)

        # /models beweist weder Free-Tier noch Kostenfreiheit. Nur die explizit
        # freigegebenen Modelle dürfen automatisch verwendet werden.
        provider["models"] = [m for m in FREE_MODELS.get(provider["name"], []) if m in valid_ids]
        print(f"Gefundene Modelle für {provider['name']}: {provider['models']}")
        return provider["models"]

    except Exception as e:
        print(f"Fehler beim Modell-Abruf für {provider['name']}: {e}")
        return []


def init_providers():
    for provider in PROVIDERS:
        if provider["key"] and not provider["key"].startswith("DEIN_"):
            fetch_available_models(provider)


def promote_model(provider, model):
    if model in provider["models"]:
        provider["models"].remove(model)
        provider["models"].insert(0, model)


def demote_model(provider, model):
    if model in provider["models"]:
        provider["models"].remove(model)
        provider["models"].append(model)


def call_ai(prompt):
    for provider in PROVIDERS:
        if not provider["key"] or provider["key"].startswith("DEIN_"):
            continue

        if not provider["models"]:
            fetch_available_models(provider)

        headers = {
            "Authorization": f"Bearer {provider['key']}",
            "Content-Type": "application/json",
        }
        chat_url = f"{provider['base_url']}/chat/completions"

        for model in list(provider["models"]):
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            }

            for attempt in range(MAX_RETRIES_PER_MODEL + 1):
                wait = 1.5 ** attempt + random.uniform(0, 0.5)
                try:
                    print(f"Versuche {provider['name']} -> {model} (Versuch {attempt + 1})...")
                    r = requests.post(
                        chat_url,
                        headers=headers,
                        json=payload,
                        timeout=(10, 60),
                    )

                    if r.status_code == 200:
                        data = r.json()
                        promote_model(provider, model)
                        print(f"Erfolg über {provider['name']} ({model})")
                        return data["choices"][0]["message"]["content"]

                    if r.status_code == 404:
                        print(f"Modell {model} veraltet (404). Entferne es...")
                        if model in provider["models"]:
                            provider["models"].remove(model)
                        break

                    if r.status_code == 429:
                        retry_after=float(r.headers.get("Retry-After", "60"))
                        print(f"Kostenloses Kontingent erreicht; Pause für {retry_after:.0f} Sekunden.")
                        time.sleep(min(retry_after, 60))
                        return None

                    print(f"HTTP-Status {r.status_code} bei {model}: {r.text[:120]}")

                    if r.status_code in RETRY_STATUS and attempt < MAX_RETRIES_PER_MODEL:
                        time.sleep(wait)
                        continue
                    break

                except (requests.Timeout, requests.ConnectionError) as e:
                    print(f"Netzwerkfehler bei {model}: {type(e).__name__}")
                    if attempt < MAX_RETRIES_PER_MODEL:
                        time.sleep(wait)
                        continue
                    break

                except Exception as e:
                    print(f"Unerwarteter Fehler: {type(e).__name__}: {e}")
                    break

    return None


def enqueue(text):
    global generation
    with state_lock:
        generation += 1; ticket = generation
        while not request_queue.empty():
            try: request_queue.get_nowait()
            except queue.Empty: break
        request_queue.put_nowait((ticket, text))

def worker():
    global last_text, own_text
    while True:
        ticket, text = request_queue.get()
        answer = call_ai(text)
        current = get_clipboard().strip()
        with state_lock:
            if ticket != generation or current != text:
                continue
            if not answer: continue
            own_text = answer.strip(); last_text = own_text
        set_clipboard(own_text)
        notify("Antwort ist in der Zwischenablage.")


def main():
    global last_text,WIN_CLIP,tray,instance_lock
    if IS_WINDOWS:
        instance_lock=SingleInstance()
        if instance_lock.already_running:return
    if not load_settings():
        open_settings()
        if not load_settings():
            print("Keine gültige Cloud-Konfiguration; Clipboard-Überwachung wird nicht gestartet.")
            return
    init_providers()
    threading.Thread(target=worker, daemon=True).start()
    if IS_WINDOWS:
        tray=TrayApp(open_settings, lambda: shutdown.set());tray.start()

    if IS_WINDOWS:
        WIN_CLIP=WindowsClipboard()
        def changed():
            global own_text
            if not tray.active:return
            text=WIN_CLIP.read().strip()
            with state_lock:
                if not text:return
                if text==own_text:
                    own_text=None;return
            enqueue(text)
        WIN_CLIP.start(changed)

    with state_lock:
        last_text = get_clipboard().strip()
    print(f"Clipboard-Überwachung gestartet ({'Windows' if IS_WINDOWS else 'Linux'}). Mit Strg+C beenden.")

    try:
        while not shutdown.is_set():
            if IS_WINDOWS:
                time.sleep(.2)
                continue
            text = get_clipboard().strip()
            with state_lock:
                is_new = bool(text) and text != last_text
                if is_new:
                    last_text = text

            if is_new:
                print(f"Neuer Text erkannt: {text[:80]}...")
                enqueue(text)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        if WIN_CLIP:WIN_CLIP.stop()
        if tray:tray.stop()
        if instance_lock:instance_lock.close()


if __name__ == "__main__":
    main()
