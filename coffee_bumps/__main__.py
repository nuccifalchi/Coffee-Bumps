"""Command line.

python -m coffee_bumps                    open the web page
python -m coffee_bumps run [--apply]      sort in the terminal (for schedules)
"""

import argparse
import logging
import sys
from collections import Counter

from typesafe_sdk import TypeSafeClient

from coffee_bumps.config import Config, ConfigError
from coffee_bumps.mail import Mailbox
from coffee_bumps.sorter import ProcessedLog, apply_actions, scan_inbox
from coffee_bumps.web import serve


def run(args: argparse.Namespace) -> int:
    try:
        config = Config.load()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2

    processed = ProcessedLog(config.state_path)
    mailbox = Mailbox.connect(config.imap_host, config.imap_port, config.icloud_email, config.icloud_app_password)
    try:
        with TypeSafeClient(api_key=config.typesafe_api_key) as client:
            scan = scan_inbox(mailbox, client, config, processed, limit=args.limit)
        if args.apply:
            apply_actions(mailbox, processed, scan.uidvalidity, {r.message.uid: r.action for r in scan.results})
    finally:
        mailbox.close()

    for result in scan.results:
        action = result.action
        target = f"-> {action.folder}" if action.folder else ("FLAG" if action.flag else "keep")
        print(f"{target:<18} {result.message.subject[:60]:<60}  {action.reason}")
    summary = Counter(r.action.folder or ("flagged" if r.action.flag else "inbox") for r in scan.results)
    print(f"\n{len(scan.results)} messages: " + ", ".join(f"{count} {name}" for name, count in summary.most_common()))
    if not args.apply:
        print("Dry run: nothing was changed. Re-run with --apply to flag and move mail.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coffee_bumps", description="Sort your iCloud inbox with TypeSafe.")
    parser.add_argument("--port", type=int, default=8765, help="Port for the web page (default 8765).")
    commands = parser.add_subparsers(dest="command")
    run_parser = commands.add_parser("run", help="Sort in the terminal instead of the web page.")
    run_parser.add_argument("--apply", action="store_true", help="Actually flag and move mail. Without it, only show what would happen.")
    run_parser.add_argument("--limit", type=int, default=25, help="Most new inbox messages to look at in this run (default 25).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "run":
        return run(args)
    serve(port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
