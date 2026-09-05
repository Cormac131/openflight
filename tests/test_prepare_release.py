"""Tests for scripts/release/prepare_release.py (changelog roll and version bump)."""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "release"))

import prepare_release as pr  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_CHANGELOG = """# Changelog

All notable changes are documented here.

## [Unreleased]

### Fixed
- Second fix.

### Added
- New thing.

### Fixed
- First fix, listed later in the file.

### Known Limitations
- Still slow.

## [0.2.0] - 2024-12-01

### Added
- Web UI.

## [0.1.0] - 2024-10-01

### Added
- Initial driver.

[Unreleased]: https://github.com/jewbetcha/openflight/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jewbetcha/openflight/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jewbetcha/openflight/releases/tag/v0.1.0
"""

SAMPLE_INIT = '"""OpenFlight."""\n\n__version__ = "0.2.0"\n\nfrom .x import y\n'


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "openflight").mkdir(parents=True)
    (tmp_path / "docs" / "CHANGELOG.md").write_text(SAMPLE_CHANGELOG, encoding="utf-8")
    (tmp_path / "src" / "openflight" / "__init__.py").write_text(SAMPLE_INIT, encoding="utf-8")
    return pr.Workspace(tmp_path)


def _run(workspace, *args):
    return pr.main(["--repo-root", str(workspace.root), *args])


class TestParsing:
    def test_splits_preamble_releases_and_drops_link_refs(self):
        changelog = pr.parse_changelog(SAMPLE_CHANGELOG)

        assert changelog.preamble[0] == "# Changelog"
        assert [r.name for r in changelog.releases] == ["Unreleased", "0.2.0", "0.1.0"]
        assert changelog.releases[1].date == "2024-12-01"
        assert not any("github.com" in line for r in changelog.releases for line in r.body)

    def test_newest_version_ignores_unreleased(self):
        assert pr.parse_changelog(SAMPLE_CHANGELOG).newest_version() == (0, 2, 0)

    @pytest.mark.parametrize("bad", ["0.3", "v0.3.0", "0.3.0-dev.1", "abc"])
    def test_rejects_versions_that_are_not_plain_x_y_z(self, bad):
        with pytest.raises(pr.ReleaseError):
            pr.parse_version(bad)


class TestNormalizeBody:
    def test_merges_duplicate_headings_in_keep_a_changelog_order(self):
        body = pr.parse_changelog(SAMPLE_CHANGELOG).find("Unreleased").body

        assert pr.normalize_body(body) == [
            "### Added",
            "- New thing.",
            "",
            "### Fixed",
            "- Second fix.",
            "- First fix, listed later in the file.",
            "",
            "### Known Limitations",
            "- Still slow.",
        ]

    def test_unknown_headings_keep_first_seen_order_after_known_ones(self):
        body = ["### Zeta", "- z", "### Alpha", "- a", "### Removed", "- r"]

        assert pr.normalize_body(body) == [
            "### Removed",
            "- r",
            "",
            "### Zeta",
            "- z",
            "",
            "### Alpha",
            "- a",
        ]

    def test_text_before_the_first_heading_is_kept_on_top(self):
        assert pr.normalize_body(["", "Intro line.", "", "### Fixed", "- x", ""]) == [
            "Intro line.",
            "",
            "### Fixed",
            "- x",
        ]

    def test_headings_without_items_are_dropped(self):
        assert pr.normalize_body(["### Added", "", "### Fixed", "- x"]) == ["### Fixed", "- x"]

    def test_empty_detection(self):
        assert pr.is_empty_body(["", "### Added", ""])
        assert not pr.is_empty_body(["### Added", "- x"])


class TestRender:
    def test_regenerates_links_for_the_configured_repository(self):
        text = pr.render_changelog(pr.parse_changelog(SAMPLE_CHANGELOG), "https://example.test/r")

        assert text.endswith(
            "[Unreleased]: https://example.test/r/compare/v0.2.0...HEAD\n"
            "[0.2.0]: https://example.test/r/compare/v0.1.0...v0.2.0\n"
            "[0.1.0]: https://example.test/r/releases/tag/v0.1.0\n"
        )
        assert "jewbetcha" not in text

    def test_unreleased_link_without_any_release_points_at_head(self):
        text = pr.render_changelog(
            pr.parse_changelog("# Log\n\n## [Unreleased]\n\n### Added\n- x\n")
        )

        assert text.endswith(f"[Unreleased]: {pr.REPO_URL}/commits/HEAD\n")

    def test_render_is_idempotent(self):
        once = pr.render_changelog(pr.parse_changelog(SAMPLE_CHANGELOG))
        twice = pr.render_changelog(pr.parse_changelog(once))

        assert once == twice


class TestVersionFile:
    def test_read_and_write_version(self):
        assert pr.read_version(SAMPLE_INIT) == "0.2.0"
        updated = pr.write_version(SAMPLE_INIT, "0.3.0")
        assert pr.read_version(updated) == "0.3.0"
        assert updated.replace('"0.3.0"', '"0.2.0"') == SAMPLE_INIT

    @pytest.mark.parametrize("source", ["", '__version__ = "1"\n__version__ = "2"\n'])
    def test_requires_exactly_one_version_line(self, source):
        with pytest.raises(pr.ReleaseError):
            pr.read_version(source)


