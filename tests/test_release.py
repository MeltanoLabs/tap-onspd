# Copyright (c) 2026 Meltano.

"""Tests for release discovery and download."""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import TYPE_CHECKING

import pytest
import responses

from tap_onspd.release import ONSPDError, ONSPDRelease, find_member
from tests.conftest import API_URL, ITEM_ID, ZIP_NAME

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import FakeArcGIS

LOGGER = logging.getLogger("test")


def make_release(tmp_path: Path) -> ONSPDRelease:
    return ONSPDRelease(api_url=API_URL, download_dir=tmp_path / "dl", logger=LOGGER)


def test_find_member_prefers_latest_as_at_date() -> None:
    names = [
        "Documents/IMD lookup EN as at 12_19.csv",
        "Documents/IMD lookup EN as at 12_25.csv",
        "Documents/IMD lookup WA as at 12_25.csv",
    ]
    assert find_member(names, r"^Documents/IMD lookup EN .*\.csv$") == "Documents/IMD lookup EN as at 12_25.csv"


def test_find_member_lists_files_when_nothing_matches() -> None:
    with pytest.raises(ONSPDError, match=re.escape("Data/ONSPD_AUG_2026_UK.txt")):
        find_member(["Data/ONSPD_AUG_2026_UK.txt"], r"\.csv$")


def test_resolves_latest_release(arcgis: FakeArcGIS, tmp_path: Path) -> None:  # noqa: ARG001
    info = make_release(tmp_path).info
    assert info.item_id == ITEM_ID
    assert info.label == "AUG_2026"
    assert info.modified_at == dt.datetime(2026, 9, 7, 10, 9, 27, tzinfo=dt.timezone.utc)


def test_arcgis_error_payload_raises(tmp_path: Path) -> None:
    with responses.RequestsMock() as mock:
        mock.add(responses.GET, f"{API_URL}/search", json={"error": {"code": 400, "message": "bad query"}})
        with pytest.raises(ONSPDError, match="bad query"):
            _ = make_release(tmp_path).info


def test_remote_zip_reads_headers_with_range_requests(arcgis: FakeArcGIS, tmp_path: Path) -> None:
    release = make_release(tmp_path)
    assert "Data/ONSPD_AUG_2026_UK.csv" in release.remote_zip.namelist()
    assert arcgis.range_requests
    assert arcgis.full_downloads == 0
    assert not release.local_path.exists()


def test_remote_zip_falls_back_to_download(arcgis: FakeArcGIS, tmp_path: Path) -> None:
    arcgis.supports_ranges = False
    release = make_release(tmp_path)
    assert "Data/ONSPD_AUG_2026_UK.csv" in release.remote_zip.namelist()
    assert arcgis.full_downloads == 1


def test_download_saves_zip_and_reuses_cache(arcgis: FakeArcGIS, tmp_path: Path) -> None:
    release = make_release(tmp_path)
    path = release.download()
    assert path == tmp_path / "dl" / ZIP_NAME
    assert path.read_bytes() == arcgis.body
    assert not path.with_name(f"{ZIP_NAME}.part").exists()

    make_release(tmp_path).download()
    assert arcgis.full_downloads == 1


def test_download_again_when_release_is_republished(arcgis: FakeArcGIS, tmp_path: Path) -> None:
    make_release(tmp_path).download()
    arcgis.modified_ms += 1000

    make_release(tmp_path).download()

    assert arcgis.full_downloads == 2


def test_download_replaces_stale_cache(arcgis: FakeArcGIS, tmp_path: Path) -> None:
    stale = tmp_path / "dl" / ZIP_NAME
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"older copy of the release")
    make_release(tmp_path).download()
    assert stale.read_bytes() == arcgis.body


def test_download_resumes_partial_file(arcgis: FakeArcGIS, tmp_path: Path) -> None:
    part = tmp_path / "dl" / f"{ZIP_NAME}.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(arcgis.body[:100])

    path = make_release(tmp_path).download()

    assert path.read_bytes() == arcgis.body
    assert arcgis.range_requests == ["bytes=100-"]
    assert arcgis.full_downloads == 0
