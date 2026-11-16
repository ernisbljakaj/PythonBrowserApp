# AI Clipboard Cloud Edition

Windows-10/11-Clipboard-Assistent für Groq oder Google AI Studio. API-Schlüssel
werden nicht im Quellcode gespeichert; die Windows-Version verwendet Credential
Manager über `keyring`. Die vorhandene `.env` ist nur eine lokale
Entwicklungsalternative und wird ignoriert.

## Build unter Windows

```bat
py -3 -m pip install -r requirements.txt
build.bat
```

Danach startet `dist\AIBrowser.exe` per Doppelklick ohne Konsolenfenster. Beim
ersten Start Anbieter und API-Key einrichten und der Cloud-Übertragung kopierter
Texte zustimmen. Das Tray-Menü aktiviert, pausiert, öffnet Einstellungen oder
beendet die App. Die Logdatei liegt unter `%LOCALAPPDATA%\AIClipboardCloud\app.log`.
