"""Tests for release version ordering in openflight.update.version."""

import pytest

from openflight.update.version import ReleaseVersion, parse_tag, parse_version


class TestParse:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("0.3.0", ReleaseVersion(0, 3, 0)),
            ("0.3.0-dev.42", ReleaseVersion(0, 3, 0, 42)),
            ("10.20.30-dev.0", ReleaseVersion(10, 20, 30, 0)),
        ],
    )
    def test_parses_release_versions(self, text, expected):
        assert parse_version(text) == expected
        assert parse_tag(f"v{text}") == expected
        assert str(expected) == text

    @pytest.mark.parametrize(
        "text",
        ["0.3", "0.3.0+0123456789ab", "v0.3.0", "0.3.0-rc.1", "0.3.0-dev", "abc", ""],
    )
    def test_rejects_everything_else(self, text):
        assert parse_version(text) is None

    def test_tag_requires_the_v_prefix(self):
        assert parse_tag("0.3.0") is None


class TestOrdering:
    @pytest.mark.parametrize(
        ("lower", "higher"),
        [
            ("0.2.9", "0.3.0"),
            ("0.3.0-dev.99", "0.3.0"),
            ("0.3.0", "0.3.1-dev.1"),
            ("0.3.0-dev.9", "0.3.0-dev.10"),
            ("0.3.0", "0.4.0-dev.1"),
            ("1.9.9", "2.0.0"),
        ],
    )
    def test_orders_like_semver_with_dev_builds_before_their_release(self, lower, higher):
        low, high = parse_version(lower), parse_version(higher)

        assert low < high
        assert high > low
        assert low <= high and high >= low
        assert not high < low

    def test_equal_versions_compare_equal(self):
        assert parse_version("0.3.0-dev.4") == parse_version("0.3.0-dev.4")
        assert parse_version("0.3.0") <= parse_version("0.3.0")

    def test_channel_follows_the_dev_suffix(self):
        assert parse_version("0.3.0").channel == "stable"
        assert parse_version("0.3.0-dev.1").channel == "experimental"
