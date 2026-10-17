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

Scanning never marks mail as read. On the page you can also mark emails as read, or delete them: deleted emails go to iCloud's Deleted Messages folder, where you can recover them for 30 days.

## Setup

1. **Python 3.10 or newer.** Then, from this folder:

   ```
   python3 -m venv .venv
   .venv/bin/pip install -e .
   ```

2. **An iCloud app-specific password.** Sign in at [account.apple.com](https://account.apple.com), go to Sign-In and Security → App-Specific Passwords, and create one named "Coffee Bumps". Don't use your normal Apple Account password, and don't share it or screenshot it.

3. **A TypeSafe API key** from your TypeSafe account.

## Running it

```
.venv/bin/python -m coffee_bumps
```

This opens Coffee Bumps in your browser. Keep the Terminal window open while you use it; press Ctrl+C there to stop.

1. **First time:** enter your iCloud email, app-specific password and TypeSafe key, then click Save. They're stored only on your Mac, in `~/.coffee_bumps/settings.json`, which only your user account can read.
2. **Scan inbox** shows your newest emails with a suggested folder for each. Scanning changes nothing.
3. Change any suggestion you disagree with. Tick **Read** to clear the unread badge (the box in the header ticks them all), or choose **Delete** in the dropdown. Then click **Apply changes**. You'll be asked to confirm before anything is deleted.

Emails that have been handled are remembered in `~/.coffee_bumps/state.json`, so mail left in the inbox isn't judged (and paid for) again.

The page only works from your own Mac: it listens on 127.0.0.1, and other websites can't control it.

### From the Terminal instead

For schedules (for example, cron every 30 minutes), sort without the page:

```
.venv/bin/python -m coffee_bumps run            # show what would happen
.venv/bin/python -m coffee_bumps run --apply    # flag and move mail
```

Add `--limit 100` to look at more mail per run. Environment variables `ICLOUD_EMAIL`, `ICLOUD_APP_PASSWORD` and `TYPESAFE_API_KEY`, if set, override the saved settings.

## Tuning

The thresholds are starting points, not tested values. Look over a few scans of your own mail and adjust:

- `COFFEE_BUMPS_MIN_CONFIDENCE` (default `0.7`): raise it if mail lands in the wrong folder; lower it if too much is left in the inbox.
- `COFFEE_BUMPS_NEEDS_ACTION_THRESHOLD` (default `0.6`): raise it if too much gets flagged.

Categories, folder names, and question wording live in `coffee_bumps/config.py` and `coffee_bumps/classify.py`.

## Tests

```
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The tests use a fake mail server and fake TypeSafe answers, so they need no accounts or network.
