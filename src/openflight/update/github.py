"""Find and download releases on GitHub.

Reuses the cloud uploader's stdlib transport so tests inject fake responses
and the Pi gains no HTTP dependency. Downloads stream to a ``.part`` file
while the digest is computed, so a tarball is never held in memory.
"""

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .. import __version__
from ..cloud.client import CloudNetworkError, HttpResponse, urllib_request
from .version import ReleaseVersion, parse_tag

GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT = 30
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
EXPERIMENTAL_PAGE_SIZE = 30
TOKEN_ENV = "OPENFLIGHT_GITHUB_TOKEN"


@dataclass(frozen=True)
class ReleaseAsset:
    """One downloadable file attached to a release."""

    name: str
    url: str
    size: int


@dataclass(frozen=True)
class RemoteRelease:
    """A release as listed by GitHub."""

    tag: str
    version: ReleaseVersion
    prerelease: bool
    assets: Tuple[ReleaseAsset, ...]

    def asset(self, name: str) -> Optional[ReleaseAsset]:
        return next((asset for asset in self.assets if asset.name == name), None)


class ReleaseLookupError(Exception):
    """The lookup failed; ``kind`` is a short machine-readable reason."""

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind


class DownloadError(Exception):
    """The asset could not be fetched intact."""


def _parse_release(data: dict) -> Optional[RemoteRelease]:
    tag = data.get("tag_name")
    version = parse_tag(tag) if isinstance(tag, str) else None
    if version is None:
        return None
    assets: List[ReleaseAsset] = []
    for item in data.get("assets") or []:
        name = item.get("name")
        url = item.get("browser_download_url")
        size = item.get("size")
        if isinstance(name, str) and isinstance(url, str) and isinstance(size, int):
            assets.append(ReleaseAsset(name=name, url=url, size=size))
    return RemoteRelease(
        tag=tag,
        version=version,
        prerelease=bool(data.get("prerelease")),
        assets=tuple(assets),
    )


class GitHubReleases:
    """Read-only view of one repository's releases."""

    def __init__(
        self,
        repository: str,
        *,
        request_fn: Callable[..., HttpResponse] = urllib_request,
        open_url_fn: Callable[..., object] = urllib.request.urlopen,
        timeout: int = DEFAULT_TIMEOUT,
        token: Optional[str] = None,
    ):
        self.repository = repository.strip("/")
        self._request = request_fn
        self._open_url = open_url_fn
        self._timeout = timeout
        self._token = token if token is not None else os.environ.get(TOKEN_ENV) or None

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": f"openflight-update/{__version__}",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get_json(self, path: str):
        url = f"{GITHUB_API}/repos/{self.repository}{path}"
        try:
            response = self._request("GET", url, headers=self._headers(), timeout=self._timeout)
        except CloudNetworkError as error:
            raise ReleaseLookupError("offline", str(error)) from error
        if response.status == 404:
            return None
        if response.status in (403, 429):
            remaining = _header(response.headers, "X-RateLimit-Remaining")
            if response.status == 429 or remaining == "0":
                raise ReleaseLookupError("rate_limited", f"HTTP {response.status} from GitHub")
        if response.status != 200:
            raise ReleaseLookupError("http", f"HTTP {response.status} for {url}")
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ReleaseLookupError("invalid", f"unreadable response for {url}") from error

    def latest(self, channel: str) -> Optional[RemoteRelease]:
        """Newest release on ``channel``, or None when the repository has none."""
        if channel == "stable":
            data = self._get_json("/releases/latest")
            if data is None:
                return None
            if not isinstance(data, dict):
                raise ReleaseLookupError("invalid", "releases/latest is not an object")
            release = _parse_release(data)
            if release is None:
                raise ReleaseLookupError("invalid", f"latest release tag {data.get('tag_name')!r}")
            return release
        if channel != "experimental":
            raise ValueError(f"unknown channel {channel!r}")
        data = self._get_json(f"/releases?per_page={EXPERIMENTAL_PAGE_SIZE}")
        if data is None:
            return None
        if not isinstance(data, list):
            raise ReleaseLookupError("invalid", "releases listing is not a list")
        candidates = []
        for item in data:
            if not isinstance(item, dict) or item.get("draft") or not item.get("prerelease"):
                continue
            release = _parse_release(item)
            if release is not None and release.version.channel == "experimental":
                candidates.append(release)
        if not candidates:
            return None
        return max(candidates, key=lambda release: release.version.sort_key)

    def download(self, asset: ReleaseAsset, dest: Path) -> str:
        """Stream ``asset`` to ``dest`` and return its sha256 hex digest."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        digest = hashlib.sha256()
        received = 0
        request = urllib.request.Request(asset.url, headers=self._headers())
        try:
            with self._open_url(request, timeout=self._timeout) as response, part.open("wb") as out:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
                    received += len(chunk)
                    out.write(chunk)
        except OSError as error:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{asset.name}: {error}") from error
        if received != asset.size:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{asset.name}: received {received} bytes, expected {asset.size}")
        os.replace(part, dest)
        return digest.hexdigest()


def _header(headers: Dict[str, str], name: str) -> Optional[str]:
    wanted = name.lower()
    for key, value in (headers or {}).items():
        if key.lower() == wanted:
            return value
    return None
