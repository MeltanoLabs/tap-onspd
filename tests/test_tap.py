"""End-to-end tests of discovery and sync against the mocked release."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from tap_onspd.tap import TapONSPD
from tests.conftest import ITEM_ID, ITEM_NAME

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from tests.conftest import FakeArcGIS

MODIFIED_AT = "2026-09-07T10:09:27+00:00"


def run_sync(config: dict, capsys: pytest.CaptureFixture[str], state: dict | None = None) -> list[dict]:
    capsys.readouterr()
    TapONSPD(config=config, state=state, parse_env_config=False).sync_all()
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def records(messages: list[dict], stream: str) -> list[dict]:
    return [m["record"] for m in messages if m["type"] == "RECORD" and m["stream"] == stream]


def test_discovery_does_not_download(arcgis: FakeArcGIS, config: dict) -> None:
    catalog = TapONSPD(config=config, parse_env_config=False).catalog_dict
    streams = {s["tap_stream_id"]: s for s in catalog["streams"]}

    assert set(streams) == {"postcodes", "regions", "wards"}
    postcodes = streams["postcodes"]
    assert postcodes["key_properties"] == ["pcds"]
    assert postcodes["replication_key"] == "_onspd_modified_at"
    props = postcodes["schema"]["properties"]
    assert {"lad_cd", "rgn_cd", "ctry_cd", "oa11cd", "oa21cd", "imd25ind", "lep_cd1"} <= set(props)
    assert props["lat"]["type"] == ["number", "null"]
    assert streams["regions"]["key_properties"] == ["rgn_cd"]
    assert set(streams["regions"]["schema"]["properties"]) >= {"rgn_cd", "rgn_nm", "rgn_nmw"}

    assert arcgis.full_downloads == 0
    assert not (Path(config["download_dir"]) / ITEM_NAME).exists()


def test_sync_emits_records_and_cleans_up(
    arcgis: FakeArcGIS,
    config: dict,
    capsys: pytest.CaptureFixture[str],
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
    zip_path = Path(config["download_dir"]) / ITEM_NAME
    assert zip_path.exists()
    assert len(exit_hooks) == 1  # deleted when the tap process exits
    exit_hooks[0]()
    assert not zip_path.exists()


def test_sync_skips_release_already_loaded(
    arcgis: FakeArcGIS, config: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    bookmark = {"replication_key": "_onspd_modified_at", "replication_key_value": MODIFIED_AT}
    state = {"bookmarks": dict.fromkeys(("postcodes", "regions", "wards"), bookmark)}

    messages = run_sync(config, capsys, state=state)

    assert not [m for m in messages if m["type"] == "RECORD"]
    assert arcgis.full_downloads == 0


def test_force_reload_ignores_bookmark(arcgis: FakeArcGIS, config: dict, capsys: pytest.CaptureFixture[str]) -> None:
    bookmark = {"replication_key": "_onspd_modified_at", "replication_key_value": MODIFIED_AT}
    state = {"bookmarks": {"postcodes": bookmark}}

    messages = run_sync({**config, "force_reload": True}, capsys, state=state)

    assert len(records(messages, "postcodes")) == 3
    assert arcgis.full_downloads == 1


def test_keep_download_and_no_country_filter(
    arcgis: FakeArcGIS,  # noqa: ARG001
    config: dict,
    capsys: pytest.CaptureFixture[str],
    exit_hooks: list[Callable[[], None]],
) -> None:
    messages = run_sync({**config, "keep_download": True, "exclude_countries": []}, capsys)

    assert len(records(messages, "postcodes")) == 6
    assert (Path(config["download_dir"]) / ITEM_NAME).exists()
    assert not exit_hooks


def test_vintage_suffixes_kept_when_disabled(arcgis: FakeArcGIS, config: dict) -> None:  # noqa: ARG001
    catalog = TapONSPD(config={**config, "strip_vintage_suffixes": False}, parse_env_config=False).catalog_dict
    postcodes = next(s for s in catalog["streams"] if s["tap_stream_id"] == "postcodes")
    assert "lad26cd" in postcodes["schema"]["properties"]
