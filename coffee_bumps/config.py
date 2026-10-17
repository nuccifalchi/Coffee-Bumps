"""Settings read from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Category key -> iCloud folder that confident matches are moved into.
# Categories missing from this map (personal, other) always stay in the inbox.
DEFAULT_FOLDERS = {
    "receipts": "Receipts",
    "finance": "Finance",
    "newsletters": "Newsletters",
    "notifications": "Notifications",
    "travel": "Travel",
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    icloud_email: str
    icloud_app_password: str
    imap_host: str = "imap.mail.me.com"
    imap_port: int = 993
    # Starting points, not tuned values: run with --dry-run on your own mail and adjust.
    min_category_confidence: float = 0.7
    needs_action_threshold: float = 0.6
    folders: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FOLDERS))
    state_path: Path = Path.home() / ".coffee_bumps" / "state.json"

    @classmethod
    def from_env(cls) -> "Config":
        email = os.environ.get("ICLOUD_EMAIL", "").strip()
        password = os.environ.get("ICLOUD_APP_PASSWORD", "").strip()
        missing = [name for name, value in (("ICLOUD_EMAIL", email), ("ICLOUD_APP_PASSWORD", password)) if not value]
        if not os.environ.get("TYPESAFE_API_KEY", "").strip():
            missing.append("TYPESAFE_API_KEY")
        if missing:
            raise ConfigError(f"Missing environment variables: {', '.join(missing)}. See README.md.")
        kwargs = {}
        if value := os.environ.get("COFFEE_BUMPS_MIN_CONFIDENCE"):
            kwargs["min_category_confidence"] = float(value)
        if value := os.environ.get("COFFEE_BUMPS_NEEDS_ACTION_THRESHOLD"):
            kwargs["needs_action_threshold"] = float(value)
        if value := os.environ.get("COFFEE_BUMPS_STATE_PATH"):
            kwargs["state_path"] = Path(value).expanduser()
        return cls(icloud_email=email, icloud_app_password=password, **kwargs)
