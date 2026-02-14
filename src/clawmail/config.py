"""Config loading with keyring/env credential storage."""

from __future__ import annotations

import os
from pathlib import Path

import keyring
import yaml
from dotenv import load_dotenv

from clawmail.models import ActionType, CategoryRule

load_dotenv()

APP_NAME = "clawmail"
CONFIG_DIR = Path(
    os.environ.get("CLAWMAIL_CONFIG_DIR", "~/.config/clawmail")
).expanduser()
CONFIG_FILE = CONFIG_DIR / "config.yaml"
PROCESSED_FILE = CONFIG_DIR / "processed.txt"

DEFAULT_CONFIG = {
    "imap": {
        "host": "imap.gmail.com",
        "port": 993,
        "email": "",
    },
    "anthropic": {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
    },
    "fetch": {
        "mailbox": "INBOX",
        "days_back": 1,
        "max_emails": 50,
        "unread_only": True,
    },
    "rules": {
        "system_prompt": (
            "You are an email triage assistant. "
            "Categorize each email and decide what action to take."
        ),
        "categories": [
            {
                "name": "important",
                "description": "Emails from colleagues, clients, or about active projects",
                "action": "flag",
            },
            {
                "name": "newsletter",
                "description": "Newsletters, blog digests, weekly roundups",
                "action": "move",
                "target_folder": "Newsletters",
            },
            {
                "name": "spam",
                "description": "Marketing, unsolicited sales pitches, scams",
                "action": "trash",
            },
            {
                "name": "receipts",
                "description": "Purchase confirmations, shipping notifications",
                "action": "move",
                "target_folder": "Receipts",
            },
            {
                "name": "keep",
                "description": "Everything else worth keeping in inbox",
                "action": "none",
            },
        ],
        "suggestions_prompt": (
            "Based on the emails you just classified, suggest new categories "
            "that would improve my triage. Focus on recurring patterns that "
            "don't fit neatly into the existing categories."
        ),
    },
}


def load_config() -> dict:
    """Load config from YAML file, falling back to defaults."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            user_config = yaml.safe_load(f) or {}
        # Merge with defaults (user config wins)
        config = _deep_merge(DEFAULT_CONFIG, user_config)
    else:
        config = DEFAULT_CONFIG.copy()
    return config


def save_config(config: dict) -> None:
    """Write config to YAML file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


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


def get_category_rules(config: dict) -> list[CategoryRule]:
    """Parse category rules from config dict."""
    raw = config.get("rules", {}).get("categories", [])
    rules = []
    for entry in raw:
        rules.append(
            CategoryRule(
                name=entry["name"],
                description=entry.get("description", ""),
                action=ActionType(entry.get("action", "none")),
                target_folder=entry.get("target_folder"),
                older_than_minutes=entry.get("older_than_minutes"),
            )
        )
    return rules


def get_system_prompt(config: dict) -> str:
    """Get the system prompt from config."""
    return config["rules"]["system_prompt"]


def get_suggestions_prompt(config: dict) -> str:
    """Get the suggestions prompt from config."""
    return config["rules"]["suggestions_prompt"]


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _ensure_processed_file() -> None:
    """Create processed UID file if it does not exist yet."""
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PROCESSED_FILE.exists():
        PROCESSED_FILE.touch()
