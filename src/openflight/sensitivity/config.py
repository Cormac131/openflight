"""Persistence for the sound-detector sensitivity setting.

Stored at ``~/.config/openflight/sound_sensitivity.json`` next to the cloud
uploader config. The X9C104 cannot be read back and OpenFlight deliberately
never writes the chip's own non-volatile memory, so this file is the only
record of where the user left the slider.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .x9c104 import MAX_POSITION

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "openflight" / "sound_sensitivity.json"


def load_position(path: Path = CONFIG_PATH) -> Optional[int]:
    """Return the saved wiper position, or None if there isn't a usable one.

    A missing, unreadable, malformed, or out-of-range file is not an error
    worth failing startup over — the caller falls back to the default tap — so
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
    if not 0 <= position <= MAX_POSITION:
        logger.warning(
            "[SENSITIVITY] Ignoring out-of-range position %d in %s (expected 0..%d)",
            position,
            path,
            MAX_POSITION,
        )
        return None
    return position


def save_position(position: int, path: Path = CONFIG_PATH) -> None:
    """Persist ``position`` so the next boot restores the same sensitivity.

    Raises:
        OSError: if the file cannot be written. The caller decides whether a
            failed save should surface — the wiper itself has already moved.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"position": position}) + "\n", encoding="utf-8")
