"""
Tests for pointing offsets: the offset calculation (:mod:`nickel_focus.pointing`),
the guider-offset keyword sequence
(:meth:`~nickel_focus.services.nickelpoco.NickelPoco.apply_guider_offset`), and
applying an offset (:meth:`~nickel_focus.slew.NickelTelescopePointing.apply_offset`),
over the simulated telescope.
"""
import logging

from astropy.coordinates import Angle
import numpy as np
import pytest

from nickel_focus import pointing
from nickel_focus import slew
from nickel_focus.focus import ARCSEC_PER_UNBINNED_PIXEL
from nickel_focus.services import KTLReadOnlyError, KTLTimeoutError, NickelPoco
from nickel_focus.services import nickelpoco


def _radians(arcsec):
    return Angle(arcsec, unit='arcsec').radian


# -- the offset calculation ---------------------------------------------------

def test_image_center_is_the_geometric_center():
    assert pointing.image_center((2048, 2088)) == (1043.5, 1023.5), \
        'an even size should put the center on the edge between the middle pixels'
    assert pointing.image_center((5, 7)) == (3.0, 2.0), \
        'an odd size should put the center on the middle pixel'


def test_star_up_and_left_needs_an_east_and_north_offset():
    # Low column is East, low row is North: a star there means the telescope
    # must point further East and North.
    offset = pointing.pointing_offset((40., 30.), (101, 101), binning=1)
    assert offset.east == pytest.approx(10 * ARCSEC_PER_UNBINNED_PIXEL), \
        'a star 10 columns left of center should need an East offset'
    assert offset.north == pytest.approx(20 * ARCSEC_PER_UNBINNED_PIXEL), \
        'a star 20 rows above center should need a North offset'
    assert offset.center == (50., 50.), 'the offset should record the image center'
    assert offset.star == (40., 30.), 'the offset should record the star'


def test_star_down_and_right_needs_a_west_and_south_offset():
    offset = pointing.pointing_offset((60., 70.), (101, 101), binning=1)
    assert offset.east < 0 and offset.north < 0, \
        'a star right of and below center should need a West and South offset'


def test_offset_scales_with_binning():
    unbinned = pointing.pointing_offset((40., 30.), (101, 101), binning=1)
    binned = pointing.pointing_offset((40., 30.), (101, 101), binning=2)
    assert binned.east == pytest.approx(2 * unbinned.east), \
        'each binned pixel should cover twice the sky'
    assert binned.north == pytest.approx(2 * unbinned.north), \
        'each binned pixel should cover twice the sky'


def test_unknown_binning_is_refused():
    with pytest.raises(ValueError, match='binning is unknown'):
        pointing.pointing_offset((40., 30.), (101, 101), binning=None)


def test_too_large_an_offset_is_refused():
    # 1043.5 pixels binned 2x2 is about 347 arcsec, beyond the 300 arcsec limit.
    with pytest.raises(ValueError, match='larger than the 300″ limit'):
        pointing.pointing_offset((0., 1000.), (2048, 2088), binning=2)
    offset = pointing.pointing_offset((0., 1000.), (2048, 2088), binning=2, max_offset=400.)
    assert offset.size > 300., 'a larger limit should allow the same offset'


def test_offset_text():
    assert pointing.offset_text(12.34, 4.5) == '12.3″ E, 4.5″ N', \
        'positive offsets should read East and North'
    assert pointing.offset_text(-3., -0.24) == '3.0″ W, 0.2″ S', \
        'negative offsets should read West and South, as positive numbers'


def test_nearest_source_within_the_radius():
    sources = np.array([[10., 10.], [50., 52.], [90., 90.]])
    assert pointing.nearest_source(sources, 48., 50.) == (50., 52.), \
        'the nearest detected source should be chosen'


def test_no_source_near_enough():
    sources = np.array([[10., 10.], [90., 90.]])
    assert pointing.nearest_source(sources, 50., 50.) is None, \
        'a source beyond the radius should not be chosen'
    assert pointing.nearest_source(np.empty((0, 2)), 50., 50.) is None, \
        'with no sources there should be no star'
    assert pointing.nearest_source(None, 50., 50.) is None, \
        'without a source list there should be no star'


# -- the guider-offset keyword sequence ----------------------------------------

def _poco_writes(sim):
    return [(name, value) for service, name, value in sim.ktl.writes if service == 'nickelpoco']


def test_guider_offset_sequence(fake_telescope):
    NickelPoco().apply_guider_offset(10., -5.)

    writes = _poco_writes(fake_telescope)
    assert [name for name, _ in writes] == ['POCGDFIN', 'POCGDIX', 'POCGDIY'], \
        'POCGDFIN should be armed before both offsets are written'
    assert fake_telescope.guider_offsets == [
        ('POCGDIX', pytest.approx(_radians(10.))), ('POCGDIY', pytest.approx(_radians(-5.)))
    ], 'the offsets should be written in radians, East as X and North as Y'
    assert fake_telescope.keyword('nickelpoco', 'POCGDFIN').value == 0, \
        'the move should have been waited for until POCGDFIN read 0'
    assert fake_telescope.on_target_marks == 0, \
        'applying the offset alone should not mark the telescope on target'


