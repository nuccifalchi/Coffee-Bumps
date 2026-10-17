import imaplib
import json
from email.message import EmailMessage

import httpx2
import pytest
from typesafe_sdk import TypeSafeClient

from coffee_bumps.classify import Judgment, decide
from coffee_bumps.config import Config
from coffee_bumps.mail import Mailbox, parse_email
from coffee_bumps.sorter import InboxChangedError, ProcessedLog, apply_actions, scan_inbox


def make_raw(subject="Hello", body="Hi there", html=None, unsubscribe=False) -> bytes:
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "me@icloud.com"
    message["Subject"] = subject
    message["Date"] = "Fri, 16 Oct 2026 10:00:00 -0700"
    if unsubscribe:
        message["List-Unsubscribe"] = "<mailto:unsub@example.com>"
    if html:
        message.set_content(html, subtype="html")
    else:
        message.set_content(body)
    return message.as_bytes()


@pytest.fixture
def config(tmp_path):
    return Config(
        icloud_email="me@icloud.com", icloud_app_password="x", typesafe_api_key="k", state_path=tmp_path / "state.json"
    )


def test_parse_email_extracts_headers_and_plain_body():
    parsed = parse_email("7", make_raw(subject="Your order shipped", body="Tracking:   123\n\nThanks", unsubscribe=True))
    assert parsed.subject == "Your order shipped"
    assert parsed.sender == "Sender <sender@example.com>"
    assert parsed.body == "Tracking: 123 Thanks"
    assert parsed.has_unsubscribe_link


def test_parse_email_strips_html_and_scripts():
    html = "<html><style>p{}</style><body><p>Big <b>sale</b> today</p><script>x()</script></body></html>"
    assert parse_email("1", make_raw(html=html)).body == "Big sale today"


@pytest.mark.parametrize(
    ("judgment", "folder", "flag"),
    [
        (Judgment("receipts", 0.9, 0.1), "Receipts", False),
        (Judgment("receipts", 0.5, 0.1), None, False),  # unsure: stays in inbox
        (Judgment("personal", 0.95, 0.1), None, False),  # personal mail never moves
        (Judgment("other", 0.95, 0.1), None, False),
        (Judgment("finance", 0.95, 0.8), None, True),  # a bill that is due stays and gets flagged
    ],
)
def test_decide(config, judgment, folder, flag):
    action = decide(judgment, config)
    assert (action.folder, action.flag) == (folder, flag)


class FakeIMAP:
    """Enough of imaplib.IMAP4 for Mailbox, recording every command."""

    capabilities = ("IMAP4REV1", "MOVE")

    def __init__(self, messages: dict[str, bytes], seen: set[str] = frozenset()):
        self.messages = messages
        self.seen = set(seen)
        self.folders = {"INBOX": "", "Archive": "", "Deleted Messages": " \\Trash"}
        self.commands = []

    def select(self, mailbox):
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code):
        return code, [b"1234"]

    def list(self):
        return "OK", [f'(\\HasNoChildren{attrs}) "/" "{name}"'.encode() for name, attrs in self.folders.items()]

    def create(self, folder):
        self.commands.append(("CREATE", folder))
        self.folders[folder.strip('"')] = ""
        return "OK", [b""]

    def uid(self, command, *args):
        self.commands.append((command, *args))
        if command == "SEARCH":
            return "OK", [" ".join(self.messages).encode()]
        if command == "FETCH":
            flags = "\\Seen" if args[0] in self.seen else ""
            return "OK", [(f"{args[0]} (UID {args[0]} FLAGS ({flags}) BODY[]".encode(), self.messages[args[0]]), b")"]
        if command == "MOVE":
            del self.messages[args[0]]
        return "OK", [b""]


