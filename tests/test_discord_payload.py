"""Tests for scripts/release/discord_payload.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "release"))

import discord_payload as dp  # noqa: E402

ARGS = dict(
    title="OpenFlight v0.3.0",
    channel="stable",
    release_url="https://github.com/open-flight/openflight/releases/tag/v0.3.0",
    artifact="openflight-v0.3.0.tar.gz",
)


class TestPayload:
    def test_contains_title_channel_link_artifact_and_notes(self):
        payload = dp.build_payload(notes="### Added\n- Thing\n", **ARGS)

        content = payload["content"]
        assert content.startswith("**OpenFlight v0.3.0** · stable release\n")
        assert ARGS["release_url"] in content
        assert "Artifact: `openflight-v0.3.0.tar.gz`" in content
        assert content.endswith("### Added\n- Thing")
        assert payload["allowed_mentions"] == {"parse": []}

    def test_empty_notes_still_produce_a_message(self):
        content = dp.build_content(notes="", **ARGS)

        assert content.endswith("Artifact: `openflight-v0.3.0.tar.gz`")
        assert "…" not in content

    @pytest.mark.parametrize("length", [0, 1500, 1990, 2000, 5000, 50000])
    @pytest.mark.parametrize("with_newlines", [True, False])
    def test_never_exceeds_discord_limit(self, length, with_newlines):
        unit = "- item line\n" if with_newlines else "x"
        notes = (unit * (length // len(unit) + 1))[:length]

        content = dp.build_content(notes=notes, **ARGS)

        assert len(content) <= dp.MAX_CONTENT

    def test_truncation_cuts_at_a_line_boundary_and_marks_it(self):
        notes = "\n".join(f"- change number {n}" for n in range(400))

        content = dp.build_content(notes=notes, **ARGS)

        assert content.endswith(dp.TRUNCATION_SUFFIX)
        body = content[: -len(dp.TRUNCATION_SUFFIX)]
        assert body.endswith(tuple(f"- change number {n}" for n in range(400)))
        assert len(content) <= dp.MAX_CONTENT

    def test_short_notes_are_not_marked_truncated(self):
        assert dp.TRUNCATION_SUFFIX not in dp.build_content(notes="- one\n- two", **ARGS)

    def test_mentions_stay_literal_and_are_not_parsed(self):
        payload = dp.build_payload(notes="- fix @everyone's bug", **ARGS)

        assert "@everyone" in payload["content"]
        assert payload["allowed_mentions"]["parse"] == []

    def test_unicode_notes_are_preserved(self):
        content = dp.build_content(notes="- Café ✓ — 100 %", **ARGS)

        assert "Café ✓ — 100 %" in content


class TestCli:
    def test_writes_json_file_and_tolerates_missing_notes(self, tmp_path, capsys):
        output = tmp_path / "out" / "discord.json"

        code = dp.main(
            [
                "--title",
                "OpenFlight v0.3.0-dev.7",
                "--channel",
                "experimental",
                "--release-url",
                "https://example.test/r",
                "--artifact",
                "openflight-v0.3.0-dev.7.tar.gz",
                "--notes-file",
                str(tmp_path / "missing.md"),
                "--output",
                str(output),
            ]
        )

        assert code == 0
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["content"].startswith("**OpenFlight v0.3.0-dev.7** · experimental release")
        assert "characters" in capsys.readouterr().out
