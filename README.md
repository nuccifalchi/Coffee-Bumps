# Coffee Bumps

Sorts your iCloud Mail inbox. For each new email, [TypeSafe](https://typesafe.ai) answers two questions in one request:

- **What kind of email is it?** Personal, receipts, finance, newsletters, notifications, travel, or other.
- **Does it need you to act?** For example, a reply, a decision, or a bill that's due.

Coffee Bumps then decides what to do:

| Situation | What happens |
| --- | --- |
| Needs you to act | Stays in the inbox and gets flagged |
| Personal or "other" | Stays in the inbox |
| Receipts, finance, newsletters, notifications, travel, and TypeSafe is confident | Moved to a folder of that name (created if missing) |
| TypeSafe isn't confident about the category | Stays in the inbox |

Nothing is ever deleted, and unread mail stays unread.

## Setup

1. **Python 3.10 or newer.** Then, from this folder:

   ```
   python3 -m venv .venv
   .venv/bin/pip install -e .
   ```

2. **An iCloud app-specific password.** Sign in at [account.apple.com](https://account.apple.com), go to Sign-In and Security → App-Specific Passwords, and create one named "Coffee Bumps". Don't use your normal Apple Account password.

3. **A TypeSafe API key** from your TypeSafe account.

4. **Set them as environment variables** (never put them in the code or commit them):

   ```
   export ICLOUD_EMAIL="you@icloud.com"
   export ICLOUD_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
   export TYPESAFE_API_KEY="..."
   ```

## Running it

Start with a dry run. It shows what would happen to your 25 newest emails without changing anything:

```
.venv/bin/python -m coffee_bumps
```

When the results look right, apply them:

```
.venv/bin/python -m coffee_bumps --apply
```

Use `--limit 100` to look at more mail per run. Emails that have been handled are remembered in `~/.coffee_bumps/state.json`, so mail left in the inbox isn't judged (and paid for) again.

To keep the inbox sorted automatically, run `--apply` on a schedule, for example with cron every 30 minutes.

## Tuning

The thresholds are starting points, not tested values. Run a few dry runs on your own mail and adjust:

- `COFFEE_BUMPS_MIN_CONFIDENCE` (default `0.7`): raise it if mail lands in the wrong folder; lower it if too much is left in the inbox.
- `COFFEE_BUMPS_NEEDS_ACTION_THRESHOLD` (default `0.6`): raise it if too much gets flagged.

Categories, folder names, and question wording live in `coffee_bumps/config.py` and `coffee_bumps/classify.py`.

## Tests

```
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The tests use a fake mail server and fake TypeSafe answers, so they need no accounts or network.
