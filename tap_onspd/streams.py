# Copyright (c) 2026 Meltano.

"""Stream types for tap-onspd."""

from __future__ import annotations

import csv
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

COUNTRY_CODES = {
    "england": "E92000001",
    "scotland": "S92000003",
    "wales": "W92000004",
    "northern_ireland": "N92000002",
    "channel_islands": "L93000001",
    "isle_of_man": "M83000003",
}

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
    replication_key = "_onspd_modified_at"

    def __init__(
        self,
        tap: Tap,
        *,
        release: ONSPDRelease,
        member_pattern: str,
        name: str | None = None,
    ) -> None:
        """Initialise the stream from the CSV header of its zip member.

        Args:
            tap: The parent tap.
            release: The ONSPD release to read from.
            member_pattern: Regex matching this stream's file in the zip.
            name: Stream name, if the class does not set one.
        """
        self._release = release
        self._member = find_member(release.remote_zip.namelist(), member_pattern)
        header = next(_read_csv(release.remote_zip, self._member))
        self._header = header
        self._raw_names = [cell.strip().lower() for cell in header]
        self._names = normalize_columns(header, strip_vintage=tap.config["strip_vintage_suffixes"])
        self._raw_by_name = {
            name: raw for raw, name in zip(self._raw_names, self._names, strict=True) if name
        }

        properties = [
            th.Property(name, column_type(raw))
            for raw, name in zip(self._raw_names, self._names, strict=True)
            if name is not None
        ]
        properties += [
            th.Property(
                "_onspd_release", th.StringType(), description="Release label, e.g. AUG_2026"
            ),
            th.Property(
                "_onspd_item_id", th.StringType(), description="ArcGIS item id of the release"
            ),
            th.Property(
                "_onspd_modified_at",
                th.DateTimeType(),
                description="Last-modified time of the release",
            ),
        ]
        super().__init__(tap, schema=th.PropertiesList(*properties).to_dict(), name=name)

        if not self.primary_keys:
            self.primary_keys = (next(name for name in self._names if name),)
        self._key = self.primary_keys[0]
        self.logger.info("Stream %s reads %s", self.name, self._member)

    @override
    def get_records(self, context: Context | None) -> Iterable[dict[str, Any]]:
        info = self._release.info
        loaded = self.get_starting_timestamp(context)
        if loaded and loaded >= info.modified_at:
            self.logger.info("Release %s already loaded; skipping %s", info.label, self.name)
            return

        rows = _read_csv(self._release.local_zip(), self._member)
        if next(rows) != self._header:
            msg = f"{self._member} header changed between discovery and sync"
            raise ONSPDError(msg)

        for row in rows:
            yield {
                name: value
                for name, value in zip(self._names, row, strict=False)
                if name is not None
            }

    @override
    def post_process(
        self, row: dict[str, Any], context: Context | None = None
    ) -> dict[str, Any] | None:
        if not row.get(self._key):
            return None
        for name, value in row.items():
            row[name] = convert_value(self._raw_by_name[name], value)
        info = self._release.info
        row["_onspd_release"] = info.label
        row["_onspd_item_id"] = info.item_id
        row["_onspd_modified_at"] = info.modified_at.isoformat()
        return row


class PostcodesStream(ZipCSVStream):
    """The main ONSPD file: one row per UK postcode, live and terminated."""

    name = "postcodes"
    primary_keys = ("pcds",)
    member_pattern = r"^Data/ONSPD_[A-Z]{3}_\d{4}_UK\.csv$"

    def __init__(self, tap: Tap, *, release: ONSPDRelease) -> None:
        """Initialise.

        Args:
            tap: The parent tap.
            release: The ONSPD release to read from.

        Raises:
            ONSPDError: If countries are excluded but there is no country column.
        """
        super().__init__(tap, release=release, member_pattern=self.member_pattern)
        self._exclude_countries = {
            COUNTRY_CODES[country] for country in self.config["exclude_countries"]
        }
        self._country_column: str | None = None
        if self._exclude_countries:
            matches = [
                name
                for raw, name in zip(self._raw_names, self._names, strict=True)
                if _COUNTRY_COLUMN.match(raw)
            ]
            if not matches:
                msg = "exclude_countries is set but the ONSPD file has no country code column"
                raise ONSPDError(msg)
            self._country_column = matches[0]

    @override
    def post_process(
        self, row: dict[str, Any], context: Context | None = None
    ) -> dict[str, Any] | None:
        if self._country_column and row.get(self._country_column) in self._exclude_countries:
            return None
        return super().post_process(row, context)
