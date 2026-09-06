"""Tests for the persisted update preference (openflight.update.config)."""

import json

import pytest

from openflight.release import ReleaseInfo
from openflight.update import DEFAULT_REPOSITORY
from openflight.update.config import (
    UpdateConfig,
    default_update_config,
    load_update_config,
    save_update_config,
    validate_channel,
)

STABLE = ReleaseInfo(
    version="0.3.0",
    base_version="0.3.0",
    channel="stable",
    tag="v0.3.0",
    repository="open-flight/openflight",
)
SOURCE = ReleaseInfo(version="0.3.0+abc", base_version="0.3.0", channel="source")


class TestDefaults:
    def test_release_install_follows_its_own_channel_and_repository(self):
        config = default_update_config(STABLE)

        assert config == UpdateConfig(channel="stable", repository="open-flight/openflight")
        assert config.enabled

    def test_source_checkout_stays_off_until_a_channel_is_picked(self):
        config = default_update_config(SOURCE)

        assert config.channel is None
        assert not config.enabled
        assert config.repository == DEFAULT_REPOSITORY

    def test_absent_file_yields_defaults(self, tmp_path):
        assert load_update_config(tmp_path / "update.json", SOURCE) == default_update_config(SOURCE)


class TestRoundTrip:
    def test_save_then_load(self, tmp_path):
        path = tmp_path / "nested" / "update.json"
        save_update_config(UpdateConfig(channel="experimental", repository="me/fork"), path)

        assert load_update_config(path, STABLE) == UpdateConfig("experimental", "me/fork")
        assert json.loads(path.read_text())["format_version"] == 1

    def test_off_is_persisted_as_null(self, tmp_path):
        path = tmp_path / "update.json"
        save_update_config(UpdateConfig(channel=None, repository="me/fork"), path)

        assert json.loads(path.read_text())["channel"] is None
        assert load_update_config(path, STABLE).channel is None

    def test_save_rejects_invalid_channel_and_empty_repository(self, tmp_path):
        with pytest.raises(ValueError):
            save_update_config(UpdateConfig(channel="nightly", repository="a/b"), tmp_path / "u")
        with pytest.raises(ValueError):
            save_update_config(UpdateConfig(channel="stable", repository="  "), tmp_path / "u")
        assert not (tmp_path / "u").exists()


class TestInvalidFiles:
    @pytest.mark.parametrize(
        "text",
        ["{not json", "[]", '{"channel": "nightly", "repository": "a/b"}'],
        ids=["malformed", "list", "unknown-channel"],
    )
    def test_invalid_file_falls_back_to_defaults_with_a_warning(self, tmp_path, caplog, text):
        path = tmp_path / "update.json"
        path.write_text(text)

        with caplog.at_level("WARNING"):
            config = load_update_config(path, STABLE)

        assert config == default_update_config(STABLE)
        assert "Ignoring invalid" in caplog.text

    def test_missing_repository_uses_the_default(self, tmp_path):
        path = tmp_path / "update.json"
        path.write_text('{"channel": "experimental"}')

        assert load_update_config(path, SOURCE) == UpdateConfig("experimental", DEFAULT_REPOSITORY)


def test_validate_channel():
    assert validate_channel(None) is None
    assert validate_channel("stable") == "stable"
    with pytest.raises(ValueError):
        validate_channel("source")
