import sys
import threading

import keyring
import pyperclip
from openai import OpenAI
from pynput import keyboard
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
)

SERVICE = "browser-writing-assistant"
DEFAULT_MODEL = "gpt-6-astra"


class Events(QObject):
    hotkey = Signal()


class Assistant:
    """KI-Anbindung getrennt von der Oberfläche."""

    @staticmethod
    def api_key():
        return keyring.get_password(SERVICE, "openai")

    @staticmethod
    def save_api_key(value):
        if value:
            keyring.set_password(SERVICE, "openai", value)
        else:
            keyring.delete_password(SERVICE, "openai")

    @classmethod
    def draft(cls, prompt, context, model):
        key = cls.api_key()
        if not key:
            raise RuntimeError("Bitte zuerst in den Einstellungen einen API-Schlüssel speichern.")

        text = f"Anfrage:\n{prompt}"
        if context:
            text += f"\n\nMarkierter Text als Kontext:\n{context}"

        response = OpenAI(api_key=key).responses.create(
            model=model,
            input=(
                "Verfasse einen passenden Antwortentwurf. "
                "Gib nur den Entwurf zurück, keine Erklärung.\n\n" + text
            ),
        )
        return response.output_text


class DraftWindow(QDialog):
    def __init__(self, text, parent):
        super().__init__(parent)
        self.setWindowTitle("Antwortentwurf prüfen")
        self.resize(560, 380)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Prüfe und bearbeite den Entwurf vor dem Einfügen:"))

        self.editor = QTextEdit()
        self.editor.setPlainText(text)
        layout.addWidget(self.editor)

        buttons = QHBoxLayout()
        copy_button = QPushButton("Kopieren")
        paste_button = QPushButton("Einfügen")
        discard_button = QPushButton("Verwerfen")
        buttons.addWidget(copy_button)
        buttons.addWidget(paste_button)
        buttons.addWidget(discard_button)
        layout.addLayout(buttons)

        copy_button.clicked.connect(self.copy_text)
        paste_button.clicked.connect(self.paste_text)
        discard_button.clicked.connect(self.reject)

    def copy_text(self):
        pyperclip.copy(self.editor.toPlainText())

    def paste_text(self):
        # Clipboard-Paste in das aktuell fokussierte Feld. Kein automatisches Absenden.
        text = self.editor.toPlainText()
        if not text:
            return
        self.accept()
        pyperclip.copy(text)
        QTimer.singleShot(250, paste_shortcut)


def paste_shortcut():
    """Sendet nur die Einfügen-Tastenkombination, keinen Enter-/Submit-Befehl."""
    with keyboard.Controller() as controller:
        if sys.platform == "darwin":
            controller.press(keyboard.Key.cmd)
            controller.press("v")
            controller.release("v")
            controller.release(keyboard.Key.cmd)
        else:
            controller.press(keyboard.Key.ctrl)
            controller.press("v")
            controller.release("v")
            controller.release(keyboard.Key.ctrl)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Browser-Schreibassistent")
        self.resize(480, 280)

        self.events = Events()
        self.events.hotkey.connect(self.show_prompt)
        self.listener = keyboard.GlobalHotKeys({
            "<ctrl>+<alt>+<space>": self.events.hotkey.emit
        })
        self.listener.start()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addWidget(QLabel("Frage oder Arbeitsanweisung:"))
        self.prompt = QTextEdit()
        self.prompt.setPlaceholderText("Zum Beispiel: Formuliere eine freundliche Antwort …")
        self.prompt.setMaximumHeight(100)
        layout.addWidget(self.prompt)

        self.use_clipboard = QCheckBox(
            "Zwischenablage als Kontext verwenden (vorher markierten Text kopieren)"
        )
        self.use_clipboard.setChecked(True)
        layout.addWidget(self.use_clipboard)

        row = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem(DEFAULT_MODEL)
        row.addWidget(QLabel("Modell:"))
        row.addWidget(self.model)
        layout.addLayout(row)

        buttons = QHBoxLayout()
        settings_button = QPushButton("Einstellungen")
        test_button = QPushButton("Verbindung testen")
        draft_button = QPushButton("Entwurf erstellen")
        buttons.addWidget(settings_button)
        buttons.addWidget(test_button)
        buttons.addWidget(draft_button)
        layout.addLayout(buttons)

        settings_button.clicked.connect(self.settings)
        test_button.clicked.connect(self.test_connection)
        draft_button.clicked.connect(self.create_draft)

        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("Browser-Schreibassistent")
        menu = self.tray.contextMenu()
        from PySide6.QtWidgets import QMenu
        menu = QMenu()
        show_action = QAction("Öffnen", self)
        quit_action = QAction("Beenden", self)
        show_action.triggered.connect(self.show_prompt)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def show_prompt(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.prompt.setFocus()

    def settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Einstellungen")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("OpenAI-API-Schlüssel (wird im System-Schlüsselbund gespeichert):"))

        key_input = QLineEdit()
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(key_input)

        save = QPushButton("Schlüssel speichern")
        layout.addWidget(save)

        def save_key():
            Assistant.save_api_key(key_input.text().strip())
            key_input.clear()
            QMessageBox.information(dialog, "Gespeichert", "Der Schlüssel wurde an keyring übergeben.")
        save.clicked.connect(save_key)
        dialog.exec()

    def test_connection(self):
        try:
            key = Assistant.api_key()
            if not key:
                raise RuntimeError("Bitte zuerst einen API-Schlüssel in den Einstellungen speichern.")
            response = OpenAI(api_key=key).responses.create(
                model=self.model.currentText().strip() or DEFAULT_MODEL,
                input="Antworte nur mit: Verbindung erfolgreich",
            )
            QMessageBox.information(self, "Verbindungstest", response.output_text)
        except Exception as exc:
            QMessageBox.critical(self, "Verbindungstest fehlgeschlagen", str(exc))

    def create_draft(self):
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(self, "Eingabe fehlt", "Gib zuerst eine Frage oder Anweisung ein.")
            return

        context = ""
        if self.use_clipboard.isChecked():
            try:
                context = pyperclip.paste().strip()
            except Exception:
                pass

        self.setEnabled(False)
        self.statusBar().showMessage("Entwurf wird erstellt …")
        threading.Thread(
            target=self._draft_worker,
            args=(prompt, context, self.model.currentText().strip() or DEFAULT_MODEL),
            daemon=True,
        ).start()

    def _draft_worker(self, prompt, context, model):
        try:
            result = Assistant.draft(prompt, context, model)
            self.events.hotkey.disconnect()
            # GUI-Updates müssen im Qt-Hauptthread laufen.
            QTimer.singleShot(0, lambda: self.show_result(result))
        except Exception as exc:
            QTimer.singleShot(0, lambda: self.show_error(str(exc)))

    def show_result(self, result):
        self.events.hotkey.connect(self.show_prompt)
        self.setEnabled(True)
        self.statusBar().clearMessage()
        DraftWindow(result, self).exec()

    def show_error(self, message):
        self.setEnabled(True)
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "Fehler beim Erstellen", message)

    def closeEvent(self, event):
        event.ignore()
        self.hide()


if __name__ == "__main__":
    from PySide6.QtCore import QTimer

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
