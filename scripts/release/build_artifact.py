#!/usr/bin/env python3
"""Build the downloadable release artifact for one tag.

The artifact is the tagged git tree (honouring ``export-ignore`` in
``.gitattributes``) plus the prebuilt ``ui/dist`` and a ``release.json`` that
``openflight.release`` reads on the device. Writing the file through
``ReleaseInfo`` keeps the writer and the reader on one schema.

Usage:
    uv run python scripts/release/build_artifact.py --tag v0.3.0 --channel stable \
        --repository open-flight/openflight --output dist/release
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from openflight.release import RELEASE_CHANNELS, RELEASE_FILE_NAME, ReleaseInfo, parse_release_tag

VERSION_LINE_RE = re.compile(r'^__version__ = "(?P<version>[^"]+)"$', re.MULTILINE)


class ArtifactError(Exception):
    """A validation or build failure reported without a traceback."""


def artifact_name(tag: str) -> str:
    return f"openflight-{tag}.tar.gz"


def parse_tag(tag: str, channel: str) -> str:
    """Return the version encoded in ``tag`` after checking it matches ``channel``."""
    parsed = parse_release_tag(tag)
    if parsed is None:
        raise ArtifactError(f"{tag!r} is not vX.Y.Z or vX.Y.Z-dev.N")
    if channel not in RELEASE_CHANNELS:
        raise ArtifactError(f"channel must be one of {', '.join(RELEASE_CHANNELS)}")
    if parsed.channel != channel:
        raise ArtifactError(f"{tag} does not belong to the {channel} channel")
    return parsed.version


def read_base_version(repo_root: Path) -> str:
    source = (repo_root / "src" / "openflight" / "__init__.py").read_text(encoding="utf-8")
    match = VERSION_LINE_RE.search(source)
    if not match:
        raise ArtifactError("could not find __version__ in src/openflight/__init__.py")
    return match.group("version")


def _git(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise ArtifactError(f"git {' '.join(args)} failed: {result.stderr.decode().strip()}")
    return result.stdout


def build_artifact(
    repo_root: Path,
    tag: str,
    channel: str,
    repository: str,
    output_dir: Path,
    commit: Optional[str] = None,
    built_at: Optional[str] = None,
) -> Path:
    """Write ``openflight-<tag>.tar.gz`` and its ``.sha256`` into ``output_dir``."""
    version = parse_tag(tag, channel)
    base_version = read_base_version(repo_root)
    if not version.startswith(base_version):
        raise ArtifactError(f"{tag} does not match __version__ {base_version}")
    dist_dir = repo_root / "ui" / "dist"
    if not (dist_dir / "index.html").is_file():
        raise ArtifactError(f"{dist_dir} has no index.html; build the UI first")

    commit = _git(repo_root, "rev-parse", commit or "HEAD").decode().strip()
    built_at = built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    info = ReleaseInfo(
        version=version,
        base_version=base_version,
        channel=channel,
        tag=tag,
        commit=commit[:12],
        built_at=built_at,
        repository=repository,
    )

    prefix = f"openflight-{tag}"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / artifact_name(tag)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        tree_tar = staging / "tree.tar"
        tree_tar.write_bytes(
            _git(repo_root, "archive", "--format=tar", f"--prefix={prefix}/", commit)
        )
        with tarfile.open(tree_tar) as tree:
            tree.extractall(staging)
        tree_tar.unlink()
        root = staging / prefix
        shutil.copytree(dist_dir, root / "ui" / "dist", dirs_exist_ok=True)
        (root / RELEASE_FILE_NAME).write_text(
            json.dumps(info.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(root, arcname=prefix)

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_name(archive_path.name + ".sha256").write_text(
        f"{digest}  {archive_path.name}\n", encoding="utf-8"
    )
    return archive_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tag", required=True, help="Release tag, e.g. v0.3.0 or v0.3.0-dev.42")
    parser.add_argument("--channel", required=True, choices=RELEASE_CHANNELS)
    parser.add_argument(
        "--repository", required=True, help="GitHub owner/name that hosts the release"
    )
    parser.add_argument("--output", required=True, type=Path, help="Directory for the artifact")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--commit", help="Commit to archive (default: HEAD)")
    parser.add_argument("--built-at", help="ISO 8601 build time (default: now, UTC)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = build_artifact(
            args.repo_root.resolve(),
            args.tag,
            args.channel,
            args.repository,
            args.output,
            commit=args.commit,
            built_at=args.built_at,
        )
    except ArtifactError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
