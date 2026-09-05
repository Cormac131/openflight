#!/usr/bin/env python3
"""Release bookkeeping for OpenFlight: version bump and changelog roll.

The changelog follows Keep a Changelog. ``[Unreleased]`` collects entries
from every PR; ``release`` turns it into a dated ``[X.Y.Z]`` section and sets
``__version__``. ``next`` bumps ``__version__`` after a stable tag so the
experimental channel moves on. ``check`` and ``notes`` are what the stable
release workflow runs, so tag verification and release notes use the same
parser as the maintainer's local tooling.

Usage:
    uv run python scripts/release/prepare_release.py release 0.3.0
    uv run python scripts/release/prepare_release.py next 0.4.0
    uv run python scripts/release/prepare_release.py check 0.3.0
    uv run python scripts/release/prepare_release.py notes 0.3.0
    uv run python scripts/release/prepare_release.py normalize
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_URL = "https://github.com/open-flight/openflight"
CHANGELOG_RELATIVE = Path("docs") / "CHANGELOG.md"
VERSION_FILE_RELATIVE = Path("src") / "openflight" / "__init__.py"
UNRELEASED = "Unreleased"
KNOWN_SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
RELEASE_HEADING_RE = re.compile(r"^## \[(?P<name>[^\]]+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?\s*$")
SECTION_HEADING_RE = re.compile(r"^### (?P<title>.+?)\s*$")
LINK_REF_RE = re.compile(r"^\[[^\]]+\]: \S+\s*$")
VERSION_LINE_RE = re.compile(r'^__version__ = "(?P<version>[^"]+)"$', re.MULTILINE)


class ReleaseError(Exception):
    """A validation failure that should be reported without a traceback."""


Version = Tuple[int, int, int]


def parse_version(text: str) -> Version:
    match = VERSION_RE.match(text)
    if not match:
        raise ReleaseError(f"{text!r} is not a plain X.Y.Z version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


@dataclass
class Release:
    name: str
    date: Optional[str]
    body: List[str] = field(default_factory=list)

    @property
    def version(self) -> Optional[Version]:
        return parse_version(self.name) if VERSION_RE.match(self.name) else None


@dataclass
class Changelog:
    preamble: List[str]
    releases: List[Release]

    def find(self, name: str) -> Optional[Release]:
        return next((release for release in self.releases if release.name == name), None)

    def newest_version(self) -> Optional[Version]:
        versions = [release.version for release in self.releases if release.version]
        return max(versions) if versions else None


def parse_changelog(text: str) -> Changelog:
    """Split the file into a preamble and one Release per ``## [name]`` heading.

    Link reference lines are dropped; render_changelog regenerates them.
    """
    preamble: List[str] = []
    releases: List[Release] = []
    current: Optional[Release] = None
    for line in text.splitlines():
        heading = RELEASE_HEADING_RE.match(line)
        if heading:
            current = Release(heading.group("name"), heading.group("date"))
            releases.append(current)
            continue
        if LINK_REF_RE.match(line):
            continue
        if current is None:
            preamble.append(line)
        else:
            current.body.append(line)
    return Changelog(preamble, releases)


def _strip_blank_edges(lines: Sequence[str]) -> List[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return list(lines[start:end])


def normalize_body(body: Sequence[str]) -> List[str]:
    """Merge duplicate ``###`` sections and order them the Keep a Changelog way."""
    leading: List[str] = []
    sections: dict = {}
    order: List[str] = []
    current_title: Optional[str] = None
    for line in body:
        heading = SECTION_HEADING_RE.match(line)
        if heading:
            current_title = heading.group("title")
            if current_title not in sections:
                sections[current_title] = []
                order.append(current_title)
            sections[current_title].append([])
            continue
        if current_title is None:
            leading.append(line)
        else:
            sections[current_title][-1].append(line)

    ordered = [title for title in KNOWN_SECTIONS if title in sections]
    ordered += [title for title in order if title not in KNOWN_SECTIONS]

    result = _strip_blank_edges(leading)
    for title in ordered:
        items = [line for chunk in sections[title] for line in _strip_blank_edges(chunk)]
        if not items:
            continue
        if result:
            result.append("")
        result.append(f"### {title}")
        result.extend(items)
    return result


