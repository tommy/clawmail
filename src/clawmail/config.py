"""Config loading with keyring/env credential storage."""

from __future__ import annotations

import os
from pathlib import Path

import keyring
import yaml

from clawmail.models import AppConfig

APP_NAME = "clawmail"
CONFIG_DIR = Path(
    os.environ.get("CLAWMAIL_CONFIG_DIR", "~/.config/clawmail")
).expanduser()
CONFIG_FILE = CONFIG_DIR / "config.yaml"
PROCESSED_FILE = CONFIG_DIR / "processed.txt"


def load_config() -> AppConfig:
    """Load config from YAML file, falling back to defaults."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            user_config = yaml.safe_load(f) or {}
        return AppConfig.model_validate(user_config)
    return AppConfig()


def save_config(config: AppConfig) -> None:
    """Write config to YAML file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def load_processed_uids() -> set[int]:
    """Load processed message UIDs from processed.txt in the config directory."""
    _ensure_processed_file()

    processed: set[int] = set()
    with open(PROCESSED_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                processed.add(int(line))
            except ValueError:
                # Ignore malformed lines so one bad entry doesn't break processing.
                continue
    return processed


def add_processed_uids(uids: set[int]) -> int:
    """Append newly processed UIDs. Returns how many were added."""
    if not uids:
        return 0

    existing = load_processed_uids()
    new_uids = sorted(uids - existing)
    if not new_uids:
        return 0

    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        for uid in new_uids:
            f.write(f"{uid}\n")

    return len(new_uids)


def get_imap_password() -> str | None:
    """Retrieve IMAP password from keyring, falling back to env var."""
    password = os.environ.get("CLAWMAIL_IMAP_PASSWORD")
    if password:
        return password
    return keyring.get_password(APP_NAME, "imap_password")


def set_imap_password(password: str) -> None:
    """Store IMAP password in OS keyring."""
    keyring.set_password(APP_NAME, "imap_password", password)


def get_anthropic_api_key() -> str | None:
    """Retrieve Anthropic API key from env var or keyring."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    return keyring.get_password(APP_NAME, "anthropic_api_key")


def set_anthropic_api_key(api_key: str) -> None:
    """Store Anthropic API key in OS keyring."""
    keyring.set_password(APP_NAME, "anthropic_api_key", api_key)


def _ensure_processed_file() -> None:
    """Create processed UID file if it does not exist yet."""
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PROCESSED_FILE.exists():
        PROCESSED_FILE.touch()
