import atexit
import os
import random
import shutil
import signal
import subprocess
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

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
TEMP_DIR = Path(tempfile.gettempdir())
LOCK_FILE = TEMP_DIR / "AIBrowser_Clipboard_Assistant.pid"
KEY_FILE = TEMP_DIR / "AIBrowser_gemini_api.key"


def load_api_key():
    """Lädt den gespeicherten Key, falls vorhanden – sonst den Standard-Key aus dem Code."""
    try:
        if KEY_FILE.exists():
            saved_key = KEY_FILE.read_text(encoding="utf-8").strip()
            if saved_key:
                return saved_key
    except OSError:
        pass
    return DEFAULT_GEMINI_API_KEY


PROVIDER = {
    "name": "Gemini",
    "base_url": "https://generativelanguage.googleapis.com/v1beta",
    "key": load_api_key(),
    "preferred": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash", "gemini-2.5-pro", "gemini"],
    "models": [],
}

# Anweisung für extrem kurze Antworten
SYSTEM_PROMPT = (
    "Antworte extrem kurz, direkt und auf den Punkt. "
    "Keine Begrüßung, keine Einleitung, keine Erklärungen, kein Markdown – nur die reine, knappste Antwort."
)

EXCLUDE_KEYWORDS = (
    "embed", "embedding", "imagen", "veo", "tts", "audio", "aqa",
    "bisect", "vision", "robotics", "computer-use", "deep-research", "learnlm"
)

POLL_INTERVAL = 1
RETRY_STATUS = {500, 502, 503, 504}
MAX_RETRIES_PER_MODEL = 2

last_text = ""
state_lock = threading.Lock()


def _terminate_process(pid):
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


def _cleanup_lock():
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


def enforce_single_instance():
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
    except OSError:
        pass


def get_clipboard():
    try:
        if IS_WINDOWS:
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

        if shutil.which("wl-copy"):
            cmd = ["wl-copy"]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-i", "-selection", "clipboard"]
        elif shutil.which("xsel"):
            cmd = ["xsel", "--clipboard", "--input"]
        else:
            return

        subprocess.run(cmd, input=text.encode("utf-8"), check=False)
    except Exception:
        pass


def update_api_key(new_key):
    """Aktualisiert den API-Key zur Laufzeit, speichert ihn und lädt die Modelle neu."""
    global last_text
    clean_key = new_key.strip()
    if not clean_key:
        return

    try:
        KEY_FILE.write_text(clean_key, encoding="utf-8")
    except OSError:
        pass

    PROVIDER["key"] = clean_key
    PROVIDER["models"] = []
    fetch_available_models()

    confirm_msg = "API_KEY_UPDATED"
    with state_lock:
        last_text = confirm_msg
    set_clipboard(confirm_msg)


def reset_api_key():
    """Setzt den API-Key wieder auf den Standard-Wert im Code zurück."""
    global last_text
    try:
        if KEY_FILE.exists():
            KEY_FILE.unlink()
    except OSError:
        pass

    PROVIDER["key"] = DEFAULT_GEMINI_API_KEY
    PROVIDER["models"] = []
    if PROVIDER["key"] and not PROVIDER["key"].startswith("DEIN_"):
        fetch_available_models()

    confirm_msg = "API_KEY_RESET"
    with state_lock:
        last_text = confirm_msg
    set_clipboard(confirm_msg)


def fetch_available_models():
    url = f"{PROVIDER['base_url']}/models?key={PROVIDER['key']}"

    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
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

        def priority_score(mid):
            lower = mid.lower()
            for idx, kw in enumerate(PROVIDER["preferred"]):
                if kw in lower:
                    return idx
            return len(PROVIDER["preferred"])

        grouped = []
        for score in range(len(PROVIDER["preferred"]) + 1):
            group = [m for m in valid_ids if priority_score(m) == score]
            group.sort(reverse=True)
            grouped.extend(group)

        PROVIDER["models"] = grouped[:5]
        return PROVIDER["models"]

    except Exception:
        return []


def promote_model(model):
    if model in PROVIDER["models"]:
        PROVIDER["models"].remove(model)
        PROVIDER["models"].insert(0, model)


def demote_model(model):
    if model in PROVIDER["models"]:
        PROVIDER["models"].remove(model)
        PROVIDER["models"].append(model)


def call_ai(prompt):
    if not PROVIDER["key"] or PROVIDER["key"].startswith("DEIN_"):
        return None

    if not PROVIDER["models"]:
        fetch_available_models()

    headers = {"Content-Type": "application/json"}

    for model in list(PROVIDER["models"]):
        chat_url = f"{PROVIDER['base_url']}/models/{model}:generateContent?key={PROVIDER['key']}"
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
            "generationConfig": {
                "maxOutputTokens": 150,
                "temperature": 0.2,
            },
        }

        for attempt in range(MAX_RETRIES_PER_MODEL + 1):
            wait = 1.5 ** attempt + random.uniform(0, 0.5)
            try:
                r = requests.post(
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

                if r.status_code == 404:
                    if model in PROVIDER["models"]:
                        PROVIDER["models"].remove(model)
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

            except Exception:
                break

    return None


def handle_clipboard_text(text):
    global last_text
    answer = call_ai(text)
    if answer:
        clean_answer = answer.strip()
        with state_lock:
            last_text = clean_answer
        set_clipboard(clean_answer)


def main():
    global last_text
    enforce_single_instance()

    if PROVIDER["key"] and not PROVIDER["key"].startswith("DEIN_"):
        fetch_available_models()

    with state_lock:
        last_text = get_clipboard().strip()

    try:
        while True:
            text = get_clipboard().strip()
            with state_lock:
                is_new = bool(text) and text != last_text
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


if __name__ == "__main__":
    main()