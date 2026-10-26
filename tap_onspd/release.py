# Copyright (c) 2026 Meltano.

"""ONSPD release discovery, download and zip access.

Each ONSPD release is an ArcGIS Online item (owner ``ONSGeography_data``, tag
``PRD_ONSPD``) whose data is a ~250 MB zip. Discovery only needs the CSV
headers, so it reads the zip remotely with HTTP range requests (~1 MB). A sync
downloads the whole zip once to ``download_dir``, keeps it for later runs, and
streams rows out of it without extracting anything to disk.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import io
import re
import sys
import time
import zipfile
from functools import cached_property
from typing import TYPE_CHECKING

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if sys.version_info >= (3, 12):
    from collections.abc import Buffer
    from typing import override
else:
    from typing_extensions import Buffer, override

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterable
    from pathlib import Path

SEARCH_QUERY = 'owner:ONSGeography_data AND tags:PRD_ONSPD AND type:"CSV Collection"'

CHUNK_SIZE = 8 * 1024 * 1024
RANGE_BUFFER_SIZE = 1024 * 1024
MAX_DOWNLOAD_ATTEMPTS = 5

_AS_AT = re.compile(r"as at (\d{2})_(\d{2})")


class ONSPDError(Exception):
    """Raised when the ONSPD release or its files cannot be read."""


class RangeNotSupportedError(ONSPDError):
    """Raised when the server does not honour HTTP range requests."""


@dataclasses.dataclass(frozen=True)
class ReleaseInfo:
    """An ONSPD release, as described by its ArcGIS item."""

    item_id: str
    name: str
    title: str
    modified_at: dt.datetime

    @property
    def label(self) -> str:
        """Release label, e.g. ``AUG_2026`` for ``ONSPD_AUG_2026.zip``."""
        return re.sub(r"^ONSPD_|\.zip$", "", self.name)


def build_session() -> requests.Session:
    """Return a session that retries transient HTTP failures.

    Returns:
        A configured session.
    """
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def find_member(names: Iterable[str], pattern: str) -> str:
    """Return the zip member matching ``pattern``.

    When several match (e.g. two IMD lookups ``as at 12_19`` and ``as at
    12_25``), the one with the latest ``as at MM_YY`` date wins.

    Args:
        names: Zip member names.
        pattern: Regular expression to match against member names.

    Returns:
        The matching member name.

    Raises:
        ONSPDError: If no member matches.
    """
    regex = re.compile(pattern)
    matches = [name for name in names if regex.search(name)]
    if not matches:
        msg = f"No file in the ONSPD zip matches {pattern!r}. Files: {sorted(names)}"
        raise ONSPDError(msg)

    def as_at(name: str) -> tuple[int, int, str]:
        found = _AS_AT.search(name)
        return (int(found[2]), int(found[1]), name) if found else (-1, -1, name)

    return max(matches, key=as_at)


class HttpRangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file using HTTP range requests."""

    def __init__(self, session: requests.Session, url: str, timeout: float) -> None:
        """Probe the remote file.

        Args:
            session: HTTP session.
            url: File URL.
            timeout: Request timeout in seconds.

        Raises:
            RangeNotSupportedError: If the server does not advertise byte ranges.
        """
        super().__init__()
        response = session.head(url, allow_redirects=True, timeout=timeout)
        response.raise_for_status()
        if (
            response.headers.get("Accept-Ranges") != "bytes"
            or "Content-Length" not in response.headers
        ):
            msg = f"{url} does not support range requests"
            raise RangeNotSupportedError(msg)
        self._session = session
        self._url = response.url
        self._timeout = timeout
        self._size = int(response.headers["Content-Length"])
        self._pos = 0

    @override
    def readable(self) -> bool:
        return True

    @override
    def seekable(self) -> bool:
        return True

    @override
    def tell(self) -> int:
        return self._pos

    @override
    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = base + offset
        return self._pos

    @override
    def readinto(self, buffer: Buffer) -> int:
        view = memoryview(buffer)
        if self._pos >= self._size or view.nbytes == 0:
            return 0
        end = min(self._pos + view.nbytes, self._size) - 1
        response = self._session.get(
            self._url,
            headers={"Range": f"bytes={self._pos}-{end}"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        if response.status_code != requests.codes.partial_content:
            msg = f"{self._url} ignored the range request"
            raise RangeNotSupportedError(msg)
        data = response.content
        view[: len(data)] = data
        self._pos += len(data)
        return len(data)


class ONSPDRelease:
    """Resolves an ONSPD release and provides access to its zip."""

    def __init__(
        self,
        *,
        api_url: str,
        download_dir: Path,
        logger: logging.Logger,
        timeout: float = 120,
        session: requests.Session | None = None,
    ) -> None:
        """Initialise.

        Args:
            api_url: ArcGIS sharing REST API base URL.
            download_dir: Directory the zip is downloaded to.
            logger: Logger for progress messages.
            timeout: HTTP read timeout in seconds.
            session: HTTP session (a retrying one is created when omitted).
        """
        self._api_url = api_url.rstrip("/")
        self._download_dir = download_dir
        self._logger = logger
        self._timeout = timeout
        self._session = session or build_session()
        self._local_zip: zipfile.ZipFile | None = None

    @cached_property
    def info(self) -> ReleaseInfo:
        """The newest ONSPD release.

        Raises:
            ONSPDError: If the release cannot be found.
        """
        results = self._get_json(
            f"{self._api_url}/search",
            {"q": SEARCH_QUERY, "sortField": "created", "sortOrder": "desc", "num": 1, "f": "json"},
        ).get("results", [])
        if not results:
            msg = "No ONSPD release found on ArcGIS Online"
            raise ONSPDError(msg)
        item = results[0]

        info = ReleaseInfo(
            item_id=item["id"],
            name=item["name"],
            title=item["title"],
            modified_at=dt.datetime.fromtimestamp(item["modified"] / 1000, tz=dt.timezone.utc),
        )
        self._logger.info(
            "ONSPD release: %s (item %s, modified %s)", info.title, info.item_id, info.modified_at
        )
        return info

    @property
    def download_url(self) -> str:
        """URL of the release zip."""
        return f"{self._api_url}/content/items/{self.info.item_id}/data"

    @property
    def local_path(self) -> Path:
        """Where the release zip is (or will be) saved.

        The name carries the item's modified time, so a release that ONS republishes
        under the same name and size is downloaded again.
        """
        modified_ms = round(self.info.modified_at.timestamp() * 1000)
        return self._download_dir / f"{self.info.name.removesuffix('.zip')}.{modified_ms}.zip"

    @cached_property
    def remote_zip(self) -> zipfile.ZipFile:
        """The release zip, read remotely with range requests (for discovery).

        Falls back to the downloaded zip when the server does not support ranges.
        """
        try:
            raw = HttpRangeFile(self._session, self.download_url, self._timeout)
        except RangeNotSupportedError:
            self._logger.warning(
                "Range requests not supported; downloading the full zip for discovery"
            )
            return self.local_zip()
        return zipfile.ZipFile(io.BufferedReader(raw, buffer_size=RANGE_BUFFER_SIZE))

    def local_zip(self) -> zipfile.ZipFile:
        """Download the release zip (if needed) and open it.

        Returns:
            The open zip file.
        """
        if self._local_zip is None:
            self.download()
            self._local_zip = zipfile.ZipFile(self.local_path)
        return self._local_zip

    def download(self) -> Path:
        """Download the release zip to ``download_dir``, resuming partial downloads.

        The file is streamed in chunks to ``<name>.part`` and renamed once it is
        complete and valid, so a zip under its final name is always usable. An
        existing valid zip is reused.

        Returns:
            Path to the downloaded zip.

        Raises:
            ONSPDError: If the download is incomplete or not a valid zip.
        """
        target = self.local_path
        size = self._remote_size()
        if target.exists() and target.stat().st_size == size and zipfile.is_zipfile(target):
            self._logger.info("Using cached ONSPD zip %s", target)
            return target

        self._download_dir.mkdir(parents=True, exist_ok=True)
        part = target.with_name(f"{target.name}.part")
        self._logger.info("Downloading %s (%.0f MB) to %s", self.info.name, size / 1e6, target)

        for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
            try:
                self._download_to(part, size)
                break
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.exceptions.ChunkedEncodingError,
            ) as exc:
                if attempt == MAX_DOWNLOAD_ATTEMPTS:
                    raise
                wait = min(2**attempt, 60)
                self._logger.warning("Download interrupted (%s); resuming in %ss", exc, wait)
                time.sleep(wait)

        if part.stat().st_size != size or not zipfile.is_zipfile(part):
            msg = f"Downloaded {part} is incomplete or not a valid zip"
            raise ONSPDError(msg)
        part.replace(target)
        self._logger.info("Downloaded %s", target)
        return target

    def _remote_size(self) -> int:
        response = self._session.head(
            self.download_url, allow_redirects=True, timeout=self._timeout
        )
        response.raise_for_status()
        return int(response.headers["Content-Length"])

    def _download_to(self, part: Path, size: int) -> None:
        offset = part.stat().st_size if part.exists() else 0
        if offset >= size:
            return
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with self._session.get(
            self.download_url,
            headers=headers,
            stream=True,
            timeout=(10, self._timeout),
        ) as response:
            response.raise_for_status()
            if offset and response.status_code != requests.codes.partial_content:
                self._logger.warning("Server ignored the resume request; restarting the download")
                offset = 0
            elif offset:
                self._logger.info("Resuming download at %.0f MB", offset / 1e6)

            written = offset
            next_log = 0.1
            with part.open("ab" if offset else "wb") as fh:
                for chunk in response.iter_content(CHUNK_SIZE):
                    fh.write(chunk)
                    written += len(chunk)
                    if written / size >= next_log:
                        self._logger.info("Downloaded %.0f%%", 100 * written / size)
                        next_log += 0.1

    def _get_json(self, url: str, params: dict) -> dict:
        response = self._session.get(url, params=params, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        # ArcGIS reports errors with HTTP 200 and an "error" object.
        if "error" in payload:
            msg = f"ArcGIS error from {url}: {payload['error']}"
            raise ONSPDError(msg)
        return payload
