"""
Tests for :class:`exposure.Exposure` and the camera's settings and path
helpers (:class:`~nickel_focus.services.nscicam.ExposureConfig`,
:class:`~nickel_focus.services.nscicam.ExposurePath`), run on the real
service classes over the fake ``ktl`` (see ``fake_ktl.py``).
"""
import pytest

from nickel_focus import exposure
from nickel_focus.services import KTLTimeoutError, KTLUnavailableError, NSciCam
from nickel_focus.services.nscicam import ExposureConfig


def test_camera_requires_ktl():
    # The autouse `no_ktl` fixture sets `services.base.ktl` to None, so the
    # camera service must refuse to construct rather than reach for real
    # hardware.
    with pytest.raises(KTLUnavailableError, match='ktl package is not available'):
        NSciCam()


def test_expose_takes_an_exposure(fake_hardware):
    sim = fake_hardware['sim']
    exp = exposure.Exposure(fake_hardware['cam'])

    exp.expose(exptime=12.5)

    assert sim.keyword('nscicam', 'EXPOSE').writes == ['Go'], 'the exposure should be started'
    assert sim.keyword('nscicam', 'EXPOSURE').writes == [12.5], 'settings applied first'
    assert exp.path.previous == fake_hardware['directory'] / 'fake0001.fits', \
        'the previous exposure path should be the new file'
    assert exp.path.previous.is_file(), 'the exposure file should have been written'


def test_expose_refuses_while_the_camera_is_busy(fake_hardware):
    sim = fake_hardware['sim']
    sim.keyword('nscicam', 'EXPSTATE').value = 'Exposing'

    with pytest.raises(ValueError, match='not ready'):
        exposure.Exposure(fake_hardware['cam']).expose()
    assert sim.keyword('nscicam', 'EXPOSE').writes == [], \
        'no exposure should start while the camera is busy'


def test_expose_reports_a_camera_that_does_not_reply(fake_hardware):
    sim = fake_hardware['sim']
    sim.keyword('nscicam', 'EXPSTATE').responsive = False

    with pytest.raises(KTLTimeoutError, match='No reply from nscicam.EXPSTATE'):
        exposure.Exposure(fake_hardware['cam']).expose()
    assert sim.keyword('nscicam', 'EXPOSE').writes == [], \
        'no exposure should start without a reply'


def test_configure_maps_speed_aliases_and_validates(fake_hardware):
    sim, cfg = fake_hardware['sim'], fake_hardware['cam'].cfg

    cfg.configure(speed='Fast', binning='1,1')

    assert sim.keyword('nscicam', 'AMPCONF').value == '1.0MHz', \
        'the Fast alias should be written as 1.0MHz'
    assert sim.keyword('nscicam', 'BINNING').value == '1,1', \
        'the binning should be written as given'
    with pytest.raises(ValueError, match='not a valid read speed'):
        cfg.configure(speed='Medium')
    with pytest.raises(ValueError, match='not a valid binning'):
        cfg.configure(binning='3,3')


def test_current_settings_reads_and_names_the_camera_settings(fake_hardware):
    sim = fake_hardware['sim']
    sim.keyword('nscicam', 'EXPOSURE').value = '12.5'
    sim.keyword('nscicam', 'AMPCONF').value = '1.0MHz'
    sim.keyword('nscicam', 'BINNING').value = '2,2 '

    assert fake_hardware['cam'].cfg.current_settings() == \
        {'exptime': 12.5, 'speed': 'Fast', 'binning': '2,2'}, \
        'the settings should be read, with the speed named and whitespace stripped'


def test_current_settings_passes_through_an_unaliased_speed(fake_hardware):
    fake_hardware['sim'].keyword('nscicam', 'AMPCONF').value = '0.5MHz'

    assert fake_hardware['cam'].cfg.current_settings()['speed'] == '0.5MHz', \
        'an unaliased speed should be returned unchanged'


def test_next_path_follows_record(fake_hardware, tmp_path):
    sim, path = fake_hardware['sim'], fake_hardware['cam'].path
    sim.keyword('nscicam', 'RECORDDIR').value = str(tmp_path / 'record')
    sim.keyword('nscicam', 'SCRATCHDIR').value = str(tmp_path / 'scratch')
    sim.keyword('nscicam', 'OBSNUM').value = 12

    assert path.next == tmp_path / 'record' / 'fake12.fits', \
        'with RECORD = Yes, the next file should be in RECORDDIR'
    sim.keyword('nscicam', 'RECORD').value = 'No'
    assert path.next == tmp_path / 'scratch' / 'fake12.fits', \
        'with RECORD = No, the next file should be in SCRATCHDIR'


@pytest.mark.parametrize('key, ascii_value, expected', [
    ('exptime', '12.500', 12.5),
    ('speed', '1.0MHz', 'Fast'),
    ('speed', '0.05MHz ', 'Slow'),
    ('speed', '0.5MHz', '0.5MHz'),
    ('binning', ' 2,2', '2,2'),
])
def test_setting_value_converts_keyword_values(key, ascii_value, expected):
    assert ExposureConfig.setting_value(key, ascii_value) == expected, \
        f'{key} {ascii_value!r} should convert to {expected!r}'


def test_setting_value_rejects_a_non_numeric_exposure_time():
    with pytest.raises(ValueError):
        ExposureConfig.setting_value('exptime', 'garbage')
