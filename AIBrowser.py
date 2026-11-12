import sys
import threading

import keyring
import pyperclip
from openai import OpenAI
from pynput import keyboard
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# Interne Einstellungen: Das Modell wird nicht in der App ausgewählt.
SERVICE = "browser-writing-assistant"
KEY_NAME = "openai"
MODEL = "gpt-4.1-mini"
HOTKEY = "<ctrl>+<alt>+<space>"


class Events(QObject):
    hotkey_pressed = Signal()
    draft_finished = Signal(str)
    draft_failed = Signal(str)
    test_finished = Signal(str)
    test_failed = Signal(str)


class CloudAI:
    """OpenAI-API-Anbindung. Der Schlüssel wird im System-Schlüsselbund gespeichert."""

    @staticmethod
    def get_api_key():
        return keyring.get_password(SERVICE, KEY_NAME)

    @staticmethod
    def save_api_key(api_key):
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("Der API-Schlüssel darf nicht leer sein.")
        keyring.set_password(SERVICE, KEY_NAME, api_key)

    @staticmethod
    def delete_api_key():
        try:
            keyring.delete_password(SERVICE, KEY_NAME)
        except keyring.errors.PasswordDeleteError:
            pass

    @classmethod
    def client(cls):
        api_key = cls.get_api_key()
        if not api_key:
            raise RuntimeError(
                "Es ist kein API-Schlüssel gespeichert. "
                "Öffne das App-Menü und wähle „Einstellungen“."
            )
        return OpenAI(api_key=api_key)

    @classmethod
    def draft(cls, prompt, context):
        user_text = f"Anweisung:\n{prompt}"
        if context:
            user_text += f"\n\nMarkierter Text als Kontext:\n{context}"

        response = cls.client().responses.create(
            model=MODEL,
            input=(
                "Schreibe einen passenden Antwortentwurf. "
                "Gib nur den Entwurf zurück, ohne zusätzliche Erklärung.\n\n"
                + user_text
            ),
        )
        return response.output_text

    @classmethod
    def test(cls):
        response = cls.client().responses.create(
            model=MODEL,
            input="Antworte nur mit: Verbindung erfolgreich",
        )
        return response.output_text


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Gib deinen OpenAI-API-Schlüssel ein. "
                "Er wird im System-Schlüsselbund gespeichert."
            )
        )

        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("API-Schlüssel")
        layout.addWidget(self.key_input)

        buttons = QHBoxLayout()
        save_button = QPushButton("Schlüssel speichern")
        delete_button = QPushButton("Gespeicherten Schlüssel löschen")
        buttons.addWidget(save_button)
        buttons.addWidget(delete_button)
        layout.addLayout(buttons)

        save_button.clicked.connect(self.save_key)
        delete_button.clicked.connect(self.delete_key)

    def save_key(self):
        try:
            CloudAI.save_api_key(self.key_input.text())
            self.key_input.clear()
            QMessageBox.information(
                self,
                "Gespeichert",
                "Der API-Schlüssel wurde im System-Schlüsselbund gespeichert.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Fehler", str(exc))

    def delete_key(self):
        try:
            CloudAI.delete_api_key()
            self.key_input.clear()
            QMessageBox.information(
                self,
                "Gelöscht",
                "Der gespeicherte API-Schlüssel wurde gelöscht.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Fehler", str(exc))


