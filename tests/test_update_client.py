"""Tests for the openflight-update GitHub Releases API client."""

import json

import pytest

from openflight.update import client as cl


class FakeTransport:
    """Records requests and returns queued responses, or raises."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers=None, timeout=30):
        self.calls.append({"method": method, "url": url, "headers": headers or {}})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _resp(status, body=None, headers=None):
    if isinstance(body, (bytes, bytearray)):
        raw = bytes(body)
    else:
        raw = json.dumps(body).encode() if body is not None else b""
    return (status, headers or {}, raw)


def _release_body(**overrides):
    body = {
        "tag_name": "v0.3.0",
        "tarball_url": "https://api.github.com/repos/o/r/tarball/v0.3.0",
        "body": "notes",
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [
            {
                "name": "ui-dist.tar.gz",
                "browser_download_url": "https://example.test/ui-dist.tar.gz",
            }
        ],
    }
    body.update(overrides)
    return body


class TestParseTagVersion:
    def test_parses_v_prefixed(self):
        assert cl.parse_tag_version("v1.2.3") == (1, 2, 3)

    def test_parses_without_v_prefix(self):
        assert cl.parse_tag_version("1.2.3") == (1, 2, 3)

    @pytest.mark.parametrize("tag", ["v1.2", "v1.2.3.4", "v1.2.3-rc1", "not-a-tag", ""])
    def test_rejects_malformed(self, tag):
        with pytest.raises(ValueError):
            cl.parse_tag_version(tag)


class TestIsNewer:
    def test_true_when_current_is_empty(self):
        assert cl.is_newer("v0.1.0", "")

    def test_true_for_strictly_greater(self):
        assert cl.is_newer("v0.10.0", "v0.9.0")  # exercises numeric, not lexical, compare

    def test_false_for_equal(self):
        assert not cl.is_newer("v1.0.0", "v1.0.0")

    def test_false_for_older(self):
        assert not cl.is_newer("v1.0.0", "v1.1.0")


class TestGetLatestRelease:
    def test_parses_release_and_ui_asset(self):
        transport = FakeTransport([_resp(200, _release_body(), {"ETag": '"abc123"'})])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        result = client.get_latest_release()

        assert result.not_modified is False
        assert result.etag == '"abc123"'
        assert result.release.tag == "v0.3.0"
        assert result.release.tarball_url.endswith("/v0.3.0")
        assert result.release.ui_dist_url == "https://example.test/ui-dist.tar.gz"
        assert result.release.notes == "notes"

    def test_sends_if_none_match_when_etag_given(self):
        transport = FakeTransport([_resp(304, headers={"ETag": '"abc123"'})])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        client.get_latest_release(etag='"abc123"')

        assert transport.calls[0]["headers"]["If-None-Match"] == '"abc123"'

    def test_not_modified_returns_no_release(self):
        transport = FakeTransport([_resp(304, headers={"ETag": '"abc123"'})])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        result = client.get_latest_release(etag='"abc123"')

        assert result.not_modified is True
        assert result.release is None
        assert result.etag == '"abc123"'

    def test_missing_ui_asset_leaves_url_none(self):
        body = _release_body(assets=[])
        transport = FakeTransport([_resp(200, body)])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        result = client.get_latest_release()

        assert result.release.ui_dist_url is None

    def test_rate_limit_403_raises(self):
        transport = FakeTransport([_resp(403, headers={"X-RateLimit-Reset": "12345"})])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        with pytest.raises(cl.RateLimited) as exc_info:
            client.get_latest_release()
        assert exc_info.value.reset_at == "12345"

    def test_rate_limit_429_raises(self):
        transport = FakeTransport([_resp(429)])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        with pytest.raises(cl.RateLimited):
            client.get_latest_release()

    def test_server_error_raises_schema_error(self):
        transport = FakeTransport([_resp(500, b"boom")])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        with pytest.raises(cl.ReleaseSchemaError):
            client.get_latest_release()

    def test_malformed_json_raises_schema_error(self):
        transport = FakeTransport([_resp(200, b"not json")])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        with pytest.raises(cl.ReleaseSchemaError):
            client.get_latest_release()

    def test_json_array_instead_of_object_raises_schema_error(self):
        transport = FakeTransport([_resp(200, b"[1, 2, 3]")])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        with pytest.raises(cl.ReleaseSchemaError):
            client.get_latest_release()

    def test_missing_required_field_raises_schema_error(self):
        body = _release_body()
        del body["tarball_url"]
        transport = FakeTransport([_resp(200, body)])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        with pytest.raises(cl.ReleaseSchemaError):
            client.get_latest_release()

    def test_network_error_propagates(self):
        transport = FakeTransport([cl.UpdateNetworkError("offline")])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        with pytest.raises(cl.UpdateNetworkError):
            client.get_latest_release()


class TestDownload:
    def test_returns_body_on_200(self):
        transport = FakeTransport([_resp(200, b"the-bytes")])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        assert client.download("https://example.test/asset") == b"the-bytes"

    def test_non_200_raises_network_error(self):
        transport = FakeTransport([_resp(404, b"")])
        client = cl.GitHubReleaseClient("o/r", request_fn=transport)

        with pytest.raises(cl.UpdateNetworkError):
            client.download("https://example.test/asset")
