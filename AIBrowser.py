import os
import random
import subprocess
import threading
import time
import requests


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


def get_clipboard():
    try:
        r = subprocess.run(
            ["wl-paste", "--no-newline"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def set_clipboard(text):
    subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=False)


def notify(message):
    try:
        subprocess.run(["notify-send", "KI-Assistent", message], check=False)
    except Exception:
        pass
    print(message)


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

        provider["models"] = grouped[:5]
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
                        print(f"Limit erreicht bei {model} (429) -> wechsle sofort...")
                        demote_model(provider, model)
                        break

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


def handle_clipboard_text(text):
    global last_text
    print("Frage wird an die KI gesendet...")
    answer = call_ai(text)
    if answer:
        clean_answer = answer.strip()
        with state_lock:
            last_text = clean_answer
        set_clipboard(clean_answer)
        notify("Antwort ist in der Zwischenablage.")
    else:
        notify("Keine Antwort erhalten. Bitte den Text erneut kopieren.")


def main():
    global last_text
    init_providers()

    with state_lock:
        last_text = get_clipboard().strip()
    print("Clipboard-Überwachung gestartet. Mit Strg+C beenden.")

    try:
        while True:
            text = get_clipboard().strip()
            with state_lock:
                is_new = bool(text) and text != last_text
                if is_new:
                    last_text = text

            if is_new:
                print(f"Neuer Text erkannt: {text[:80]}...")
                threading.Thread(
                    target=handle_clipboard_text,
                    args=(text,),
                    daemon=True,
                ).start()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()