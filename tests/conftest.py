"""Fixtures: a miniature ONSPD release zip served by a mocked ArcGIS API."""

from __future__ import annotations

import dataclasses
import io
import re
import zipfile
from typing import TYPE_CHECKING

import pytest
import responses

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

API_URL = "https://arcgis.test/sharing/rest"
ITEM_ID = "9e5a92a3cfb14dc7ad43d6ea7a7b8c7f"
ITEM_NAME = "ONSPD_AUG_2026.zip"
MODIFIED_MS = 1788775767000  # 2026-09-07T10:09:27Z
DATA_URL = f"{API_URL}/content/items/{ITEM_ID}/data"

POSTCODES_CSV = (
    "pcd7,pcd8,pcds,doterm,cty26cd,lad26cd,ctry26cd,rgn26cd,lat,long,oa11cd,oa21cd,imd20ind,imd25ind,lep21cd1,lep21cd2\n"
    '"SW1A1AA","SW1A 1AA","SW1A 1AA","","E99999999","E09000033","E92000001","E12000007",'
    '51.501009,-0.141588,"E00023938","E00166756",17694,18002,"E37000051",""\n'
    '"EH1 1YZ","EH1  1YZ","EH1 1YZ","200112","S99999999","S12000036","S92000003","S99999999",'
    '55.952103,-3.188628,"S00000001","S00000002",0,0,"",""\n'
    '"BT1 1AA","BT1  1AA","BT1 1AA","","N99999999","N09000003","N92000002","N99999999",'
    '54.597128,-5.930120,"95AA01S1","N00000001",0,0,"",""\n'
    '"AB1 0AA","AB1  0AA","AB1 0AA","199606","S99999999","S12000033","S92000003","S99999999",'
    '99.999999,0.000000,"","",0,0,"",""\n'
)
REGIONS_CSV = "﻿RGN25CD,RGN25NM,RGN25NMW,,\nE12000007,London,Llundain,,\n,,,,\n"
WARDS_OLD_CSV = "﻿WD25CD,WD25NM\nE05000001,Old Ward\n"
WARDS_NEW_CSV = "﻿WD26CD,WD26NM\nE05000002,New Ward\n"


def build_zip() -> bytes:
    """Return a zip laid out like a real ONSPD release, with tiny files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Data/multi_csv/ONSPD_AUG_2026_UK_SW.csv", POSTCODES_CSV)
        zf.writestr("Data/ONSPD_AUG_2026_UK.csv", POSTCODES_CSV)
        zf.writestr("Data/ONSPD_AUG_2026_UK.txt", POSTCODES_CSV)
        zf.writestr("Documents/RGN Region names and codes EN as at 05_25.csv", REGIONS_CSV)
        zf.writestr("Documents/WD Ward names and codes UK as at 05_25.csv", WARDS_OLD_CSV)
        zf.writestr("Documents/WD Ward names and codes UK as at 05_26.csv", WARDS_NEW_CSV)
        zf.writestr("Documents/RGN Region names and codes EN as at 05_25.xlsx", b"not a csv")
    return buffer.getvalue()


@dataclasses.dataclass
class FakeArcGIS:
    """Records how the release zip was fetched."""

    body: bytes
    full_downloads: int = 0
    range_requests: list[str] = dataclasses.field(default_factory=list)
    supports_ranges: bool = True


ITEM = {"id": ITEM_ID, "name": ITEM_NAME, "title": "ONS Postcode Directory (August 2026)", "modified": MODIFIED_MS}


@pytest.fixture
def arcgis() -> Iterator[FakeArcGIS]:
    """Mock the ArcGIS search, item and data endpoints."""
    fake = FakeArcGIS(body=build_zip())

    def head(_request: object) -> tuple[int, dict, bytes]:
        headers = {"Content-Length": str(len(fake.body))}
        if fake.supports_ranges:
            headers["Accept-Ranges"] = "bytes"
        return 200, headers, b""

    def get(request: object) -> tuple[int, dict, bytes]:
        range_header = request.headers.get("Range")  # type: ignore[attr-defined]
        if range_header and fake.supports_ranges:
            fake.range_requests.append(range_header)
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
            assert match
            start = int(match[1])
            end = int(match[2]) if match[2] else len(fake.body) - 1
            return 206, {}, fake.body[start : end + 1]
        fake.full_downloads += 1
        return 200, {"Content-Length": str(len(fake.body))}, fake.body

    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add(responses.GET, f"{API_URL}/search", json={"results": [ITEM]})
        mock.add(responses.GET, f"{API_URL}/content/items/{ITEM_ID}", json=ITEM)
        mock.add_callback(responses.HEAD, DATA_URL, callback=head)
        mock.add_callback(responses.GET, DATA_URL, callback=get)
        yield fake


@pytest.fixture
def config(tmp_path: Path) -> dict:
    """Tap config pointing at the mocked API and a temp download dir."""
    return {
        "api_url": API_URL,
        "download_dir": str(tmp_path / "downloads"),
        "lookups": {
            "regions": r"^Documents/RGN .*\.csv$",
            "wards": r"^Documents/WD .*\.csv$",
        },
    }
