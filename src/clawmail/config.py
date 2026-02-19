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
