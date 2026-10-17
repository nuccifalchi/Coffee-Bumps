"""Command line: python -m coffee_bumps [--apply] [--limit N]"""

import argparse
import logging
import sys
from collections import Counter

from typesafe_sdk import TypeSafeClient

from coffee_bumps.config import Config, ConfigError
from coffee_bumps.mail import Mailbox
from coffee_bumps.sorter import ProcessedLog, sort_inbox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coffee_bumps", description="Sort your iCloud inbox with TypeSafe.")
    parser.add_argument("--apply", action="store_true", help="Actually flag and move mail. Without it, only show what would happen.")
    parser.add_argument("--limit", type=int, default=25, help="Most new inbox messages to look at in this run (default 25).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        config = Config.from_env()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2

    mailbox = Mailbox.connect(config.imap_host, config.imap_port, config.icloud_email, config.icloud_app_password)
    try:
        with TypeSafeClient() as client:
            results = sort_inbox(
                mailbox, client, config, ProcessedLog(config.state_path), limit=args.limit, apply=args.apply
            )
    finally:
        mailbox.close()

    for result in results:
        target = f"-> {result.action.folder}" if result.action.folder else ("FLAG" if result.action.flag else "keep")
        print(f"{target:<18} {result.message.subject[:60]:<60}  {result.action.reason}")
    summary = Counter(r.action.folder or ("flagged" if r.action.flag else "inbox") for r in results)
    print(f"\n{len(results)} messages: " + ", ".join(f"{count} {name}" for name, count in summary.most_common()))
    if not args.apply:
        print("Dry run: nothing was changed. Re-run with --apply to flag and move mail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
