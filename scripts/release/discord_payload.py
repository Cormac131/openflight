#!/usr/bin/env python3
"""Build the Discord webhook payload announcing a release.

Discord rejects messages over 2000 characters and would resolve
``@everyone`` written in commit subjects, so the notes are truncated at a
line boundary and mentions are disabled in the payload itself.

Usage:
    uv run python scripts/release/discord_payload.py --title "OpenFlight v0.3.0" \
        --channel stable --release-url URL --artifact openflight-v0.3.0.tar.gz \
        --notes-file notes.md --output discord.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

MAX_CONTENT = 2000
TRUNCATION_SUFFIX = "…\n_Full notes on GitHub._"


def build_content(title: str, channel: str, release_url: str, artifact: str, notes: str) -> str:
    header = f"**{title}** · {channel} release\n{release_url}\nArtifact: `{artifact}`\n\n"
    notes = notes.strip()
    if len(header) + len(notes) <= MAX_CONTENT:
        return (header + notes).rstrip()
    budget = MAX_CONTENT - len(header) - len(TRUNCATION_SUFFIX)
    cut = notes[:budget]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    return (header + cut.rstrip() + TRUNCATION_SUFFIX)[:MAX_CONTENT]


def build_payload(title: str, channel: str, release_url: str, artifact: str, notes: str) -> dict:
    return {
        "content": build_content(title, channel, release_url, artifact, notes),
        "allowed_mentions": {"parse": []},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--title", required=True)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--release-url", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--notes-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    notes = args.notes_file.read_text(encoding="utf-8") if args.notes_file.is_file() else ""
    payload = build_payload(args.title, args.channel, args.release_url, args.artifact, notes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{args.output}: {len(payload['content'])} characters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