def typesafe_client(answers_by_subject: dict[str, dict]) -> TypeSafeClient:
    """A real TypeSafeClient whose HTTP requests are answered locally."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert set(body["questions"]) == {"category", "needs_action"}
        category, confidence, needs_action = answers_by_subject[body["state"]["email"]["subject"]]
        return httpx2.Response(
            200,
            json={
                "model": "jev-test",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "answers": {
                    "category": {
                        "type": "choice",
                        "choice": category,
                        "confidence": confidence,
                        "probabilities": {category: confidence},
                    },
                    "needs_action": {"type": "noul", "noul": needs_action},
                },
            },
        )

    return TypeSafeClient(api_key="test", transport=httpx2.MockTransport(handler), compression=False)


def test_scan_inbox_changes_nothing(config):
    imap = FakeIMAP({"1": make_raw(subject="Receipt"), "2": make_raw(subject="Lunch?")})
    client = typesafe_client({"Receipt": ("receipts", 0.9, 0.0), "Lunch?": ("personal", 0.9, 0.9)})
    scan = scan_inbox(Mailbox(imap), client, config, ProcessedLog(config.state_path), limit=10)
    assert scan.uidvalidity == "1234"
    assert [(r.message.subject, r.action.folder, r.action.flag) for r in scan.results] == [
        ("Lunch?", None, True),
        ("Receipt", "Receipts", False),
    ]
    assert not any(c[0] in ("MOVE", "STORE", "CREATE") for c in imap.commands)
    assert not config.state_path.exists()


def test_apply_actions_moves_flags_and_later_scans_skip(config):
    imap = FakeIMAP({"1": make_raw(subject="Receipt"), "2": make_raw(subject="Lunch?")})
    client = typesafe_client({"Receipt": ("receipts", 0.9, 0.0), "Lunch?": ("personal", 0.9, 0.9)})
    scan = scan_inbox(Mailbox(imap), client, config, ProcessedLog(config.state_path), limit=10)
    actions = {r.message.uid: r.action for r in scan.results}
    apply_actions(Mailbox(imap), ProcessedLog(config.state_path), scan.uidvalidity, actions)
    assert ("CREATE", '"Receipts"') in imap.commands
    assert ("MOVE", "1", '"Receipts"') in imap.commands
    assert ("STORE", "2", "+FLAGS", "(\\Flagged)") in imap.commands
    assert list(imap.messages) == ["2"]

    imap.commands.clear()
    again = scan_inbox(Mailbox(imap), client, config, ProcessedLog(config.state_path), limit=10)
    assert again.results == []
    assert not any(c[0] == "FETCH" for c in imap.commands)


def test_apply_actions_refuses_when_inbox_was_reset(config):
    imap = FakeIMAP({"1": make_raw()})
    with pytest.raises(InboxChangedError):
        apply_actions(Mailbox(imap), ProcessedLog(config.state_path), "9999", {})
    assert not any(c[0] == "MOVE" for c in imap.commands)


def test_processed_log_resets_when_uidvalidity_changes(tmp_path):
    log = ProcessedLog(tmp_path / "state.json")
    log.for_uidvalidity("1")
    log.add("5")
    log.save()
    reloaded = ProcessedLog(tmp_path / "state.json")
    reloaded.for_uidvalidity("1")
    assert "5" in reloaded
    reloaded.for_uidvalidity("2")
    assert "5" not in reloaded


def test_fetch_reports_unread_from_flags_not_body():
    imap = FakeIMAP({"1": make_raw(body="I have \\Seen this"), "2": make_raw()}, seen={"2"})
    mailbox = Mailbox(imap)
    assert mailbox.fetch("1").unread
    assert not mailbox.fetch("2").unread


def test_apply_actions_marks_read_then_trashes(config):
    imap = FakeIMAP({"1": make_raw(subject="Junk")})
    from coffee_bumps.classify import Action

    actions = {"1": Action(folder=None, flag=False, reason="", mark_read=True, trash=True)}
    apply_actions(Mailbox(imap), ProcessedLog(config.state_path), "1234", actions)
    stores = [c for c in imap.commands if c[0] == "STORE"]
    assert stores == [("STORE", "1", "+FLAGS", "(\\Seen)")]
    assert imap.commands[-1] == ("MOVE", "1", '"Deleted Messages"')
    assert not any(c[0] in ("CREATE", "EXPUNGE") for c in imap.commands)
    assert imap.messages == {}


def test_trash_refuses_without_a_trash_folder(config):
    imap = FakeIMAP({"1": make_raw()})
    del imap.folders["Deleted Messages"]
    with pytest.raises(imaplib.IMAP4.error):
        Mailbox(imap).trash("1")
    assert "1" in imap.messages
