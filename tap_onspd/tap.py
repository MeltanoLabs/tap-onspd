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
COUNTRY_CODES = {
    "england": "E92000001",
    "scotland": "S92000003",
    "wales": "W92000004",
    "northern_ireland": "N92000002",
    "channel_islands": "L93000001",
    "isle_of_man": "M83000003",
}
# Northern Ireland postcodes are licensed separately (LPS) and restrict commercial use.
# The Channel Islands and the Isle of Man are Crown Dependencies, not part of the UK.
DEFAULT_EXCLUDE_COUNTRIES = ["northern_ireland", "channel_islands", "isle_of_man"]


class TapONSPD(Tap):
    """Singer tap for the ONS Postcode Directory."""

    name = "tap-onspd"

    config_jsonschema = th.PropertiesList(
        th.Property(
            "exclude_countries",
            th.ArrayType(th.StringType(allowed_values=list(COUNTRY_CODES))),
            default=DEFAULT_EXCLUDE_COUNTRIES,
            title="Exclude countries",
            description=(
                "Countries whose postcodes are left out. Defaults to Northern Ireland "
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
        return ONSPDRelease(
            api_url=DEFAULT_API_URL,
            download_dir=Path(tempfile.gettempdir()) / "tap-onspd",
            logger=self.logger,
        )

    @override
    def discover_streams(self) -> list[ZipCSVStream]:
        streams: list[ZipCSVStream] = [
            PostcodesStream(
                self,
                release=self.release,
                member_pattern=POSTCODES_PATTERN,
                exclude_countries=[COUNTRY_CODES[country] for country in self.config["exclude_countries"]],
            )
        ]
        streams += [
            ZipCSVStream(self, name=name, release=self.release, member_pattern=pattern)
            for name, pattern in LOOKUPS.items()
        ]
        return streams


if __name__ == "__main__":
    TapONSPD.cli()
