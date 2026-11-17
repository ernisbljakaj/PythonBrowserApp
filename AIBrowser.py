import atexit
import ctypes
import logging
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import requests

# Standard-Gemini-API-Key im Code (Fallback, falls kein eigener Key gesetzt wurde)
DEFAULT_GEMINI_API_KEY = "GEMINI-KEY"

# Geheime Befehle für die Zwischenablage
SECRET_SET_PREFIX = "SET_API:"
SECRET_RESET_CMD = "RESET_API"

# Sicherheits- und Performance-Limits
POLL_INTERVAL = 0.5
MAX_TEXT_LENGTH = 4000  # Verhindert unbeabsichtigte API-Calls bei langen Dokumenten
RETRY_STATUS = {500, 502, 503, 504}
MAX_RETRIES_PER_MODEL = 2

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0

TEMP_DIR = Path(tempfile.gettempdir())
LOCK_FILE = TEMP_DIR / "AIBrowser_Clipboard_Assistant.pid"
LOG_FILE = TEMP_DIR / "AIBrowser_Clipboard_Assistant.log"
KEY_FILE = Path.home() / ".aibrowser_gemini.key"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

SYSTEM_PROMPT = (
    "Antworte extrem kurz, direkt und auf den Punkt. "
    "Keine Begrüßung, keine Einleitung, keine Erklärungen, kein Markdown – nur die reine, knappste Antwort."
)

EXCLUDE_KEYWORDS = (
    "embed", "embedding", "imagen", "veo", "tts", "audio", "aqa",
    "bisect", "vision", "robotics", "computer-use", "deep-research", "learnlm"
)

state_lock = threading.Lock()
last_text = ""
http_session = requests.Session()


def is_valid_key(key: str) -> bool:
    """Prüft, ob ein echter API-Key (kein Platzhalter) gesetzt ist."""
    return bool(key) and key != DEFAULT_GEMINI_API_KEY and not key.startswith("DEIN_")


def load_api_key() -> str:
    """Lädt den gespeicherten Key, falls vorhanden – sonst den Standard-Key aus dem Code."""
    try:
        if KEY_FILE.exists():
            saved_key = KEY_FILE.read_text(encoding="utf-8").strip()
            if saved_key:
                return saved_key
    except OSError as e:
        logging.warning("Konnte Key-Datei nicht lesen: %s", e)
    return DEFAULT_GEMINI_API_KEY


PROVIDER = {
    "name": "Gemini",
    "base_url": "https://generativelanguage.googleapis.com/v1beta",
    "key": load_api_key(),
    "preferred": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash", "gemini-2.5-pro", "gemini"],
    "models": [],
}


# --- Single-Instance-Verwaltung ---

def _terminate_process(pid: int) -> None:
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _cleanup_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


def enforce_single_instance() -> None:
    current_pid = os.getpid()
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            if old_pid != current_pid:
                _terminate_process(old_pid)
                time.sleep(0.3)
        except (ValueError, OSError):
            pass

    try:
        LOCK_FILE.write_text(str(current_pid), encoding="utf-8")
        atexit.register(_cleanup_lock)
    except OSError as e:
        logging.warning("Konnte Lock-Datei nicht schreiben: %s", e)


# --- Natives Clipboard-Handling ohne externe Pakete ---

if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _CF_UNICODETEXT = 13
    _GMEM_MOVEABLE = 0x0002

    _user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    _user32.OpenClipboard.restype = ctypes.c_bool
    _user32.CloseClipboard.restype = ctypes.c_bool
    _user32.EmptyClipboard.restype = ctypes.c_bool
    _user32.GetClipboardData.argtypes = [ctypes.c_uint]
    _user32.GetClipboardData.restype = ctypes.c_void_p
    _user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    _user32.SetClipboardData.restype = ctypes.c_void_p

    _kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    _kernel32.GlobalAlloc.restype = ctypes.c_void_p
    _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalUnlock.restype = ctypes.c_bool
    _kernel32.GlobalFree.argtypes = [ctypes.c_void_p]


