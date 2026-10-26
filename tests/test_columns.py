# Copyright (c) 2026 Meltano.

"""Tests for column naming and typing."""

from __future__ import annotations

from tap_onspd.columns import column_type, convert_value, normalize_columns


def test_strips_vintage_from_unique_columns() -> None:
    header = ["pcds", "lad26cd", "rgn26cd", "lep21cd1", "lep21cd2", "usrtypind"]
    assert normalize_columns(header, strip_vintage=True) == [
        "pcds",
        "lad_cd",
        "rgn_cd",
        "lep_cd1",
        "lep_cd2",
        "usrtypind",
    ]


def test_keeps_vintage_when_names_would_collide() -> None:
    header = ["oa11cd", "oa21cd", "imd20ind", "imd25ind", "pcon24cd"]
    assert normalize_columns(header, strip_vintage=True) == [
        "oa11cd",
        "oa21cd",
        "imd20ind",
        "imd25ind",
        "pcon_cd",
    ]


def test_lowercases_lookup_headers_and_drops_blank_columns() -> None:
    header = ["RGN25CD", "RGN25NM", "RGN25NMW", "", " "]
    assert normalize_columns(header, strip_vintage=True) == [
        "rgn_cd",
        "rgn_nm",
        "rgn_nmw",
        None,
        None,
    ]


def test_strip_vintage_disabled() -> None:
    assert normalize_columns(["LAD26CD", ""], strip_vintage=False) == ["lad26cd", None]


def test_types_and_conversion() -> None:
    assert column_type("lat").type_dict["type"] == ["number"]
    assert column_type("imd25ind").type_dict["type"] == ["integer"]
    assert column_type("east1m").type_dict["type"] == ["string"]
    assert convert_value("lat", "51.501009") == 51.501009
    assert convert_value("imd25ind", "18002") == 18002
    assert convert_value("east1m", "0801193") == "0801193"
    assert convert_value("doterm", "") is None
