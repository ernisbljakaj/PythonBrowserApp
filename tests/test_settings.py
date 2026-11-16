import sys, types
sys.modules.setdefault("keyring",types.SimpleNamespace(get_password=lambda *a:None,set_password=lambda *a:None))
import AIBrowser as app

def test_load_settings_with_mocked_keyring(monkeypatch):
    values={(app.KEYRING_SERVICE,"provider"):"Groq",(app.KEYRING_SERVICE,"Groq"):"test-key"}
    monkeypatch.setattr(app.keyring,"get_password",lambda s,n:values.get((s,n)))
    assert app.load_settings()
    assert app.GROQ_API_KEY=="test-key"

def test_enqueue_replaces_pending_request():
    while not app.request_queue.empty():app.request_queue.get_nowait()
    app.enqueue("first");app.enqueue("second")
    assert app.request_queue.get_nowait()[1]=="second"