def _win_get_clipboard() -> str:
    if not _user32.OpenClipboard(None):
        return ""
    try:
        handle = _user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def _win_set_clipboard(text: str) -> None:
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not _user32.OpenClipboard(None):
        return
    try:
        _user32.EmptyClipboard()
        handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        if not handle:
            return
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            _kernel32.GlobalFree(handle)
            return
        ctypes.memmove(ptr, data, len(data))
        _kernel32.GlobalUnlock(handle)
        if not _user32.SetClipboardData(_CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
    finally:
        _user32.CloseClipboard()


def get_clipboard() -> str:
    try:
        if IS_WINDOWS:
            return _win_get_clipboard()

        if IS_MAC and shutil.which("pbpaste"):
            cmd = ["pbpaste"]
        elif shutil.which("wl-paste"):
            cmd = ["wl-paste", "--no-newline"]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-o", "-selection", "clipboard"]
        elif shutil.which("xsel"):
            cmd = ["xsel", "--clipboard", "--output"]
        else:
            return ""

        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=3)
        return r.stdout if r.returncode == 0 and r.stdout else ""
    except Exception:
        return ""


def set_clipboard(text: str) -> None:
    try:
        if IS_WINDOWS:
            _win_set_clipboard(text)
            return

        if IS_MAC and shutil.which("pbcopy"):
            cmd = ["pbcopy"]
        elif shutil.which("wl-copy"):
            cmd = ["wl-copy"]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-i", "-selection", "clipboard"]
        elif shutil.which("xsel"):
            cmd = ["xsel", "--clipboard", "--input"]
        else:
            return

        subprocess.run(cmd, input=text.encode("utf-8"), check=False, timeout=3)
    except Exception as e:
        logging.warning("Fehler beim Schreiben in die Zwischenablage: %s", e)


# --- API-Key & Modell-Verwaltung ---

def _write_clipboard_status(msg: str) -> None:
    global last_text
    with state_lock:
        last_text = msg
    set_clipboard(msg)


def update_api_key(new_key: str) -> None:
    """Aktualisiert den API-Key zur Laufzeit, speichert ihn sicher und lädt die Modelle neu."""
    clean_key = new_key.strip()
    if not clean_key:
        return

    try:
        KEY_FILE.write_text(clean_key, encoding="utf-8")
        if not IS_WINDOWS:
            os.chmod(KEY_FILE, 0o600)
    except OSError as e:
        logging.warning("Konnte API-Key nicht speichern: %s", e)

    with state_lock:
        PROVIDER["key"] = clean_key
        PROVIDER["models"] = []

    models = fetch_available_models()
    if models:
        _write_clipboard_status("API_KEY_UPDATED")
    else:
        _write_clipboard_status("API_KEY_INVALID_OR_OFFLINE")


def reset_api_key() -> None:
    """Setzt den API-Key wieder auf den Standard-Wert im Code zurück."""
    try:
        if KEY_FILE.exists():
            KEY_FILE.unlink()
    except OSError:
        pass

    with state_lock:
        PROVIDER["key"] = DEFAULT_GEMINI_API_KEY
        PROVIDER["models"] = []

    if is_valid_key(DEFAULT_GEMINI_API_KEY):
        fetch_available_models()

    _write_clipboard_status("API_KEY_RESET")


def fetch_available_models() -> list[str]:
    with state_lock:
        api_key = PROVIDER["key"]
        base_url = PROVIDER["base_url"]
        preferred = list(PROVIDER["preferred"])

    if not is_valid_key(api_key):
        return []

    url = f"{base_url}/models"
    headers = {"x-goog-api-key": api_key}

    try:
        r = http_session.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            logging.warning("Modellsammlung fehlgeschlagen (HTTP %s)", r.status_code)
            return []

        raw_models = r.json().get("models", [])
        valid_ids = []

        for item in raw_models:
            methods = item.get("supportedGenerationMethods", [])
            if "generateContent" not in methods:
                continue

            model_id = item.get("name", "").replace("models/", "")
            lower_id = model_id.lower()
            if not model_id or any(bad in lower_id for bad in EXCLUDE_KEYWORDS):
                continue
            valid_ids.append(model_id)

        def priority_score(mid: str) -> int:
            lower = mid.lower()
            for idx, kw in enumerate(preferred):
                if kw in lower:
                    return idx
            return len(preferred)

        grouped = []
        for score in range(len(preferred) + 1):
            group = [m for m in valid_ids if priority_score(m) == score]
            group.sort(reverse=True)
            grouped.extend(group)

        top_models = grouped[:5]
        with state_lock:
            PROVIDER["models"] = top_models
        return top_models

    except Exception as e:
        logging.warning("Netzwerkfehler beim Laden der Modelle: %s", e)
        return []


