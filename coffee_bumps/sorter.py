"""Judging new inbox mail, and applying the chosen actions."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from typesafe_sdk import TypeSafeClient, TypeSafeError

from coffee_bumps.classify import Action, Judgment, decide, judge
from coffee_bumps.config import Config
from coffee_bumps.mail import Email, Mailbox

logger = logging.getLogger(__name__)


class InboxChangedError(Exception):
    """The inbox was reset on the server since the scan, so its message IDs no longer match."""


@dataclass(frozen=True)
class Result:
    message: Email
    judgment: Judgment
    action: Action


@dataclass(frozen=True)
class Scan:
    uidvalidity: str
    results: list[Result]


class ProcessedLog:
    """UIDs already handled, so mail left in the inbox is not judged again on every run.

    UIDs are only meaningful for one INBOX UIDVALIDITY; if the server resets it, the log starts over.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            data = {}
        self._uidvalidity = data.get("uidvalidity", "")
        self._uids = set(data.get("uids", []))

    def for_uidvalidity(self, uidvalidity: str) -> None:
        if uidvalidity != self._uidvalidity:
            self._uidvalidity = uidvalidity
            self._uids = set()

    def __contains__(self, uid: str) -> bool:
        return uid in self._uids

    def add(self, uid: str) -> None:
        self._uids.add(uid)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"uidvalidity": self._uidvalidity, "uids": sorted(self._uids, key=int)}))


def scan_inbox(mailbox: Mailbox, client: TypeSafeClient, config: Config, processed: ProcessedLog, *, limit: int) -> Scan:
    """Judge up to `limit` of the newest unhandled inbox messages. Changes nothing."""
    uidvalidity = mailbox.open_inbox()
    processed.for_uidvalidity(uidvalidity)
    # Newest first; UIDs increase as mail arrives.
    pending = [uid for uid in sorted(mailbox.inbox_uids(), key=int, reverse=True) if uid not in processed][:limit]
    results = []
    for uid in pending:
        message = mailbox.fetch(uid)
        try:
            judgment = judge(client, message)
        except TypeSafeError as error:
            # Leave the message unrecorded so the next scan retries it.
            logger.warning("Could not judge %r: %s", message.subject, error)
            continue
        results.append(Result(message, judgment, decide(judgment, config)))
    return Scan(uidvalidity, results)


def apply_actions(mailbox: Mailbox, processed: ProcessedLog, uidvalidity: str, actions: dict[str, Action]) -> None:
    """Mark read, flag, trash or move messages by UID, then remember them so later scans skip them."""
    if mailbox.open_inbox() != uidvalidity:
        raise InboxChangedError("Your inbox changed on the server since the scan. Scan again.")
    processed.for_uidvalidity(uidvalidity)
    try:
        for uid, action in actions.items():
            # Flags travel with the message, so set them before it moves.
            if action.mark_read:
                mailbox.mark_read(uid)
            if action.flag:
                mailbox.flag(uid)
            if action.trash:
                mailbox.trash(uid)
            elif action.folder:
                mailbox.move(uid, action.folder)
            processed.add(uid)
    finally:
        processed.save()
