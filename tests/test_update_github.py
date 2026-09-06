"""Tests for the GitHub Releases lookup and download (openflight.update.github)."""

import hashlib
import io
import json
from contextlib import contextmanager

import pytest

from openflight.cloud.client import CloudNetworkError, HttpResponse
from openflight.update import github as gh


class FakeTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, data=None, headers=None, timeout=30):
        self.calls.append({"method": method, "url": url, "headers": headers or {}})
        return self._responses.pop(0)


def _resp(status, body=None, headers=None):
    raw = json.dumps(body).encode() if body is not None else b""
    return HttpResponse(status=status, headers=headers or {}, body=raw)


def _release(tag, *, prerelease=False, draft=False, assets=None):
    if assets is None:
        assets = [
            {
                "name": f"openflight-{tag}.tar.gz",
                "browser_download_url": f"https://d/{tag}.tar.gz",
                "size": 10,
            },
            {
                "name": f"openflight-{tag}.tar.gz.sha256",
                "browser_download_url": f"https://d/{tag}.sha",
                "size": 97,
            },
        ]
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "assets": assets}


def _client(responses, **kwargs):
    transport = FakeTransport(responses)
    return gh.GitHubReleases(
        "open-flight/openflight", request_fn=transport, token=None, **kwargs
    ), transport


class TestLatestStable:
    def test_returns_the_latest_release_with_its_assets(self):
        client, transport = _client([_resp(200, _release("v0.3.0"))])

        release = client.latest("stable")

        assert release.tag == "v0.3.0"
        assert str(release.version) == "0.3.0"
        assert release.asset("openflight-v0.3.0.tar.gz").size == 10
        assert release.asset("missing") is None
        assert (
            transport.calls[0]["url"]
            == "https://api.github.com/repos/open-flight/openflight/releases/latest"
        )

    def test_sends_github_headers_and_optional_token(self, monkeypatch):
        monkeypatch.delenv(gh.TOKEN_ENV, raising=False)
        client, transport = _client([_resp(200, _release("v0.3.0"))])
        client.latest("stable")
        headers = transport.calls[0]["headers"]
        assert headers["Accept"] == "application/vnd.github+json"
        assert headers["X-GitHub-Api-Version"] == gh.API_VERSION
        assert headers["User-Agent"].startswith("openflight-update/")
        assert "Authorization" not in headers

        monkeypatch.setenv(gh.TOKEN_ENV, "ghp_secret")
        transport2 = FakeTransport([_resp(200, _release("v0.3.0"))])
        gh.GitHubReleases("o/r", request_fn=transport2).latest("stable")
        assert transport2.calls[0]["headers"]["Authorization"] == "Bearer ghp_secret"

    def test_no_release_yet_returns_none(self):
        client, _ = _client([_resp(404)])
        assert client.latest("stable") is None

    @pytest.mark.parametrize(
        ("response", "kind"),
        [
            (_resp(403, headers={"X-RateLimit-Remaining": "0"}), "rate_limited"),
            (_resp(429), "rate_limited"),
            (_resp(500), "http"),
            (HttpResponse(200, {}, b"{not json"), "invalid"),
            (_resp(200, [1, 2]), "invalid"),
            (_resp(200, _release("nightly-1")), "invalid"),
        ],
    )
    def test_failures_carry_a_kind(self, response, kind):
        client, _ = _client([response])
        with pytest.raises(gh.ReleaseLookupError) as info:
            client.latest("stable")
        assert info.value.kind == kind

    def test_403_without_rate_limit_header_is_a_plain_http_error(self):
        client, _ = _client([_resp(403, headers={"X-RateLimit-Remaining": "12"})])
        with pytest.raises(gh.ReleaseLookupError) as info:
            client.latest("stable")
        assert info.value.kind == "http"

    def test_offline_transport_is_reported_as_offline(self):
        def boom(*_a, **_k):
            raise CloudNetworkError("no route")

        client = gh.GitHubReleases("o/r", request_fn=boom, token=None)
        with pytest.raises(gh.ReleaseLookupError) as info:
            client.latest("stable")
        assert info.value.kind == "offline"


class TestLatestExperimental:
    def test_picks_the_highest_dev_prerelease_and_ignores_the_rest(self):
        listing = [
            _release("v0.3.0-dev.7", prerelease=True),
            _release("v0.3.0-dev.12", prerelease=True, draft=True),
            _release("v0.3.0-dev.9", prerelease=True),
            _release("v0.3.0"),
            _release("v0.4.0-rc.1", prerelease=True),
            {"tag_name": 5, "prerelease": True, "assets": []},
        ]
        client, transport = _client([_resp(200, listing)])

        release = client.latest("experimental")

        assert release.tag == "v0.3.0-dev.9"
        assert "releases?per_page=" in transport.calls[0]["url"]

    def test_no_prereleases_returns_none(self):
        client, _ = _client([_resp(200, [_release("v0.3.0")])])
        assert client.latest("experimental") is None

    def test_listing_must_be_a_list(self):
        client, _ = _client([_resp(200, {"tag_name": "v1"})])
        with pytest.raises(gh.ReleaseLookupError):
            client.latest("experimental")

    def test_unknown_channel_is_a_programming_error(self):
        client, _ = _client([])
        with pytest.raises(ValueError):
            client.latest("nightly")


class TestDownload:
    @staticmethod
    def _open_url_for(payload: bytes):
        @contextmanager
        def open_url(request, timeout):
            assert request.get_header("User-agent").startswith("openflight-update/")
            yield io.BytesIO(payload)

        return open_url

    def test_streams_to_file_and_returns_the_digest(self, tmp_path):
        payload = b"x" * (gh.DOWNLOAD_CHUNK_BYTES + 5)
        client = gh.GitHubReleases("o/r", open_url_fn=self._open_url_for(payload), token=None)
        asset = gh.ReleaseAsset("a.tar.gz", "https://d/a", len(payload))
        dest = tmp_path / "dl" / "a.tar.gz"

        digest = client.download(asset, dest)

        assert dest.read_bytes() == payload
        assert digest == hashlib.sha256(payload).hexdigest()
        assert not dest.with_name("a.tar.gz.part").exists()

    def test_size_mismatch_removes_the_partial_file(self, tmp_path):
        client = gh.GitHubReleases("o/r", open_url_fn=self._open_url_for(b"short"), token=None)
        asset = gh.ReleaseAsset("a.tar.gz", "https://d/a", 99)

        with pytest.raises(gh.DownloadError, match="expected 99"):
            client.download(asset, tmp_path / "a.tar.gz")

        assert list(tmp_path.iterdir()) == []

    def test_transport_error_removes_the_partial_file(self, tmp_path):
        @contextmanager
        def open_url(request, timeout):
            raise OSError("reset by peer")
            yield  # pylint: disable=unreachable

        client = gh.GitHubReleases("o/r", open_url_fn=open_url, token=None)
        with pytest.raises(gh.DownloadError, match="reset by peer"):
            client.download(gh.ReleaseAsset("a", "https://d/a", 1), tmp_path / "a")
        assert list(tmp_path.iterdir()) == []