class DraftDialog(QDialog):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Entwurf prüfen")
        self.resize(600, 400)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Prüfe oder bearbeite den Entwurf. "
                "Beim Übernehmen wird er in die Zwischenablage kopiert."
            )
        )

        self.editor = QTextEdit()
        self.editor.setPlainText(text)
        layout.addWidget(self.editor)

        buttons = QHBoxLayout()
        copy_button = QPushButton("Übernehmen & kopieren")
        discard_button = QPushButton("Verwerfen")
        buttons.addWidget(copy_button)
        buttons.addWidget(discard_button)
        layout.addLayout(buttons)

        copy_button.clicked.connect(self.copy_and_close)
        discard_button.clicked.connect(self.reject)

    def copy_and_close(self):
        try:
            pyperclip.copy(self.editor.toPlainText())
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Zwischenablage-Fehler",
                f"Der Text konnte nicht kopiert werden:\n{exc}",
            )
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Browser-Schreibassistent")
        self.resize(520, 300)

        self.context_text = ""
        self.previous_clipboard = ""

        self.events = Events()
        self.events.hotkey_pressed.connect(self.capture_selection)
        self.events.draft_finished.connect(self.show_draft)
        self.events.draft_failed.connect(self.show_error)
        self.events.test_finished.connect(self.show_test_result)
        self.events.test_failed.connect(self.show_error)

        self.build_ui()
        self.setup_tray()
        self.setup_hotkey()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addWidget(QLabel("Frage oder Anweisung:"))

        self.prompt = QTextEdit()
        self.prompt.setPlaceholderText(
            "Zum Beispiel: Formuliere eine freundliche Antwort …"
        )
        self.prompt.setMaximumHeight(110)
        layout.addWidget(self.prompt)

        self.use_selection = QCheckBox(
            "Beim Hotkey markierten Browsertext als Kontext verwenden"
        )
        self.use_selection.setChecked(True)
        layout.addWidget(self.use_selection)

        buttons = QHBoxLayout()
        settings_button = QPushButton("Einstellungen")
        test_button = QPushButton("Verbindung testen")
        draft_button = QPushButton("Entwurf erstellen")

        buttons.addWidget(settings_button)
        buttons.addWidget(test_button)
        buttons.addWidget(draft_button)
        layout.addLayout(buttons)

        settings_button.clicked.connect(self.open_settings)
        test_button.clicked.connect(self.test_connection)
        draft_button.clicked.connect(self.create_draft)

        self.statusBar().showMessage(
            "Im Hintergrund aktiv. Hotkey: Ctrl+Alt+Space"
        )

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("Browser-Schreibassistent")
        self.tray.setIcon(
            QApplication.style().standardIcon(
                QApplication.style().StandardPixmap.SP_ComputerIcon
            )
        )

        menu = QMenu(self)
        open_action = QAction("Öffnen", self)
        settings_action = QAction("Einstellungen", self)
        quit_action = QAction("Beenden", self)

        open_action.triggered.connect(self.show_prompt)
        settings_action.triggered.connect(self.open_settings)
        quit_action.triggered.connect(self.quit_app)

        menu.addAction(open_action)
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    def setup_hotkey(self):
        self.hotkey_listener = keyboard.GlobalHotKeys(
            {HOTKEY: self.events.hotkey_pressed.emit}
        )
        self.hotkey_listener.start()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_prompt()

    def open_settings(self):
        SettingsDialog(self).exec()

    def show_prompt(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.prompt.setFocus()

    def capture_selection(self):
        # Der globale Hotkey wird von einem Hintergrundthread ausgelöst.
        # Die UI- und Zwischenablageaktionen laufen im Qt-Hauptthread.
        QTimer.singleShot(0, self._copy_selection)

    def _copy_selection(self):
        if not self.use_selection.isChecked():
            self.context_text = ""
            self.show_prompt()
            return

        try:
            self.previous_clipboard = pyperclip.paste()

            controller = keyboard.Controller()
            modifier = (
                keyboard.Key.cmd if sys.platform == "darwin"
                else keyboard.Key.ctrl
            )

            controller.press(modifier)
            controller.press("c")
            controller.release("c")
            controller.release(modifier)

            QTimer.singleShot(300, self._read_selection)
        except Exception as exc:
            self.context_text = ""
            self.show_prompt()
            QMessageBox.warning(
                self,
                "Auswahl kopieren",
                "Die Browserauswahl konnte nicht kopiert werden. "
                "Du kannst den Text vorher manuell kopieren.\n\n"
                f"Details: {exc}",
            )

    def _read_selection(self):
        try:
            copied_text = pyperclip.paste().strip()
            old_text = self.previous_clipboard.strip()

            # Wenn sich die Zwischenablage nicht geändert hat, wird kein
            # vorheriger, nicht zur aktuellen Auswahl gehörender Text benutzt.
            self.context_text = copied_text if copied_text != old_text else ""
        except Exception:
            self.context_text = ""

        self.show_prompt()

        if self.context_text:
            self.statusBar().showMessage(
                "Markierter Text wurde als Kontext übernommen."
            )
        else:
            self.statusBar().showMessage(
                "Keine neue Auswahl kopiert. Du kannst Text vorher manuell kopieren."
            )

    def create_draft(self):
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(
                self,
                "Eingabe fehlt",
                "Gib zuerst eine Frage oder Anweisung ein.",
            )
            return

        context = self.context_text if self.use_selection.isChecked() else ""

        self.setEnabled(False)
        self.statusBar().showMessage("Entwurf wird erstellt …")

        def worker():
            try:
                result = CloudAI.draft(prompt, context)
                self.events.draft_finished.emit(result)
            except Exception as exc:
                self.events.draft_failed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def test_connection(self):
        self.setEnabled(False)
        self.statusBar().showMessage("Verbindung wird getestet …")

        def worker():
            try:
                result = CloudAI.test()
                self.events.test_finished.emit(result)
            except Exception as exc:
                self.events.test_failed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def show_draft(self, text):
        self.setEnabled(True)
        self.statusBar().showMessage("Entwurf fertig. Bitte prüfen.")
        DraftDialog(text, self).exec()

    def show_test_result(self, text):
        self.setEnabled(True)
        self.statusBar().showMessage("Verbindung erfolgreich.")
        QMessageBox.information(self, "Verbindungstest", text)

    def show_error(self, message):
        self.setEnabled(True)
        self.statusBar().showMessage("Aktion fehlgeschlagen.")
        QMessageBox.critical(self, "Fehler", message)

    def quit_app(self):
        try:
            self.hotkey_listener.stop()
        except Exception:
            pass
        QApplication.quit()

    def closeEvent(self, event):
        # Schließen blendet das Fenster aus; die App läuft im Hintergrund weiter.
        event.ignore()
        self.hide()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()

    # Startet im Hintergrund. Öffnen über Hotkey oder Tray-Symbol.
    window.hide()

    sys.exit(app.exec())
