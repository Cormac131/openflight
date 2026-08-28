"""Locate a bash that can run repo scripts under pytest.

Windows PATH often puts WSL's ``C:\\Windows\\system32\\bash.exe`` first.
That launcher does not inherit custom environment variables from
``subprocess.run(env=...)``, so kiosk tests that set ``SESSION_LOCATION``
or ``OPENFLIGHT_DETECT_CMD`` would silently see empty values. Git Bash
does inherit them.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def is_wsl_bash(path: str | None) -> bool:
    """True for the Windows WSL trampoline, not a real POSIX bash."""
    if not path:
        return False
    normalized = path.replace("\\", "/").lower()
    return normalized.endswith("windows/system32/bash.exe") or "/system32/bash.exe" in normalized


def find_bash() -> str | None:
    """Prefer Git Bash on Windows; otherwise the first non-WSL bash on PATH."""
    program_files = (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    )
    for root in program_files:
        for relative in (
            Path("Git") / "bin" / "bash.exe",
            Path("Git") / "usr" / "bin" / "bash.exe",
        ):
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)

    which = shutil.which("bash")
    if which and not is_wsl_bash(which):
        return which
    return None


def posix_path(path: str | Path) -> str:
    """Turn a Windows path into the ``/c/Users/...`` form Git Bash can ``-r``."""
    raw = os.fspath(path)
    if len(raw) >= 2 and raw[1] == ":":
        return "/" + raw[0].lower() + raw[2:].replace("\\", "/")
    return raw.replace("\\", "/")
