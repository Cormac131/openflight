"""Persistence for pots whose wiper does not survive a power cycle.

The DS3502 keeps its own setting in EEPROM and needs nothing here. The MCP401X
family is RAM-only and comes up at mid-scale every time, so for those the
setting has to live on the Pi and be re-applied at startup.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "openflight" / "sound_sensitivity.json"


def load_position(path: Path = CONFIG_PATH, *, max_position: int = 127) -> Optional[int]:
    """Return the saved wiper position, or None if there isn't a usable one.

    A missing, unreadable, malformed, or out-of-range file is not worth failing
    startup over — the caller falls back to whatever the chip came up at — so
    each case is logged and swallowed.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("[SENSITIVITY] Ignoring unreadable %s: %s", path, error)
        return None
    position = data.get("position") if isinstance(data, dict) else None
    if not isinstance(position, int) or isinstance(position, bool):
        logger.warning("[SENSITIVITY] Ignoring non-integer position in %s: %r", path, position)
        return None
    if not 0 <= position <= max_position:
        logger.warning(
            "[SENSITIVITY] Ignoring out-of-range position %d in %s (expected 0..%d)",
            position,
            path,
            max_position,
        )
        return None
    return position


def save_position(position: int, path: Path = CONFIG_PATH) -> None:
    """Persist ``position`` so the next boot restores the same sensitivity.

    Raises:
        OSError: if the file cannot be written. The caller decides whether that
            should surface — the wiper itself has already moved.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"position": position}) + "\n", encoding="utf-8")