class TestReleaseCommand:
    def test_rolls_unreleased_into_a_dated_section_and_bumps_version(self, workspace, capsys):
        assert _run(workspace, "release", "0.3.0", "--date", "2026-09-05") == 0

        text = workspace.changelog_path.read_text(encoding="utf-8")
        assert "## [Unreleased]\n\n## [0.3.0] - 2026-09-05\n\n### Added\n- New thing." in text
        assert "### Fixed\n- Second fix.\n- First fix, listed later in the file." in text
        assert (
            "[Unreleased]: https://github.com/open-flight/openflight/compare/v0.3.0...HEAD" in text
        )
        assert "[0.3.0]: https://github.com/open-flight/openflight/compare/v0.2.0...v0.3.0" in text
        assert workspace.current_version() == "0.3.0"
        assert "git tag v0.3.0" in capsys.readouterr().out

    def test_defaults_to_todays_date(self, workspace):
        _run(workspace, "release", "0.3.0")

        assert f"## [0.3.0] - {pr._today()}" in workspace.changelog_path.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("changelog", "version", "message"),
        [
            pytest.param(
                SAMPLE_CHANGELOG.replace("## [Unreleased]", "## [Pending]"),
                "0.3.0",
                "no ## [Unreleased]",
                id="missing-unreleased",
            ),
            pytest.param(
                "# Log\n\n## [Unreleased]\n\n### Added\n\n## [0.2.0] - 2024-12-01\n\n### Added\n- x\n",
                "0.3.0",
                "is empty",
                id="empty-unreleased",
            ),
            pytest.param(SAMPLE_CHANGELOG, "0.2.0", "already exists", id="duplicate-version"),
            pytest.param(SAMPLE_CHANGELOG, "0.1.5", "not greater", id="lower-than-newest"),
            pytest.param(SAMPLE_CHANGELOG, "0.3", "not a plain", id="invalid-version"),
        ],
    )
    def test_refuses_and_leaves_both_files_untouched(
        self, workspace, capsys, changelog, version, message
    ):
        workspace.changelog_path.write_text(changelog, encoding="utf-8")

        assert _run(workspace, "release", version) == 1

        assert message in capsys.readouterr().err
        assert workspace.changelog_path.read_text(encoding="utf-8") == changelog
        assert workspace.read_version_source() == SAMPLE_INIT

    def test_refuses_to_go_below_the_current_version(self, workspace, capsys):
        workspace.version_path.write_text(SAMPLE_INIT.replace("0.2.0", "0.4.0"), encoding="utf-8")

        assert _run(workspace, "release", "0.3.0") == 1
        assert "lower than the current __version__" in capsys.readouterr().err

    def test_version_file_problem_is_reported_before_writing(self, workspace):
        workspace.version_path.write_text("nothing here\n", encoding="utf-8")

        assert _run(workspace, "release", "0.3.0") == 1
        assert workspace.changelog_path.read_text(encoding="utf-8") == SAMPLE_CHANGELOG


class TestNextCommand:
    def test_bumps_only_the_version_file(self, workspace):
        assert _run(workspace, "next", "0.3.0") == 0

        assert workspace.current_version() == "0.3.0"
        assert workspace.changelog_path.read_text(encoding="utf-8") == SAMPLE_CHANGELOG

    @pytest.mark.parametrize("version", ["0.2.0", "0.1.9"])
    def test_refuses_non_increasing_versions(self, workspace, capsys, version):
        assert _run(workspace, "next", version) == 1
        assert "not greater" in capsys.readouterr().err
        assert workspace.current_version() == "0.2.0"


class TestCheckCommand:
    def test_passes_when_version_and_section_agree(self, workspace, capsys):
        assert _run(workspace, "check", "0.2.0") == 0
        assert "ready to tag" in capsys.readouterr().out

    def test_reports_every_problem(self, workspace, capsys):
        assert _run(workspace, "check", "0.3.0") == 1

        err = capsys.readouterr().err
        assert "__version__ is 0.2.0, expected 0.3.0" in err
        assert "no ## [0.3.0] section" in err

    def test_rejects_an_empty_section(self, workspace, capsys):
        workspace.changelog_path.write_text(
            SAMPLE_CHANGELOG.replace("### Added\n- Web UI.\n", ""), encoding="utf-8"
        )

        assert _run(workspace, "check", "0.2.0") == 1
        assert "is empty" in capsys.readouterr().err

    def test_parser_accepts_the_real_changelog_layout(self, tmp_path):
        """Guard the parser against the repository's actual formatting."""
        text = (REPO_ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")

        changelog = pr.parse_changelog(text)

        assert changelog.find("Unreleased") is not None
        assert changelog.newest_version() is not None
        rendered = pr.render_changelog(changelog)
        assert pr.render_changelog(pr.parse_changelog(rendered)) == rendered


class TestNotesCommand:
    def test_prints_the_normalized_section_body_only(self, workspace, capsys):
        assert _run(workspace, "notes", "0.2.0") == 0

        assert capsys.readouterr().out == "### Added\n- Web UI.\n"

    def test_stops_before_the_next_release_and_link_refs(self, workspace, capsys):
        _run(workspace, "notes", "0.1.0")

        out = capsys.readouterr().out
        assert "Initial driver" in out
        assert "0.2.0" not in out
        assert "github.com" not in out

    def test_unknown_version_fails(self, workspace, capsys):
        assert _run(workspace, "notes", "9.9.9") == 1
        assert "no ## [9.9.9]" in capsys.readouterr().err


class TestNormalizeCommand:
    def test_rewrites_headings_and_links_without_releasing(self, workspace):
        assert _run(workspace, "normalize") == 0

        text = workspace.changelog_path.read_text(encoding="utf-8")
        assert text.count("### Fixed") == 1
        assert "[0.2.0]: https://github.com/open-flight/openflight/compare/v0.1.0...v0.2.0" in text
        assert "## [Unreleased]\n\n### Added" in text
        assert workspace.current_version() == "0.2.0"
