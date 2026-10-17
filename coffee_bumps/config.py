"""Settings from environment variables, falling back to a private settings file saved by the web page."""

import json
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

DATA_DIR = Path(os.environ.get("COFFEE_BUMPS_HOME", Path.home() / ".coffee_bumps")).expanduser()
SETTINGS_PATH = DATA_DIR / "settings.json"
CREDENTIALS = {
    "icloud_email": "ICLOUD_EMAIL",
    "icloud_app_password": "ICLOUD_APP_PASSWORD",
    "typesafe_api_key": "TYPESAFE_API_KEY",
}


class ConfigError(Exception):
    pass


def load_saved_settings() -> dict[str, str]:
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(values: dict[str, str]) -> None:
    """Save credentials readable only by the current user. Empty values keep what was saved before."""
    settings = load_saved_settings()
    settings.update({key: value.strip() for key, value in values.items() if key in CREDENTIALS and value.strip()})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.chmod(0o700)
    fd = os.open(SETTINGS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(settings, file)
    SETTINGS_PATH.chmod(0o600)


@dataclass(frozen=True)
class Config:
    icloud_email: str
    icloud_app_password: str
    typesafe_api_key: str
    imap_host: str = "imap.mail.me.com"
    imap_port: int = 993
    # Starting points, not tuned values: review a few scans of your own mail and adjust.
    min_category_confidence: float = 0.7
    needs_action_threshold: float = 0.6
    folders: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FOLDERS))
    state_path: Path = DATA_DIR / "state.json"

    @classmethod
    def load(cls) -> "Config":
        saved = load_saved_settings()
        credentials = {key: os.environ.get(env, "").strip() or saved.get(key, "") for key, env in CREDENTIALS.items()}
        missing = [CREDENTIALS[key] for key, value in credentials.items() if not value]
        if missing:
            raise ConfigError(f"Missing settings: {', '.join(missing)}. Enter them on the settings page or see README.md.")
        kwargs = {}
        if value := os.environ.get("COFFEE_BUMPS_MIN_CONFIDENCE"):
            kwargs["min_category_confidence"] = float(value)
        if value := os.environ.get("COFFEE_BUMPS_NEEDS_ACTION_THRESHOLD"):
            kwargs["needs_action_threshold"] = float(value)
        if value := os.environ.get("COFFEE_BUMPS_STATE_PATH"):
            kwargs["state_path"] = Path(value).expanduser()
        return cls(**credentials, **kwargs)
