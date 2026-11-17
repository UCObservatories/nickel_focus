"""Tests for :mod:`nickel_focus.exposure`."""
import types

import pytest

from nickel_focus import exposure


def test_exposure_requires_ktl():
    # The autouse `no_ktl` fixture sets `exposure.ktl` to None, so the camera
    # handle must refuse to construct rather than reach for real hardware.
    with pytest.raises(RuntimeError, match='ktl package is not available'):
        exposure.Exposure()


def _config_reading(exptime, ampconf, binning):
    """An `ExposureConfig` whose keywords are stand-ins returning these values."""
    def keyword(value):
        return types.SimpleNamespace(read=lambda: value)
    # Bypass __init__, which would call ktl.cache(...).
    cfg = object.__new__(exposure.ExposureConfig)
    cfg._exptime = keyword(exptime)
    cfg.expspeed = keyword(ampconf)
    cfg.expbin = keyword(binning)
    return cfg


def test_current_settings_reads_and_names_the_camera_settings():
    cfg = _config_reading('12.5', '1.0MHz', '2,2 ')

    assert cfg.current_settings() == {'exptime': 12.5, 'speed': 'Fast', 'binning': '2,2'}


def test_current_settings_passes_through_an_unaliased_speed():
    cfg = _config_reading('5', '0.5MHz', '1,1')

    assert cfg.current_settings()['speed'] == '0.5MHz'
