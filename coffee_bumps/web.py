"""A web page on this computer for scanning the inbox and approving changes.

The server only listens on 127.0.0.1. Every API call must carry a token embedded in the page, so other
websites open in the same browser cannot drive it, and requests for any other host name are refused.
"""

import imaplib
import json
import logging
import secrets
import threading
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from typesafe_sdk import TypeSafeAuthenticationError, TypeSafeClient, TypeSafeError

from coffee_bumps.classify import Action
from coffee_bumps.config import Config, ConfigError, load_saved_settings, save_settings
from coffee_bumps.mail import Mailbox
from coffee_bumps.sorter import InboxChangedError, ProcessedLog, Scan, apply_actions, scan_inbox

logger = logging.getLogger(__name__)

MAX_SCAN = 200


class UserError(Exception):
    """A problem to show on the page as is."""


class App:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._last_scan: Scan | None = None

    def settings(self) -> dict:
        try:
            config = Config.load()
        except ConfigError:
            saved = load_saved_settings()
            return {"configured": False, "icloud_email": saved.get("icloud_email", "")}
        return {"configured": True, "icloud_email": config.icloud_email}

    def save_settings(self, body: dict) -> dict:
        save_settings({key: str(body.get(key, "")) for key in ("icloud_email", "icloud_app_password", "typesafe_api_key")})
        return self.settings()

    def scan(self, body: dict) -> dict:
        limit = max(1, min(int(body.get("limit", 25)), MAX_SCAN))
        config = self._config()
        with self._lock, self._mailbox(config) as mailbox, TypeSafeClient(api_key=config.typesafe_api_key) as client:
            try:
                scan = scan_inbox(mailbox, client, config, ProcessedLog(config.state_path), limit=limit)
            except TypeSafeAuthenticationError as error:
                raise UserError("TypeSafe rejected your API key. Check it in Settings.") from error
            self._last_scan = scan
        return {
            "folders": sorted(set(config.folders.values())),
            "results": [
                {
                    "uid": r.message.uid,
                    "from": r.message.sender,
                    "subject": r.message.subject,
                    "date": r.message.date,
                    "category": r.judgment.category,
                    "confidence": round(r.judgment.category_confidence, 2),
                    "needs_action": round(r.judgment.needs_action, 2),
                    **asdict(r.action),
                }
                for r in scan.results
            ],
        }

    def apply(self, body: dict) -> dict:
        config = self._config()
        folders = set(config.folders.values())
        with self._lock:
            if self._last_scan is None:
                raise UserError("Scan your inbox first.")
            scanned = {r.message.uid for r in self._last_scan.results}
            actions = {}
            for item in body.get("items", []):
                uid, folder = str(item.get("uid")), item.get("folder") or None
                if uid not in scanned or (folder is not None and folder not in folders):
                    raise UserError("That change doesn't match the last scan. Scan again.")
                actions[uid] = Action(folder=folder, flag=bool(item.get("flag")), reason="chosen on the page")
            with self._mailbox(config) as mailbox:
                try:
                    apply_actions(mailbox, ProcessedLog(config.state_path), self._last_scan.uidvalidity, actions)
                except InboxChangedError as error:
                    raise UserError(str(error)) from error
            self._last_scan = None
        return {
            "moved": sum(1 for a in actions.values() if a.folder),
            "flagged": sum(1 for a in actions.values() if a.flag),
            "kept": sum(1 for a in actions.values() if not a.folder),
        }

    @staticmethod
    def _config() -> Config:
        try:
            return Config.load()
        except ConfigError as error:
            raise UserError(str(error)) from error

    @staticmethod
    def _mailbox(config: Config):
        try:
            mailbox = Mailbox.connect(config.imap_host, config.imap_port, config.icloud_email, config.icloud_app_password)
        except imaplib.IMAP4.error as error:
            raise UserError("iCloud rejected your email or app-specific password. Check them in Settings.") from error
        except OSError as error:
            raise UserError("Couldn't reach iCloud Mail. Check your internet connection.") from error
        return _Closing(mailbox)


class _Closing:
    def __init__(self, mailbox: Mailbox) -> None:
        self.mailbox = mailbox

    def __enter__(self) -> Mailbox:
        return self.mailbox

    def __exit__(self, *exc) -> None:
        try:
            self.mailbox.close()
        except (imaplib.IMAP4.error, OSError):
            pass


def make_handler(app: App, port: int) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    routes = {"/api/settings": app.save_settings, "/api/scan": app.scan, "/api/apply": app.apply}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if not self._host_ok():
                return
            if self.path == "/":
                page = resources.files("coffee_bumps").joinpath("static/index.html").read_text()
                self._send(HTTPStatus.OK, page.replace("__TOKEN__", app.token).encode(), "text/html; charset=utf-8")
            elif self.path == "/api/settings":
                if self._token_ok():
                    self._json(HTTPStatus.OK, app.settings())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:
            if not self._host_ok() or not self._token_ok():
                return
            route = routes.get(self.path)
            if route is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                self._json(HTTPStatus.OK, route(body))
            except UserError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except (TypeSafeError, imaplib.IMAP4.error, OSError, ValueError) as error:
                logger.exception("Request to %s failed", self.path)
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Something went wrong: {error}"})

        def _host_ok(self) -> bool:
            if self.headers.get("Host") in allowed_hosts:
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
            return False

        def _token_ok(self) -> bool:
            if secrets.compare_digest(self.headers.get("X-Token", ""), app.token):
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "Forbidden"})
            return False

        def _json(self, status: HTTPStatus, data: dict) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            logger.debug(format, *args)

    return Handler


def serve(port: int = 8765, open_browser: bool = True) -> None:
    app = App()
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(app, port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Coffee Bumps is running at {url}\nKeep this window open while you use it. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
