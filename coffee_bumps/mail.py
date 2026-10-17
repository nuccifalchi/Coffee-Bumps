"""Reading and moving iCloud mail over IMAP."""

import email
import email.policy
import imaplib
import re
from dataclasses import dataclass
from email.message import EmailMessage
from html.parser import HTMLParser

MAX_BODY_CHARS = 3000


@dataclass(frozen=True)
class Email:
    uid: str
    sender: str
    to: str
    subject: str
    date: str
    body: str
    has_unsubscribe_link: bool
    unread: bool = False


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(parser.parts)


def _body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, UnicodeDecodeError):
        text = part.get_payload(decode=True).decode("utf-8", errors="replace")
    if part.get_content_subtype() == "html":
        text = _html_to_text(text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_BODY_CHARS]


def parse_email(uid: str, raw: bytes, unread: bool = False) -> Email:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    return Email(
        uid=uid,
        sender=str(message.get("From", "")),
        to=str(message.get("To", "")),
        subject=str(message.get("Subject", "")),
        date=str(message.get("Date", "")),
        body=_body_text(message),
        has_unsubscribe_link=message.get("List-Unsubscribe") is not None,
        unread=unread,
    )


def _quote(mailbox: str) -> str:
    return '"' + mailbox.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Mailbox:
    """The iCloud INBOX. Reading a message never marks it as read; "deleting" moves mail to the trash folder."""

    def __init__(self, conn: imaplib.IMAP4) -> None:
        self._conn = conn
        self._known_folders: set[str] | None = None
        self._trash_folder: str | None = None

    @classmethod
    def connect(cls, host: str, port: int, user: str, password: str) -> "Mailbox":
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(user, password)
        return cls(conn)

    def close(self) -> None:
        try:
            self._conn.close()
        finally:
            self._conn.logout()

    def open_inbox(self) -> str:
        """Select INBOX and return its UIDVALIDITY, which identifies this set of UIDs."""
        self._check(self._conn.select("INBOX"))
        _, data = self._conn.response("UIDVALIDITY")
        return data[0].decode() if data and data[0] else ""

    def inbox_uids(self) -> list[str]:
        _, data = self._check(self._conn.uid("SEARCH", None, "ALL"))
        return data[0].decode().split() if data and data[0] else []

    def fetch(self, uid: str) -> Email:
        # BODY.PEEK keeps unread mail unread; iCloud returns nothing for RFC822 fetches.
        _, data = self._check(self._conn.uid("FETCH", uid, "(FLAGS BODY.PEEK[])"))
        item = next((item for item in data if isinstance(item, tuple)), None)
        if item is None:
            raise imaplib.IMAP4.error(f"No message body returned for UID {uid}")
        # Servers may send FLAGS before or after the body, so check every part except the body itself.
        flags = b" ".join(entry[0] if isinstance(entry, tuple) else entry for entry in data if entry)
        return parse_email(uid, item[1], unread=b"\\Seen" not in flags)

    def flag(self, uid: str) -> None:
        self._check(self._conn.uid("STORE", uid, "+FLAGS", "(\\Flagged)"))

    def mark_read(self, uid: str) -> None:
        self._check(self._conn.uid("STORE", uid, "+FLAGS", "(\\Seen)"))

    def trash(self, uid: str) -> None:
        """Move to the trash folder (Deleted Messages on iCloud), where Mail keeps it for 30 days."""
        self._load_folders()
        if self._trash_folder is None:
            raise imaplib.IMAP4.error("Couldn't find your Deleted Messages folder; nothing was deleted.")
        self.move(uid, self._trash_folder)

    def move(self, uid: str, folder: str) -> None:
        self._ensure_folder(folder)
        if "MOVE" in self._conn.capabilities:
            self._check(self._conn.uid("MOVE", uid, _quote(folder)))
        elif "UIDPLUS" in self._conn.capabilities:
            # UID EXPUNGE removes only this message, never other mail already marked deleted.
            self._check(self._conn.uid("COPY", uid, _quote(folder)))
            self._check(self._conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)"))
            self._check(self._conn.uid("EXPUNGE", uid))
        else:
            raise imaplib.IMAP4.error("Server supports neither MOVE nor UIDPLUS; refusing to move mail.")

    def _load_folders(self) -> None:
        if self._known_folders is not None:
            return
        _, data = self._check(self._conn.list())
        self._known_folders = set()
        for line in data:
            if isinstance(line, bytes) and (match := re.search(rb'^\(([^)]*)\).*?(?:"([^"]*)"|(\S+))\s*$', line)):
                name = (match.group(2) or match.group(3)).decode()
                self._known_folders.add(name)
                if b"\\Trash" in match.group(1).split():
                    self._trash_folder = name
        if self._trash_folder is None:
            self._trash_folder = next((n for n in ("Deleted Messages", "Trash") if n in self._known_folders), None)

    def _ensure_folder(self, folder: str) -> None:
        self._load_folders()
        if folder not in self._known_folders:
            self._check(self._conn.create(_quote(folder)))
            self._known_folders.add(folder)

    @staticmethod
    def _check(result):
        status, data = result
        if status != "OK":
            raise imaplib.IMAP4.error(f"IMAP command failed: {data!r}")
        return result
