"""Plattformunabhaengige Tests fuer die Ereignis- und Request-Logik.

Die echten Wayland- und Win32-APIs werden hier absichtlich nicht aufgerufen;
sie brauchen eine aktive Desktop-Sitzung auf dem jeweiligen Betriebssystem.
"""
import importlib.util
import pathlib
import threading
import time
import unittest
from unittest.mock import patch


SOURCE = pathlib.Path(__file__).parents[1] / "AIBrowser.py"
SPEC = importlib.util.spec_from_file_location("aibrowser", SOURCE)
aibrowser = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aibrowser)


def wait_for(predicate, timeout=2.0):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FakeClipboard:
    def __init__(self):
        self.value = ""
        self.callback = None
        self.writes = []

    def start(self, callback):
        self.callback = callback

    def stop(self):
        pass

    def read(self):
        return self.value

    def write(self, text):
        self.writes.append(text)
        self.value = text
        self.callback()  # Simuliert das Event des eigenen Clipboard-Writes.

    def copy(self, text):
        self.value = text
        self.callback()


class FakeAI:
    def __init__(self):
        self.calls = []

    def ask(self, _model, text):
        self.calls.append(text)
        return f"Antwort: {text}"


class BlockingAI(FakeAI):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def ask(self, _model, text):
        self.calls.append(text)
        self.started.set()
        self.release.wait(2)
        return f"Antwort: {text}"


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.clip = FakeClipboard()

    def tearDown(self):
        if hasattr(self, "controller"):
            self.controller.stop()

    def start(self, ai):
        self.controller = aibrowser.Controller(self.clip, ai, "test")
        self.controller.start()

    def test_own_answer_does_not_create_a_loop(self):
        ai = FakeAI()
        self.start(ai)
        self.clip.copy("erste Anfrage")
        self.assertTrue(wait_for(lambda: self.clip.writes == ["Antwort: erste Anfrage"]))
        time.sleep(0.05)
        self.assertEqual(ai.calls, ["erste Anfrage"])

    def test_same_text_can_be_copied_again_after_own_event(self):
        ai = FakeAI()
        self.start(ai)
        self.clip.copy("identisch")
        self.assertTrue(wait_for(lambda: len(self.clip.writes) == 1))
        self.clip.copy("identisch")
        self.assertTrue(wait_for(lambda: len(self.clip.writes) == 2))
        self.assertEqual(ai.calls, ["identisch", "identisch"])

    def test_many_successive_copies_are_all_processed(self):
        ai = FakeAI()
        self.start(ai)
        for number in range(10):
            text = f"Anfrage {number}"
            self.clip.copy(text)
            self.assertTrue(wait_for(lambda: len(self.clip.writes) == number + 1))
        self.assertEqual(ai.calls, [f"Anfrage {number}" for number in range(10)])

    def test_new_copy_replaces_answer_in_progress_and_is_processed(self):
        ai = BlockingAI()
        self.start(ai)
        self.clip.copy("alt")
        self.assertTrue(ai.started.wait(1))
        self.clip.copy("neu")
        ai.release.set()
        self.assertTrue(wait_for(lambda: self.clip.writes == ["Antwort: neu"]))
        self.assertEqual(ai.calls, ["alt", "neu"])

    def test_wayland_polling_only_notifies_on_changed_clipboard(self):
        backend = aibrowser.Wayland()
        values = iter(["alt", "alt", "neu", "neu", ""])
        backend.read = lambda: next(values)
        notifications = []
        previous = "alt"
        for _ in range(5):
            previous = backend._poll_once(previous, lambda: notifications.append(previous))
        self.assertEqual(notifications, ["alt", "neu"])
        self.assertEqual(backend.POLL_SECONDS, .3)

    def test_wayland_decodes_german_utf8(self):
        backend = aibrowser.Wayland()
        self.assertEqual(backend._decode_text("ä ö ü ß – Sonderzeichen".encode("utf-8")), "ä ö ü ß – Sonderzeichen")

    def test_wayland_recovers_legacy_bytes_and_rate_limits_warning(self):
        backend = aibrowser.Wayland()
        with patch("builtins.print") as output:
            self.assertEqual(backend._decode_text(b"Gr\xe4\xdf e"), "Gräß e")
            self.assertEqual(backend._decode_text(b"Gr\xe4\xdf e"), "Gräß e")
        self.assertEqual(output.call_count, 1)

    def test_wayland_read_receives_bytes_not_implicit_utf8_text(self):
        backend = aibrowser.Wayland()
        result = aibrowser.subprocess.CompletedProcess(["wl-paste"], 0, b"Gr\xe4\xdf e", b"")
        with patch.object(aibrowser.subprocess, "run", return_value=result) as run:
            self.assertEqual(backend.read(), "Gräß e")
        self.assertNotIn("text", run.call_args.kwargs)

    def test_missing_model_is_pulled_once(self):
        ai = aibrowser.Ollama()
        ai.executable = "/usr/bin/ollama"
        ai.api = lambda *_args, **_kwargs: {"models": []}
        with patch.object(aibrowser.subprocess, "run") as run:
            self.assertEqual(ai.model(), aibrowser.FALLBACK)
        run.assert_called_once_with(["/usr/bin/ollama", "pull", aibrowser.FALLBACK], check=True)

    def test_missing_ollama_stops_when_installation_is_declined(self):
        ai = aibrowser.Ollama(input_func=lambda _prompt: "n")
        ai._find_executable = lambda: None
        ai._confirm_install = lambda _message: False
        with self.assertRaisesRegex(RuntimeError, "abgebrochen"):
            ai._exe()


if __name__ == "__main__":
    unittest.main()
