import threading
import types
import requests
import AIBrowser as app
from tray_app import TrayApp

def test_stale_generation_is_detected():
    app.generation = 2
    assert 1 != app.generation

def test_identical_manual_events_are_enqueued_twice(monkeypatch):
    while not app.request_queue.empty(): app.request_queue.get_nowait()
    app.enqueue("gleich"); first=app.request_queue.get_nowait()
    app.enqueue("gleich"); second=app.request_queue.get_nowait()
    assert first[1] == second[1] == "gleich"
    assert second[0] > first[0]

def test_401_returns_no_answer(monkeypatch):
    app.PROVIDERS[0]["key"]="x";app.PROVIDERS[0]["models"]=["llama-3.1-8b-instant"]
    monkeypatch.setattr(app.requests,"post",lambda *a,**k: types.SimpleNamespace(status_code=401,text="",headers={}))
    assert app.call_ai("frage") is None

def test_timeout_returns_no_answer(monkeypatch):
    app.PROVIDERS[0]["key"]="x";app.PROVIDERS[0]["models"]=["llama-3.1-8b-instant"]
    monkeypatch.setattr(app.requests,"post",lambda *a,**k: (_ for _ in ()).throw(requests.Timeout()))
    monkeypatch.setattr(app.time,"sleep",lambda *_: None)
    assert app.call_ai("frage") is None

def test_keyring_save_and_load(monkeypatch):
    store={}
    monkeypatch.setattr(app.keyring,"set_password",lambda s,n,v:store.__setitem__((s,n),v))
    monkeypatch.setattr(app.keyring,"get_password",lambda s,n:store.get((s,n)))
    app.keyring.set_password(app.KEYRING_SERVICE,"provider","Groq");app.keyring.set_password(app.KEYRING_SERVICE,"Groq","fake")
    assert app.load_settings() and app.GROQ_API_KEY == "fake"

def test_http_429_returns_no_answer(monkeypatch):
    app.PROVIDERS[0]["key"]="x";app.PROVIDERS[0]["models"]=["llama-3.1-8b-instant"]
    response=types.SimpleNamespace(status_code=429,text="",headers={"Retry-After":"1"})
    monkeypatch.setattr(app.requests,"post",lambda *a,**k: response)
    monkeypatch.setattr(app.time,"sleep",lambda *_: None)
    assert app.call_ai("frage") is None

class Icon:
    def __init__(self): self.updated=False;self.stopped=False
    def update_menu(self): self.updated=True
    def stop(self): self.stopped=True

def test_pause_and_resume_change_tray_state_without_api_calls():
    tray=object.__new__(TrayApp);tray.active=True
    icon=Icon();tray.pause(icon,None)
    assert not tray.active and icon.updated
    tray.enable(icon,None)
    assert tray.active

def test_tray_quit_requests_clean_shutdown():
    stopped=[];tray=object.__new__(TrayApp);tray.on_exit=lambda:stopped.append(True)
    icon=Icon();tray.quit(icon,None)
    assert stopped == [True] and icon.stopped

def test_own_answer_marker_can_be_consumed_once():
    app.own_text="KI-Antwort"
    copied="KI-Antwort"
    assert copied == app.own_text
    app.own_text=None
    assert app.own_text is None
