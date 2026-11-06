"""Personal settings, read from config.json (not committed) or environment variables."""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
_CONFIG_FILE = HERE / "config.json"
_CONFIG = json.loads(_CONFIG_FILE.read_text(encoding="utf-8")) if _CONFIG_FILE.exists() else {}

EMAIL = os.environ.get("MILLENNIUM_EMAIL", _CONFIG.get("email", ""))
NAME = os.environ.get("MILLENNIUM_NAME", _CONFIG.get("name", ""))
