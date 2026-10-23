"""End-to-end tests of discovery and sync against the mocked release."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from singer_sdk.exceptions import ConfigValidationError

from tap_onspd.tap import TapONSPD
from tests.conftest import ITEM_ID, ZIP_NAME

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.conftest import FakeArcGIS

MODIFIED_AT = "2026-09-07T10:09:27+00:00"
STREAMS = ("postcodes", "countries", "regions", "counties", "local_authorities", "wards", "constituencies")


def run_sync(config: dict, capsys: pytest.CaptureFixture[str], state: dict | None = None) -> list[dict]:
    capsys.readouterr()
    TapONSPD(config=config, state=state, parse_env_config=False).sync_all()
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def records(messages: list[dict], stream: str) -> list[dict]:
    return [m["record"] for m in messages if m["type"] == "RECORD" and m["stream"] == stream]


def test_discovery_does_not_download(arcgis: FakeArcGIS, config: dict, download_dir: Path) -> None:
    catalog = TapONSPD(config=config, parse_env_config=False).catalog_dict
    streams = {s["tap_stream_id"]: s for s in catalog["streams"]}

    assert set(streams) == set(STREAMS)
    postcodes = streams["postcodes"]
    assert postcodes["key_properties"] == ["pcds"]
    assert postcodes["replication_key"] == "_onspd_modified_at"
    props = postcodes["schema"]["properties"]
    assert {"lad_cd", "rgn_cd", "ctry_cd", "oa11cd", "oa21cd", "imd25ind", "lep_cd1"} <= set(props)
    assert props["lat"]["type"] == ["number", "null"]
    assert streams["regions"]["key_properties"] == ["rgn_cd"]
    assert set(streams["regions"]["schema"]["properties"]) >= {"rgn_cd", "rgn_nm", "rgn_nmw"}

    assert arcgis.full_downloads == 0
    assert not (download_dir / ZIP_NAME).exists()


def test_sync_emits_records_and_keeps_zip(
    arcgis: FakeArcGIS,
    config: dict,
    capsys: pytest.CaptureFixture[str],
    download_dir: Path,
    exit_hooks: list[Callable[[], None]],
) -> None:
    messages = run_sync(config, capsys)

    postcodes = {r["pcds"]: r for r in records(messages, "postcodes")}
    assert set(postcodes) == {"SW1A 1AA", "EH1 1YZ", "AB1 0AA"}  # NI, Channel Islands, Isle of Man excluded
    london = postcodes["SW1A 1AA"]
    assert london["lat"] == 51.501009
    assert london["lad_cd"] == "E09000033"
    assert london["imd25ind"] == 18002
    assert london["doterm"] is None
    assert london["lep_cd2"] is None
    assert london["_onspd_release"] == "AUG_2026"
    assert london["_onspd_item_id"] == ITEM_ID
    assert london["_onspd_modified_at"] == MODIFIED_AT

    # blank trailing rows are skipped; the newest ward lookup wins
    assert [r["rgn_nm"] for r in records(messages, "regions")] == ["London"]
    assert [r["wd_nm"] for r in records(messages, "wards")] == ["New Ward"]

    states = [m["value"] for m in messages if m["type"] == "STATE"]
    bookmark = states[-1]["bookmarks"]["postcodes"]
    assert bookmark["replication_key_value"] == MODIFIED_AT

    assert arcgis.full_downloads == 1
    for hook in exit_hooks:  # as when the tap process exits
        hook()
    assert (download_dir / ZIP_NAME).exists()

    run_sync(config, capsys)
    assert arcgis.full_downloads == 1  # the next run reads the kept zip


def test_sync_skips_release_already_loaded(
    arcgis: FakeArcGIS, config: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    bookmark = {"replication_key": "_onspd_modified_at", "replication_key_value": MODIFIED_AT}
    state = {"bookmarks": dict.fromkeys(STREAMS, bookmark)}

    messages = run_sync(config, capsys, state=state)

    assert not [m for m in messages if m["type"] == "RECORD"]
    assert arcgis.full_downloads == 0


def test_no_country_filter(
    arcgis: FakeArcGIS,  # noqa: ARG001
    config: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    messages = run_sync({**config, "exclude_countries": []}, capsys)

    assert len(records(messages, "postcodes")) == 6


def test_exclude_countries_by_name(arcgis: FakeArcGIS, config: dict, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ARG001
    messages = run_sync({**config, "exclude_countries": ["england", "scotland"]}, capsys)

    assert {r["pcds"] for r in records(messages, "postcodes")} == {"BT1 1AA", "JE2 3AB", "IM1 1AA"}


def test_exclude_countries_rejects_codes(config: dict) -> None:
    with pytest.raises(ConfigValidationError):
        TapONSPD(config={**config, "exclude_countries": ["N92000002"]}, parse_env_config=False)


def test_vintage_suffixes_kept_when_disabled(arcgis: FakeArcGIS, config: dict) -> None:  # noqa: ARG001
    catalog = TapONSPD(config={**config, "strip_vintage_suffixes": False}, parse_env_config=False).catalog_dict
    postcodes = next(s for s in catalog["streams"] if s["tap_stream_id"] == "postcodes")
    assert "lad26cd" in postcodes["schema"]["properties"]
