"""Stream types for tap-onspd."""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import sys
from typing import TYPE_CHECKING, Any

from singer_sdk import Stream
from singer_sdk import typing as th

from tap_onspd.columns import column_type, convert_value, normalize_columns
from tap_onspd.release import ONSPDError, find_member

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    import zipfile
    from collections.abc import Iterable, Iterator

    from singer_sdk import Tap
    from singer_sdk.helpers.types import Context

    from tap_onspd.release import ONSPDRelease

REPLICATION_KEY = "_onspd_modified_at"

_COUNTRY_COLUMN = re.compile(r"^ctry(\d{2})?cd$")


def _read_csv(zf: zipfile.ZipFile, member: str) -> Iterator[list[str]]:
    with zf.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
        yield from csv.reader(text)


class ZipCSVStream(Stream):
    """A stream read from one CSV file inside the ONSPD release zip.

    Every record carries the release it came from. ``_onspd_modified_at`` (the
    ArcGIS item's last-modified time) is the replication key: if the bookmark
    already holds the current release, the stream emits nothing and the zip is
    never downloaded.
    """

    is_sorted = True

    def __init__(
        self,
        tap: Tap,
        *,
        name: str,
        release: ONSPDRelease,
        member_pattern: str,
        primary_key: str | None = None,
    ) -> None:
        """Initialise the stream from the CSV header of its zip member.

        Args:
            tap: The parent tap.
            name: Stream name.
            release: The ONSPD release to read from.
            member_pattern: Regex matching this stream's file in the zip.
            primary_key: Primary key property; defaults to the first column.
        """
        self._release = release
        self._member = find_member(release.remote_zip.namelist(), member_pattern)
        header = next(_read_csv(release.remote_zip, self._member))
        self._header = header
        self._raw_names = [cell.strip().lower() for cell in header]
        self._names = normalize_columns(header, strip_vintage=tap.config.get("strip_vintage_suffixes", True))

        properties = [
            th.Property(name, column_type(raw))
            for raw, name in zip(self._raw_names, self._names, strict=True)
            if name is not None
        ]
        properties += [
            th.Property("_onspd_release", th.StringType(), description="Release label, e.g. AUG_2026"),
            th.Property("_onspd_item_id", th.StringType(), description="ArcGIS item id of the release"),
            th.Property(REPLICATION_KEY, th.DateTimeType(), description="Last-modified time of the release"),
        ]
        super().__init__(tap, schema=th.PropertiesList(*properties).to_dict(), name=name)

        self.primary_keys = [primary_key or next(name for name in self._names if name)]
        self.replication_key = REPLICATION_KEY
        self.logger.info("Stream %s reads %s", name, self._member)

    def keep_row(self, row: list[str]) -> bool:  # noqa: ARG002
        """Whether a data row should be emitted.

        Args:
            row: The raw CSV row.

        Returns:
            True to emit the row.
        """
        return True

    @override
    def get_records(self, context: Context | None) -> Iterable[dict[str, Any]]:
        info = self._release.info
        bookmark = self.get_starting_replication_key_value(context)
        if bookmark and not self.config.get("force_reload", False):
            loaded = dt.datetime.fromisoformat(str(bookmark).replace("Z", "+00:00"))
            if loaded >= info.modified_at:
                self.logger.info("Release %s already loaded; skipping %s", info.label, self.name)
                return

        release_columns = {
            "_onspd_release": info.label,
            "_onspd_item_id": info.item_id,
            REPLICATION_KEY: info.modified_at.isoformat(),
        }
        pk_index = self._names.index(self.primary_keys[0])

        rows = _read_csv(self._release.local_zip(), self._member)
        if next(rows) != self._header:
            msg = f"{self._member} header changed between discovery and sync"
            raise ONSPDError(msg)

        for row in rows:
            if not row or not row[pk_index].strip() or not self.keep_row(row):
                continue
            record = {
                name: convert_value(raw, value)
                for raw, name, value in zip(self._raw_names, self._names, row, strict=False)
                if name is not None
            }
            record.update(release_columns)
            yield record


class PostcodesStream(ZipCSVStream):
    """The main ONSPD file: one row per UK postcode, live and terminated."""

    def __init__(self, tap: Tap, *, release: ONSPDRelease, member_pattern: str, exclude_countries: list[str]) -> None:
        """Initialise.

        Args:
            tap: The parent tap.
            release: The ONSPD release to read from.
            member_pattern: Regex matching the main CSV in the zip.
            exclude_countries: Country codes whose postcodes are dropped.

        Raises:
            ONSPDError: If countries are excluded but there is no country column.
        """
        super().__init__(tap, name="postcodes", release=release, member_pattern=member_pattern, primary_key="pcds")
        self._exclude_countries = set(exclude_countries)
        self._country_index: int | None = None
        if self._exclude_countries:
            matches = [i for i, raw in enumerate(self._raw_names) if _COUNTRY_COLUMN.match(raw)]
            if not matches:
                msg = "exclude_countries is set but the ONSPD file has no country code column"
                raise ONSPDError(msg)
            self._country_index = matches[0]

    @override
    def keep_row(self, row: list[str]) -> bool:
        return self._country_index is None or row[self._country_index] not in self._exclude_countries
