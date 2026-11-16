import os
import random
import shutil
import subprocess
import threading
import time
import requests

# Nur noch dein Groq API-Key nötig
GROQ_API_KEY = "DEIN_GROQ_API_KEY_HIER"

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0

PROVIDER = {
    "name": "Groq",
    "base_url": "https://api.groq.com/openai/v1",
    "key": GROQ_API_KEY,
    "preferred": ["llama-3.3", "gpt-oss", "qwen", "llama"],
    "models": [],
}

# Anweisung für extrem kurze Antworten
SYSTEM_PROMPT = (
    "Antworte extrem kurz, direkt und auf den Punkt. "
    "Keine Begrüßung, keine Einleitung, keine Erklärungen, kein Markdown – nur die reine, knappste Antwort."
)

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


def fetch_available_models():
    headers = {"Authorization": f"Bearer {PROVIDER['key']}"}
    url = f"{PROVIDER['base_url']}/models"

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
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

    headers = {
        "Authorization": f"Bearer {PROVIDER['key']}",
        "Content-Type": "application/json",
    }
    chat_url = f"{PROVIDER['base_url']}/chat/completions"

    for model in list(PROVIDER["models"]):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 150,
            "temperature": 0.2,
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
                    promote_model(model)
                    return data["choices"][0]["message"]["content"]

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