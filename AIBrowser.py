import subprocess
import requests
import time
import threading

# ── API-Einstellungen: hier deine Werte eintragen ──────────────────
API_URL = "https://api.example.com/v1/chat/completions"
MODEL = "dein-modellname"
API_KEY = "HIER_DEIN_API_KEY_EINFÜGEN"
# ──────────────────────────────────────────────────────────────────

busy = False
zu_ignorieren = None


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
    global busy, zu_ignorieren

    try:
        print("\nKI denkt...")

        response = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Beantworte die Frage korrekt und kurz. "
                            "Gib nur die Antwort aus:\n" + frage
                        ),
                    }
                ],
            },
            timeout=300,
        )

        response.raise_for_status()
        daten = response.json()
        antwort = daten["choices"][0]["message"]["content"]

        # Die KI-Antwort nicht gleich erneut an die API senden.
        zu_ignorieren = antwort
        clipboard_schreiben(antwort)

        print("\nAntwort:", antwort)
        print("FERTIG! STRG+V drücken.")

    except requests.exceptions.HTTPError as e:
        print("HTTP-Fehler:", e)
        if e.response is not None:
            print("Antwort des Anbieters:", e.response.text)

    except Exception as e:
        print("Fehler:", e)

    finally:
        busy = False


if API_KEY == "HIER_DEIN_API_KEY_EINFÜGEN":
    raise SystemExit("Bitte oben in der Datei deinen API-Key eintragen.")

if not API_URL or not MODEL:
    raise SystemExit("Bitte API_URL und MODEL oben in der Datei eintragen.")

print("================================")
print(" KI-ZWISCHENABLAGE AKTIV")
print(" Modell:", MODEL)
print("================================")
print("Kopiere eine Frage in die Zwischenablage.")
print("Die Antwort wird automatisch hineinkopiert.")
print("STRG+C im Terminal = Programm beenden")

letzter_text = clipboard_lesen()

while True:
    try:
        text = clipboard_lesen()

        if text and text != letzter_text:
            letzter_text = text

            if text == zu_ignorieren:
                zu_ignorieren = None
            elif not busy:
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
