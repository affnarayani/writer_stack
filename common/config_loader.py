import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT_DIR / "json" / "config.json"

_config_cache = None


def _load_config():
    global _config_cache
    if _config_cache is None:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            _config_cache = json.load(f)
    return _config_cache


def get(key, default=None):
    """Fetch a setting from json/config.json by key."""
    config = _load_config()
    return config.get(key, default)


def get_platform(platform):
    """Fetch platform-specific config block."""
    config = _load_config()
    return config.get("platforms", {}).get(platform, {})