def is_empty_body(body: Sequence[str]) -> bool:
    return not any(line.strip() for line in normalize_body(body))


def render_changelog(changelog: Changelog, repo_url: str = REPO_URL) -> str:
    lines = _strip_blank_edges(changelog.preamble)
    for release in changelog.releases:
        heading = f"## [{release.name}]"
        if release.date:
            heading += f" - {release.date}"
        lines += ["", heading]
        body = normalize_body(release.body)
        if body:
            lines += [""] + body
    lines += [""] + _link_references(changelog, repo_url)
    return "\n".join(lines) + "\n"


def _link_references(changelog: Changelog, repo_url: str) -> List[str]:
    versioned = [release for release in changelog.releases if release.version]
    refs: List[str] = []
    if changelog.find(UNRELEASED) is not None:
        if versioned:
            refs.append(f"[{UNRELEASED}]: {repo_url}/compare/v{versioned[0].name}...HEAD")
        else:
            refs.append(f"[{UNRELEASED}]: {repo_url}/commits/HEAD")
    for index, release in enumerate(versioned):
        if index + 1 < len(versioned):
            previous = versioned[index + 1].name
            refs.append(f"[{release.name}]: {repo_url}/compare/v{previous}...v{release.name}")
        else:
            refs.append(f"[{release.name}]: {repo_url}/releases/tag/v{release.name}")
    return refs


def read_version(version_source: str) -> str:
    matches = VERSION_LINE_RE.findall(version_source)
    if len(matches) != 1:
        raise ReleaseError(
            f"expected exactly one __version__ line in {VERSION_FILE_RELATIVE}, found {len(matches)}"
        )
    return matches[0]


def write_version(version_source: str, new_version: str) -> str:
    read_version(version_source)
    return VERSION_LINE_RE.sub(f'__version__ = "{new_version}"', version_source, count=1)


