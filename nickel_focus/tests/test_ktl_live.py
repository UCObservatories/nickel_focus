"""
Tests against the real KTL services, for use at the telescope (e.g. after a
``ktl`` or dispatcher upgrade).

They run only with ``pytest --ktl-live`` (see ``conftest.py``), and fail rather
than skip if ``ktl`` isn't available then.  **They never change a keyword
value:** every service is constructed with ``read_only=True``, so any write
raises :class:`~nickel_focus.services.base.KTLReadOnlyError` before reaching
``ktl``.  They never construct the GUI.
"""
import pytest

from nickel_focus import exposure
from nickel_focus import focus
from nickel_focus import slew
from nickel_focus.services import NickelPoco, NSciCam

pytestmark = pytest.mark.ktl_live


@pytest.fixture
def poco():
    return NickelPoco(read_only=True)


@pytest.fixture
def cam():
    return NSciCam(read_only=True)


def test_telescope_service_constructs(poco):
    assert poco.read_only, 'the live telescope service should be read-only'


def test_camera_service_constructs(cam):
    # Construction monitors EXPSTATE and EXPOSE and reads AMPCONFLIST.
    assert cam.read_only, 'the live camera service should be read-only'
    assert cam.cfg.expspeed_options, 'AMPCONFLIST should list at least one read speed'


def test_device_classes_construct(poco, cam):
    focus.Focus(poco)
    slew.NickelTelescopePointing(poco)
    exposure.Exposure(cam)


def test_telescope_values_read(poco):
    assert 165 <= poco.secondary_position() <= 500, 'the focus should be within its range'
    poco.pointing()


def test_camera_values_read(cam):
    cam.exposure_state()
    settings = cam.cfg.current_settings()
    assert set(settings) == {'exptime', 'speed', 'binning'}, \
        'the camera settings should have exptime, speed, and binning'
    cam.path.next
