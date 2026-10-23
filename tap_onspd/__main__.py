# Copyright (c) 2026 Meltano.

"""ONSPD entry point."""

from __future__ import annotations

from tap_onspd.tap import TapONSPD

TapONSPD.cli()
