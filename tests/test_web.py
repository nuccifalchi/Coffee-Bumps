import json
import stat
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from coffee_bumps import config as config_module
from coffee_bumps import web
from coffee_bumps.mail import Mailbox
from tests.test_coffee_bumps import FakeIMAP, make_raw, typesafe_client


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path / "home")
    monkeypatch.setattr(config_module, "SETTINGS_PATH", tmp_path / "home" / "settings.json")
    monkeypatch.setenv("COFFEE_BUMPS_STATE_PATH", str(tmp_path / "home" / "state.json"))
    for name in ("ICLOUD_EMAIL", "ICLOUD_APP_PASSWORD", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fake_services(monkeypatch):
    imap = FakeIMAP({"1": make_raw(subject="Receipt"), "2": make_raw(subject="Lunch?")})
    monkeypatch.setattr(web.Mailbox, "connect", classmethod(lambda cls, *args: Mailbox(imap)))
    imap.close = imap.logout = lambda: None
    answers = {"Receipt": ("receipts", 0.9, 0.0), "Lunch?": ("personal", 0.9, 0.9)}
    monkeypatch.setattr(web, "TypeSafeClient", lambda api_key: typesafe_client(answers))
    return imap


@contextmanager
def running_server():
    app = web.App()
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.RequestHandlerClass = web.make_handler(app, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, port
    finally:
        server.shutdown()
        server.server_close()


def request(port, method, path, body=None, token=None, host=None):
    conn = HTTPConnection("127.0.0.1", port)
    headers = {"Content-Type": "application/json", "Host": host or f"127.0.0.1:{port}"}
    if token:
        headers["X-Token"] = token
    conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def test_page_embeds_token():
    with running_server() as (app, port):
        status, page = request(port, "GET", "/")
    assert status == 200
    assert app.token.encode() in page
    assert b"__TOKEN__" not in page


def test_api_requires_token_and_local_host():
    with running_server() as (app, port):
        assert request(port, "GET", "/api/settings")[0] == 403
        assert request(port, "POST", "/api/scan", {}, token="wrong")[0] == 403
        assert request(port, "GET", "/", host="evil.example")[0] == 403
        assert request(port, "GET", "/api/settings", token=app.token)[0] == 200


def test_settings_saved_privately_and_password_kept_when_blank():
    with running_server() as (app, port):
        body = {"icloud_email": "me@icloud.com", "icloud_app_password": "pw", "typesafe_api_key": "key"}
        status, data = request(port, "POST", "/api/settings", body, token=app.token)
        assert status == 200 and json.loads(data) == {"configured": True, "icloud_email": "me@icloud.com"}
        request(port, "POST", "/api/settings", {"icloud_email": "me@icloud.com", "icloud_app_password": ""}, token=app.token)
    path = config_module.SETTINGS_PATH
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["icloud_app_password"] == "pw"


def test_scan_then_apply_with_user_override(fake_services):
    config_module.save_settings({"icloud_email": "me@icloud.com", "icloud_app_password": "pw", "typesafe_api_key": "k"})
    with running_server() as (app, port):
        status, data = request(port, "POST", "/api/scan", {"limit": 10}, token=app.token)
        assert status == 200
        results = {r["subject"]: r for r in json.loads(data)["results"]}
        assert results["Receipt"]["folder"] == "Receipts"
        assert not fake_services.messages.keys() - {"1", "2"}

        # The user overrides the suggestion: the receipt goes to Finance instead.
        items = [{"uid": "1", "folder": "Finance", "flag": False}, {"uid": "2", "folder": None, "flag": True}]
        status, data = request(port, "POST", "/api/apply", {"items": items}, token=app.token)
        assert status == 200 and json.loads(data) == {"moved": 1, "flagged": 1, "kept": 1}
    assert ("MOVE", "1", '"Finance"') in fake_services.commands
    assert ("STORE", "2", "+FLAGS", "(\\Flagged)") in fake_services.commands


def test_apply_rejects_unknown_folder_or_message(fake_services):
    config_module.save_settings({"icloud_email": "me@icloud.com", "icloud_app_password": "pw", "typesafe_api_key": "k"})
    with running_server() as (app, port):
        assert request(port, "POST", "/api/apply", {"items": []}, token=app.token)[0] == 400  # no scan yet
        request(port, "POST", "/api/scan", {"limit": 10}, token=app.token)
        bad_folder = {"items": [{"uid": "1", "folder": "Trash", "flag": False}]}
        assert request(port, "POST", "/api/apply", bad_folder, token=app.token)[0] == 400
        bad_uid = {"items": [{"uid": "99", "folder": None, "flag": False}]}
        assert request(port, "POST", "/api/apply", bad_uid, token=app.token)[0] == 400
    assert not any(c[0] in ("MOVE", "STORE") for c in fake_services.commands)
