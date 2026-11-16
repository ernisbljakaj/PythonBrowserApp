# AIBrowser

Lokale Ollama-Zwischenablage: kopieren, auf die Antwort warten, einfügen.
Unter Fedora GNOME Wayland arbeitet ein einzelner, schlafender Thread mit
300-ms-Polling der normalen Zwischenablage, weil GNOME das fuer `wl-paste
--watch` benoetigte data-control-Protokoll nicht bereitstellt. Windows verwendet
weiterhin sein ereignisgesteuertes Clipboard-Backend. Während einer laufenden
Anfrage ersetzt die letzte neue Kopie eine ältere noch nicht abgeschlossene
Anfrage.

Fehlt Ollama, fragt das Programm vor einer Installation nach. Unter Fedora wird
das offizielle Archiv von `ollama.com/download` geladen und erst nach der
Bestätigung mit `sudo tar` nach `/usr` entpackt; es wird kein Remote-Skript an
eine Shell weitergereicht. Unter Windows wird nach Bestätigung der offizielle
Installer von `ollama.com` gestartet. Fehlt nur ein Modell, lädt Ollama einmalig
`qwen2.5:0.5b`; vorhandene, zur RAM-Größe passende Modelle werden bevorzugt.

## Tests

```bash
python3 -m unittest discover -v
```

Die Tests simulieren Clipboard-Ereignisse. Sie prüfen Selbst-Echos,
wiederholte identische Kopien, eine neue Kopie während einer KI-Anfrage und die
Änderungserkennung des Wayland-Pollings. Außerdem prüfen sie UTF-8 mit deutschen
Umlauten, einen Legacy-Byte-Fallback und ungültige Bytefolgen.

Nicht automatisiert getestet sind die echten Desktop-Backends: `wl-paste` und
`wl-copy` benötigen eine aktive Fedora-GNOME-Wayland-Sitzung; das Win32-Backend
benötigt Windows 10 oder 11. Die Win32-ctypes-Signaturen sind auf pointerbreite
64-Bit-Handles ausgelegt, sollten aber vor einem produktiven Windows-Einsatz in
einer echten Sitzung geprüft werden.

Das Wayland-Polling beobachtet nur die normale Zwischenablage, nicht die
PRIMARY-Auswahl. Reines Markieren sollte daher keine Anfrage erzeugen und keine
GNOME-Oberfläche beeinflussen. Da Polling nur Inhaltsänderungen erkennt, kann
ein direkt erneut kopierter, identischer Text unter GNOME Wayland nicht sicher
erkannt werden. Das ist zugleich nötig, um das eigene Ausgabe-Echo nicht erneut
an Ollama zu senden; Wayland liefert dafür keine Herkunftsinformation.
