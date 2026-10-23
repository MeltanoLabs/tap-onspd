"""ONSPD tap class."""

from __future__ import annotations

import sys
import tempfile
from functools import cached_property
from pathlib import Path

from singer_sdk import Tap
from singer_sdk import typing as th

from tap_onspd.release import ONSPDRelease
from tap_onspd.streams import PostcodesStream, ZipCSVStream

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

DEFAULT_API_URL = "https://www.arcgis.com/sharing/rest"
POSTCODES_PATTERN = r"^Data/ONSPD_[A-Z]{3}_\d{4}_UK\.csv$"
LOOKUPS = {
    "countries": r"^Documents/CTRY .*\.csv$",
    "regions": r"^Documents/RGN .*\.csv$",
    "counties": r"^Documents/CTY .*\.csv$",
    "local_authorities": r"^Documents/LAD .*\.csv$",
    "wards": r"^Documents/WD .*\.csv$",
    "constituencies": r"^Documents/PCON .*\.csv$",
}
NORTHERN_IRELAND = "N92000002"
CHANNEL_ISLANDS = "L93000001"
ISLE_OF_MAN = "M83000003"
# Northern Ireland postcodes are licensed separately (LPS) and restrict commercial use.
# The Channel Islands and the Isle of Man are Crown Dependencies, not part of the UK.
DEFAULT_EXCLUDE_COUNTRIES = [NORTHERN_IRELAND, CHANNEL_ISLANDS, ISLE_OF_MAN]


class TapONSPD(Tap):
    """Singer tap for the ONS Postcode Directory."""

    name = "tap-onspd"

    config_jsonschema = th.PropertiesList(
        th.Property(
            "download_dir",
            th.StringType(),
            title="Download directory",
            description=(
                "Directory the release zip (~250 MB) is downloaded to during a sync. "
                "Defaults to `tap-onspd` in the system temp directory."
            ),
        ),
        th.Property(
            "keep_download",
            th.BooleanType(),
            default=False,
            title="Keep download",
            description="Keep the zip after the tap exits, so later runs can reuse it.",
        ),
        th.Property(
            "exclude_countries",
            th.ArrayType(th.StringType()),
            default=DEFAULT_EXCLUDE_COUNTRIES,
            title="Exclude countries",
            description=(
                "Country codes whose postcodes are left out. Defaults to Northern Ireland "
                "(licensed separately by LPS, restricting commercial use), the Channel Islands "
                "and the Isle of Man (Crown Dependencies). Set to [] to load every postcode."
            ),
        ),
        th.Property(
            "strip_vintage_suffixes",
            th.BooleanType(),
            default=True,
            title="Strip vintage suffixes",
            description=(
                "Drop the geography year from column names (`lad26cd` -> `lad_cd`) so they stay "
                "stable across releases. Columns present in several vintages keep their names."
            ),
        ),
    ).to_dict()

    @cached_property
    def release(self) -> ONSPDRelease:
        """The ONSPD release this run loads."""
        download_dir = self.config.get("download_dir") or Path(tempfile.gettempdir()) / "tap-onspd"
        return ONSPDRelease(
            api_url=DEFAULT_API_URL,
            download_dir=Path(download_dir).expanduser(),
            logger=self.logger,
            delete_on_exit=not self.config.get("keep_download", False),
        )

    @override
    def discover_streams(self) -> list[ZipCSVStream]:
        streams: list[ZipCSVStream] = [
            PostcodesStream(
                self,
                release=self.release,
                member_pattern=POSTCODES_PATTERN,
                exclude_countries=self.config.get("exclude_countries", DEFAULT_EXCLUDE_COUNTRIES),
            )
        ]
        streams += [
            ZipCSVStream(self, name=name, release=self.release, member_pattern=pattern)
            for name, pattern in LOOKUPS.items()
        ]
        return streams


if __name__ == "__main__":
    TapONSPD.cli()