def promote_model(model: str) -> None:
    with state_lock:
        if model in PROVIDER["models"]:
            PROVIDER["models"].remove(model)
            PROVIDER["models"].insert(0, model)


def demote_model(model: str) -> None:
    with state_lock:
        if model in PROVIDER["models"]:
            PROVIDER["models"].remove(model)
            PROVIDER["models"].append(model)


def remove_model(model: str) -> None:
    with state_lock:
        if model in PROVIDER["models"]:
            PROVIDER["models"].remove(model)


# --- Gemini-Anfrage ---

def call_ai(prompt: str) -> str | None:
    with state_lock:
        api_key = PROVIDER["key"]
        base_url = PROVIDER["base_url"]
        models_snapshot = list(PROVIDER["models"])

    if not is_valid_key(api_key):
        return None

    if not models_snapshot:
        models_snapshot = fetch_available_models()

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    for model in models_snapshot:
        chat_url = f"{base_url}/models/{model}:generateContent"

        gen_config = {
            "maxOutputTokens": 250,
            "temperature": 0.2,
        }
        # Thinking bei 2.5-Flash deaktivieren, damit keine leeren Antworten entstehen
        if "2.5-flash" in model.lower():
            gen_config["thinkingConfig"] = {"thinkingBudget": 0}

        payload = {
            "systemInstruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": gen_config,
        }

        for attempt in range(MAX_RETRIES_PER_MODEL + 1):
            wait = 1.5 ** attempt + random.uniform(0, 0.5)
            try:
                r = http_session.post(
                    chat_url,
                    headers=headers,
                    json=payload,
                    timeout=(10, 30),
                )

                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts and "text" in parts[0]:
                            promote_model(model)
                            return parts[0]["text"]
                    break

                if r.status_code in (400, 403):
                    logging.error("Ungültiger API-Key oder Anfrage (HTTP %s)", r.status_code)
                    return "ERROR: INVALID_API_KEY"

                if r.status_code == 404:
                    remove_model(model)
                    break

                if r.status_code == 429:
                    demote_model(model)
                    break

                if r.status_code in RETRY_STATUS and attempt < MAX_RETRIES_PER_MODEL:
                    time.sleep(wait)
                    continue
                break

            except (requests.Timeout, requests.ConnectionError):
                if attempt < MAX_RETRIES_PER_MODEL:
                    time.sleep(wait)
                    continue
                break

            except Exception as e:
                logging.warning("Unerwarteter Fehler bei %s: %s", model, e)
                break

    return None


def handle_clipboard_text(text: str) -> None:
    answer = call_ai(text)
    if answer:
        _write_clipboard_status(answer.strip())


# --- Hauptschleife ---

def main() -> None:
    global last_text
    enforce_single_instance()

    if is_valid_key(PROVIDER["key"]):
        fetch_available_models()

    with state_lock:
        last_text = get_clipboard().strip()

    try:
        while True:
            text = get_clipboard().strip()
            with state_lock:
                is_new = bool(text) and text != last_text and len(text) <= MAX_TEXT_LENGTH
                if is_new:
                    last_text = text

            if is_new:
                if text.startswith(SECRET_SET_PREFIX):
                    new_key = text[len(SECRET_SET_PREFIX):].strip()
                    threading.Thread(
                        target=update_api_key,
                        args=(new_key,),
                        daemon=True,
                    ).start()
                elif text == SECRET_RESET_CMD:
                    threading.Thread(
                        target=reset_api_key,
                        daemon=True,
                    ).start()
                else:
                    threading.Thread(
                        target=handle_clipboard_text,
                        args=(text,),
                        daemon=True,
                    ).start()

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        http_session.close()


if __name__ == "__main__":
    main()