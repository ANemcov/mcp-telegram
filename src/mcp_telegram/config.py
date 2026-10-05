"""Configuration management for Telegram credentials."""

import json
import logging
import stat
from pathlib import Path

from xdg_base_dirs import xdg_config_home

logger = logging.getLogger(__name__)


def _config_path() -> Path:
    return xdg_config_home() / "mcp-telegram" / "config.json"


def load_config_file() -> dict[str, str | int | None]:
    """Load configuration from $XDG_CONFIG_HOME/mcp-telegram/config.json.

    Returns:
        A dictionary with api_id, api_hash, and optional proxy settings.
        Returns empty dict if file doesn't exist.
    """
    config_path = _config_path()

    if not config_path.exists():
        return {}

    mode = config_path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning(
            f"Config file {config_path} is readable by group/others. "
            "Run: chmod 600 ~/.config/mcp-telegram/config.json"
        )

    try:
        with open(config_path) as f:
            data = json.load(f)
        logger.debug(f"Loaded config from {config_path}")
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file {config_path}: {e}")
        return {}
    except OSError as e:
        logger.warning(f"Failed to read config file {config_path}: {e}")
        return {}


def get_config_instructions() -> str:
    """Return instructions for creating the config file."""
    config_path = _config_path()
    return f"""Create {config_path} with your Telegram API credentials:

{{
  "api_id": "YOUR_API_ID",
  "api_hash": "YOUR_API_HASH",
  "mtproto_proxy_server": null,
  "mtproto_proxy_port": null,
  "mtproto_proxy_secret": null
}}

You can get api_id and api_hash from https://my.telegram.org/apps

Alternatively, set environment variables:
  export TELEGRAM_API_ID="your_api_id"
  export TELEGRAM_API_HASH="your_api_hash"
"""