def test_guider_offset_in_series(fake_telescope, monkeypatch):
    monkeypatch.setattr(nickelpoco, 'GUIDER_OFFSET_IN_SERIES', True)
    NickelPoco().apply_guider_offset(10., -5.)

    assert [name for name, _ in _poco_writes(fake_telescope)] == [
        'POCGDFIN', 'POCGDIX', 'POCGDFIN', 'POCGDIY'
    ], 'in series, each axis should be armed and written in turn'


def test_an_axis_with_no_offset_is_skipped(fake_telescope):
    NickelPoco().apply_guider_offset(0., 5.)
    assert [name for name, _ in _poco_writes(fake_telescope)] == ['POCGDFIN', 'POCGDIY'], \
        'an axis with no offset should not be written'

    fake_telescope.ktl.writes.clear()
    NickelPoco().apply_guider_offset(0., 0.)
    assert _poco_writes(fake_telescope) == [], 'with no offset at all, nothing should be written'


def test_guider_offset_sign_and_cos_dec(fake_telescope, monkeypatch):
    monkeypatch.setattr(nickelpoco, 'GUIDER_OFFSET_SIGN', (-1, -1))
    monkeypatch.setattr(nickelpoco, 'GUIDER_OFFSET_COS_DEC', True)
    fake_telescope.dec = 60.
    NickelPoco().apply_guider_offset(10., 5.)

    assert fake_telescope.guider_offsets == [
        ('POCGDIX', pytest.approx(-_radians(10.) / 0.5)), ('POCGDIY', pytest.approx(-_radians(5.)))
    ], 'the signs should be applied, and X divided by cos(Dec)'


def test_a_failed_guider_move_raises(fake_telescope):
    fake_telescope.guider_move_succeeds = False
    with pytest.raises(ValueError, match='guider offset failed'):
        NickelPoco().apply_guider_offset(10., 5.)


def test_an_unfinished_guider_move_raises(fake_telescope):
    fake_telescope.guider_move_finishes = False
    with pytest.raises(ValueError, match='did not finish'):
        NickelPoco().apply_guider_offset(10., 5., timeout=0.1)


def test_a_guider_move_that_does_not_arm_raises(fake_telescope):
    fake_telescope.guider_arms = False
    with pytest.raises(ValueError, match='did not change to 1'):
        NickelPoco().apply_guider_offset(10., 5., timeout=0.1)
    assert fake_telescope.guider_offsets == [], 'no offset should be written unless armed'


def test_no_reply_from_the_guider_raises(fake_telescope):
    fake_telescope.keyword('nickelpoco', 'POCGDFIN').responsive = False
    with pytest.raises(KTLTimeoutError, match='No reply from nickelpoco.POCGDFIN'):
        NickelPoco().apply_guider_offset(10., 5., timeout=0.1)


def test_a_read_only_service_never_offsets(fake_telescope):
    with pytest.raises(KTLReadOnlyError):
        NickelPoco(read_only=True).apply_guider_offset(10., 5.)
    assert fake_telescope.ktl.writes == [], 'a read-only service should never write'


def test_mark_guider_on_target(fake_telescope):
    assert NickelPoco().mark_guider_on_target() is True, \
        'POCGDOT should bring the telescope on target'
    assert fake_telescope.on_target_marks == 1, 'POCGDOT should be written once'

    fake_telescope.marks_on_target = False
    assert NickelPoco().mark_guider_on_target(timeout=0.1) is False, \
        'a telescope that does not report on target should give False'


# -- applying an offset ---------------------------------------------------------

def test_apply_offset_offsets_then_marks_on_target(fake_telescope, caplog):
    telescope = slew.NickelTelescopePointing(NickelPoco())
    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        telescope.apply_offset(12.34, -4.5)

    assert [name for name, _ in _poco_writes(fake_telescope)] == [
        'POCGDFIN', 'POCGDIX', 'POCGDIY', 'POCGDOT'
    ], 'the offset should be applied and then marked on target'
    messages = [r.getMessage() for r in caplog.records]
    assert 'Applying pointing offset: 12.3″ E, 4.5″ S' in messages, 'the offset should be logged'
    assert 'Pointing offset applied; marked on target.' in messages, 'success should be logged'


def test_apply_offset_warns_if_not_on_target(fake_telescope, caplog):
    fake_telescope.marks_on_target = False
    telescope = slew.NickelTelescopePointing(NickelPoco())
    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        telescope.apply_offset(12., 4.)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and 'did not report being on target' in warnings[0].getMessage(), \
        'not reaching on target should be a warning, not a failure'


@pytest.mark.parametrize('flag,message', [
    ('movement_allowed', 'Telescope movement is disabled!'),
    ('tracking_on', 'Tracking is disabled!'),
])
def test_apply_offset_checks_the_telescope_first(fake_telescope, flag, message):
    setattr(fake_telescope, flag, False)
    telescope = slew.NickelTelescopePointing(NickelPoco())
    with pytest.raises(ValueError, match=message):
        telescope.apply_offset(12., 4.)
    assert _poco_writes(fake_telescope) == [], 'a failed check must not write anything'


def test_apply_offset_stops_if_the_move_fails(fake_telescope):
    fake_telescope.guider_move_succeeds = False
    telescope = slew.NickelTelescopePointing(NickelPoco())
    with pytest.raises(ValueError, match='guider offset failed'):
        telescope.apply_offset(12., 4.)
    assert fake_telescope.on_target_marks == 0, 'a failed offset must not be marked on target'
