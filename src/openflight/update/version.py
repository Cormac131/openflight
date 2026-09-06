"""Ordering of release versions: ``X.Y.Z`` and ``X.Y.Z-dev.N``.

A stable release sorts above every experimental build of the same base
version (``0.3.0 > 0.3.0-dev.99``), matching the tag scheme in
docs/release-process.md. Source checkouts (``0.3.0+<sha>``) have no order.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from ..release import parse_release_tag


@dataclass(frozen=True)
class ReleaseVersion:
    """A parsed release version."""

    major: int
    minor: int
    patch: int
    dev: Optional[int] = None

    @property
    def channel(self) -> str:
        return "stable" if self.dev is None else "experimental"

    @property
    def sort_key(self) -> Tuple[int, int, int, int, int]:
        return (self.major, self.minor, self.patch, 1 if self.dev is None else 0, self.dev or 0)

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.dev is None else f"{base}-dev.{self.dev}"

    def __lt__(self, other: "ReleaseVersion") -> bool:
        return self.sort_key < other.sort_key

    def __le__(self, other: "ReleaseVersion") -> bool:
        return self.sort_key <= other.sort_key

    def __gt__(self, other: "ReleaseVersion") -> bool:
        return self.sort_key > other.sort_key

    def __ge__(self, other: "ReleaseVersion") -> bool:
        return self.sort_key >= other.sort_key


def parse_version(text: str) -> Optional[ReleaseVersion]:
    """Parse ``0.3.0`` or ``0.3.0-dev.42``; anything else (including ``+sha``) is None."""
    return parse_tag(f"v{text}")


def parse_tag(tag: str) -> Optional[ReleaseVersion]:
    """Parse ``v0.3.0`` or ``v0.3.0-dev.42``."""
    parsed = parse_release_tag(tag)
    if parsed is None:
        return None
    major, minor, patch = (int(part) for part in parsed.base_version.split("."))
    return ReleaseVersion(major, minor, patch, parsed.dev)