@dataclass
class Workspace:
    root: Path

    @property
    def changelog_path(self) -> Path:
        return self.root / CHANGELOG_RELATIVE

    @property
    def version_path(self) -> Path:
        return self.root / VERSION_FILE_RELATIVE

    def read_changelog(self) -> Changelog:
        try:
            return parse_changelog(self.changelog_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ReleaseError(f"cannot read {self.changelog_path}: {error}") from error

    def read_version_source(self) -> str:
        try:
            return self.version_path.read_text(encoding="utf-8")
        except OSError as error:
            raise ReleaseError(f"cannot read {self.version_path}: {error}") from error

    def current_version(self) -> str:
        return read_version(self.read_version_source())


def plan_release(
    changelog: Changelog, version_source: str, new_version: str, date: str
) -> Tuple[Changelog, str]:
    """Validate everything, then return the new changelog and version source."""
    target = parse_version(new_version)
    current = read_version(version_source)
    unreleased = changelog.find(UNRELEASED)
    if unreleased is None:
        raise ReleaseError(f"{CHANGELOG_RELATIVE} has no ## [{UNRELEASED}] section")
    if is_empty_body(unreleased.body):
        raise ReleaseError(f"[{UNRELEASED}] is empty; nothing to release")
    if changelog.find(new_version) is not None:
        raise ReleaseError(f"[{new_version}] already exists in {CHANGELOG_RELATIVE}")
    newest = changelog.newest_version()
    if newest is not None and target <= newest:
        raise ReleaseError(
            f"{new_version} is not greater than the newest released version "
            f"{'.'.join(map(str, newest))}"
        )
    if parse_version(current) > target:
        raise ReleaseError(f"{new_version} is lower than the current __version__ {current}")

    released = Release(new_version, date, normalize_body(unreleased.body))
    releases = [Release(UNRELEASED, None, [])]
    releases += [released if r is unreleased else r for r in changelog.releases]
    return Changelog(changelog.preamble, releases), write_version(version_source, new_version)


def plan_next(version_source: str, new_version: str) -> str:
    target = parse_version(new_version)
    current = read_version(version_source)
    if target <= parse_version(current):
        raise ReleaseError(f"{new_version} is not greater than the current __version__ {current}")
    return write_version(version_source, new_version)


def check_release(changelog: Changelog, version_source: str, version: str) -> List[str]:
    """Return the problems that would stop ``version`` from being released."""
    problems: List[str] = []
    parse_version(version)
    current = read_version(version_source)
    if current != version:
        problems.append(f"__version__ is {current}, expected {version}")
    release = changelog.find(version)
    if release is None:
        problems.append(f"{CHANGELOG_RELATIVE} has no ## [{version}] section")
    elif is_empty_body(release.body):
        problems.append(f"[{version}] section in {CHANGELOG_RELATIVE} is empty")
    return problems


def release_notes(changelog: Changelog, version: str) -> str:
    release = changelog.find(version)
    if release is None:
        raise ReleaseError(f"{CHANGELOG_RELATIVE} has no ## [{version}] section")
    return "\n".join(normalize_body(release.body)) + "\n"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def cmd_release(workspace: Workspace, version: str, date: Optional[str], repo_url: str) -> int:
    changelog, version_source = plan_release(
        workspace.read_changelog(), workspace.read_version_source(), version, date or _today()
    )
    _write(workspace.changelog_path, render_changelog(changelog, repo_url))
    _write(workspace.version_path, version_source)
    print(f"Prepared release {version}: updated {CHANGELOG_RELATIVE} and {VERSION_FILE_RELATIVE}")
    print("Next: uv lock, commit as `chore(release): v" + version + "`, merge, then")
    print(f"      git tag v{version} <merge commit> && git push origin v{version}")
    print("      and open the follow-up bump with: prepare_release.py next <X.Y.Z>")
    return 0


def cmd_next(workspace: Workspace, version: str) -> int:
    _write(workspace.version_path, plan_next(workspace.read_version_source(), version))
    print(
        f"__version__ is now {version}. Next: uv lock, commit as `chore: start {version} development`"
    )
    return 0


def cmd_check(workspace: Workspace, version: str) -> int:
    problems = check_release(workspace.read_changelog(), workspace.read_version_source(), version)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"{version} is ready to tag")
    return 0


def cmd_notes(workspace: Workspace, version: str) -> int:
    sys.stdout.write(release_notes(workspace.read_changelog(), version))
    return 0


def cmd_normalize(workspace: Workspace, repo_url: str) -> int:
    _write(workspace.changelog_path, render_changelog(workspace.read_changelog(), repo_url))
    print(f"Normalized {CHANGELOG_RELATIVE}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root holding docs/CHANGELOG.md and src/openflight/__init__.py",
    )
    parser.add_argument("--repo-url", default=REPO_URL, help="GitHub URL used for compare links")
    commands = parser.add_subparsers(dest="command", required=True)

    release = commands.add_parser("release", help="Roll [Unreleased] into a dated section")
    release.add_argument("version")
    release.add_argument("--date", help="Release date YYYY-MM-DD (default: today, UTC)")

    commands.add_parser("next", help="Bump __version__ after a stable tag").add_argument("version")
    commands.add_parser("check", help="Verify a version is ready to tag").add_argument("version")
    commands.add_parser("notes", help="Print a version's changelog section").add_argument("version")
    commands.add_parser("normalize", help="Merge duplicate headings and regenerate links")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = Workspace(args.repo_root)
    try:
        if args.command == "release":
            return cmd_release(workspace, args.version, args.date, args.repo_url)
        if args.command == "next":
            return cmd_next(workspace, args.version)
        if args.command == "check":
            return cmd_check(workspace, args.version)
        if args.command == "notes":
            return cmd_notes(workspace, args.version)
        return cmd_normalize(workspace, args.repo_url)
    except ReleaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
