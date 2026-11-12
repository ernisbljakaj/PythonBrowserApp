
import subprocess
import requests
import time
import threading

MODEL = "qwen3:8b"

letzte_frage = None
busy = False

def clipboard_lesen():
    result = subprocess.run(
        ["wl-paste", "--no-newline"],
        capture_output=True,
        text=True
    )
    return result.stdout if result.returncode == 0 else ""

def clipboard_schreiben(text):
    subprocess.run(
        ["wl-copy"],
        input=text,
        text=True,
        check=True
    )

def ki_loesen(frage):
    global busy

    try:
        print("\nKI denkt...")

        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": MODEL,
                "prompt": (
                    "Beantworte die Frage korrekt und kurz. "
                    "Gib nur die Antwort aus:\n" + frage
                ),
                "think": False,
                "stream": False
            },
            timeout=300
        )

        response.raise_for_status()
        antwort = response.json()["response"]

        clipboard_schreiben(antwort)

        print("\nAntwort:", antwort)
        print("FERTIG! STRG+V drücken.")

    except Exception as e:
        print("Fehler:", e)

    finally:
        busy = False

print("================================")
print(" KI-ZWISCHENABLAGE AKTIV")
print(" Modell:", MODEL)
print("================================")
print("STRG+C = KI fragen")
print("STRG+V = Antwort einfügen")
print("STRG+C im Terminal = Beenden")

letzter_text = clipboard_lesen()

while True:
    try:
        text = clipboard_lesen()

        if text and text != letzter_text and not busy:
            letzter_text = text
            busy = True

            threading.Thread(
                target=ki_loesen,
                args=(text,),
                daemon=True
            ).start()

        time.sleep(0.3)

    except KeyboardInterrupt:
        print("\nProgramm beendet.")
        break