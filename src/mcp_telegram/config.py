"""Configuration management for Telegram credentials."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_config_file() -> dict[str, str | int | None]:
    """Load configuration from ~/.config/mcp-telegram/config.json.

    Returns:
        A dictionary with api_id, api_hash, and optional proxy settings.
        Returns empty dict if file doesn't exist.
    """
    config_path = Path.home() / ".config" / "mcp-telegram" / "config.json"

    if not config_path.exists():
        return {}

    try:
        with open(config_path) as f:
            data = json.load(f)
        logger.debug(f"Loaded config from {config_path}")
        return data
    except Exception as e:
        logger.warning(f"Failed to load config from {config_path}: {e}")
        return {}


def create_config_template() -> str:
    """Return a template for config.json."""
    return """{
  "api_id": "YOUR_API_ID",
  "api_hash": "YOUR_API_HASH",
  "mtproto_proxy_server": null,
  "mtproto_proxy_port": null,
  "mtproto_proxy_secret": null
}
"""


def get_config_instructions() -> str:
    """Return instructions for creating the config file."""
    config_path = Path.home() / ".config" / "mcp-telegram" / "config.json"
    return f"""Create {config_path} with your Telegram API credentials:

{create_config_template()}

You can get api_id and api_hash from https://my.telegram.org/apps

Alternatively, set environment variables:
  export TELEGRAM_API_ID="your_api_id"
  export TELEGRAM_API_HASH="your_api_hash"
"""
