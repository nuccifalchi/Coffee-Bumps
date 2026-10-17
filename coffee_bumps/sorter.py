"""One pass over the inbox: judge new mail and apply the resulting actions."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from typesafe_sdk import TypeSafeClient, TypeSafeError

from coffee_bumps.classify import Action, decide, judge
from coffee_bumps.config import Config
from coffee_bumps.mail import Email, Mailbox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Result:
    message: Email
    action: Action


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


def sort_inbox(
    mailbox: Mailbox,
    client: TypeSafeClient,
    config: Config,
    processed: ProcessedLog,
    *,
    limit: int,
    apply: bool,
) -> list[Result]:
    processed.for_uidvalidity(mailbox.open_inbox())
    # Newest first; UIDs increase as mail arrives.
    pending = [uid for uid in sorted(mailbox.inbox_uids(), key=int, reverse=True) if uid not in processed][:limit]
    results = []
    for uid in pending:
        message = mailbox.fetch(uid)
        try:
            action = decide(judge(client, message), config)
        except TypeSafeError as error:
            # Leave the message untouched and unrecorded so the next run retries it.
            logger.warning("Could not judge %r: %s", message.subject, error)
            continue
        if apply:
            if action.flag:
                mailbox.flag(uid)
            if action.folder:
                mailbox.move(uid, action.folder)
            processed.add(uid)
        results.append(Result(message, action))
    if apply:
        processed.save()
    return results
