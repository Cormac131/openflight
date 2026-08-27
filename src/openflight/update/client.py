"""GitHub Releases API client for the auto-updater.

Uses the standard library (``urllib``) — no third-party HTTP dependency on the
Pi, matching ``cloud/client.py``. The low-level transport is injectable
(``request_fn``) so tests run without the network.

Only the public, unauthenticated REST API is used
(``GET /repos/{repo}/releases/latest``); this repo is public and the polling
cadence is far below the unauthenticated rate limit. ETag conditional
requests make repeat "no update" checks essentially free.
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT = 30

UI_DIST_ASSET_NAME = "ui-dist.tar.gz"

HttpResponse = Tuple[int, Dict[str, str], bytes]

_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class UpdateNetworkError(Exception):
    """Raised when the request never reached GitHub (DNS, TLS, offline)."""


class RateLimited(Exception):
    """Raised when GitHub's API rate limit has been exhausted."""

    def __init__(self, reset_at: Optional[str] = None):
        super().__init__(f"GitHub API rate limited (reset={reset_at})")
        self.reset_at = reset_at


class ReleaseSchemaError(Exception):
    """Raised when the release response isn't the JSON shape we expect."""


@dataclass
class ReleaseInfo:
    """The subset of a GitHub Release we care about."""

    tag: str
    tarball_url: str
    notes: str
    published_at: str
    ui_dist_url: Optional[str] = None


@dataclass
class CheckResult:
    """Outcome of a release check: the release (if any) plus the fresh ETag."""

    release: Optional[ReleaseInfo]
    etag: Optional[str]
    not_modified: bool = False


def parse_tag_version(tag: str) -> Tuple[int, int, int]:
    """Parse a strict ``vX.Y.Z`` (or ``X.Y.Z``) tag into a comparable tuple.

    Raises ValueError on anything else (pre-release suffixes, extra
    components, non-numeric parts) — release tags are produced by our own
    release workflow, which enforces this exact shape, so a mismatch here
    means something unexpected happened and should not be silently coerced.
    """
    match = _TAG_RE.match(tag.strip())
    if not match:
        raise ValueError(f"not a vX.Y.Z tag: {tag!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def is_newer(candidate_tag: str, current_tag: str) -> bool:
    """True if ``candidate_tag`` is a newer version than ``current_tag``.

    An empty ``current_tag`` (nothing installed/tracked yet) is always older.
    """
    if not current_tag:
        return True
    return parse_tag_version(candidate_tag) > parse_tag_version(current_tag)


def _header(headers: Dict[str, str], name: str) -> Optional[str]:
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    return lowered.get(name.lower())


def urllib_request(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> HttpResponse:
    """Default transport. Returns (status, headers, body) for any HTTP status
    (including 304/4xx/5xx); raises UpdateNetworkError only when GitHub was
    unreachable."""
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        # HTTP error responses (304, 403, 404, 5xx) are valid outcomes, not
        # transport failures.
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read()
    except urllib.error.URLError as exc:
        raise UpdateNetworkError(str(exc.reason)) from exc
    except (TimeoutError, OSError) as exc:
        raise UpdateNetworkError(str(exc)) from exc


class GitHubReleaseClient:
    """Thin client over ``GET /repos/{repo}/releases/latest``."""

    def __init__(
        self,
        repo: str,
        request_fn: Callable[..., HttpResponse] = urllib_request,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.repo = repo
        self._request = request_fn
        self.timeout = timeout

    def get_latest_release(self, etag: Optional[str] = None) -> CheckResult:
        """Fetch the latest non-draft, non-prerelease release.

        Passing the previously-seen ``etag`` lets GitHub answer 304 Not
        Modified — cheap for both sides and doesn't count against the rate
        limit the way a full 200 response does.
        """
        headers = {"Accept": "application/vnd.github+json"}
        if etag:
            headers["If-None-Match"] = etag

        status, resp_headers, body = self._request(
            "GET",
            f"{API_BASE}/repos/{self.repo}/releases/latest",
            headers=headers,
            timeout=self.timeout,
        )
        fresh_etag = _header(resp_headers, "ETag")

        if status == 304:
            return CheckResult(release=None, etag=fresh_etag or etag, not_modified=True)

        if status in (403, 429):
            reset_at = _header(resp_headers, "X-RateLimit-Reset")
            raise RateLimited(reset_at)

        if status != 200:
            raise ReleaseSchemaError(f"unexpected status {status} from GitHub releases API")

        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise ReleaseSchemaError(f"malformed release JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ReleaseSchemaError("release response was not a JSON object")

        try:
            tag = data["tag_name"]
            tarball_url = data["tarball_url"]
        except KeyError as exc:
            raise ReleaseSchemaError(f"release JSON missing required field: {exc}") from exc

        ui_dist_url = None
        for asset in data.get("assets") or []:
            if isinstance(asset, dict) and asset.get("name") == UI_DIST_ASSET_NAME:
                ui_dist_url = asset.get("browser_download_url")
                break

        release = ReleaseInfo(
            tag=tag,
            tarball_url=tarball_url,
            notes=data.get("body") or "",
            published_at=data.get("published_at") or "",
            ui_dist_url=ui_dist_url,
        )
        return CheckResult(release=release, etag=fresh_etag)

    def download(self, url: str) -> bytes:
        """Download a release asset/tarball. Raises UpdateNetworkError on
        transport failure; caller is responsible for validating the result."""
        status, _headers, body = self._request(
            "GET", url, headers={"Accept": "application/octet-stream"}, timeout=self.timeout
        )
        if status != 200:
            raise UpdateNetworkError(f"download failed with status {status}: {url}")
        return body
