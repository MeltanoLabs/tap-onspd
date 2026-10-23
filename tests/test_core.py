# Copyright (c) 2026 Meltano.

"""Tests standard tap features against the live ONSPD release, using the built-in SDK tests library."""

import pytest
from singer_sdk.testing import get_tap_test_class

from tap_onspd.tap import TapONSPD


@pytest.fixture
def download_dir() -> None:
    """Keep the release zip in the system temp directory, where the class-scoped sync already put it."""


TestTapONSPD = get_tap_test_class(tap_class=TapONSPD, config={})
