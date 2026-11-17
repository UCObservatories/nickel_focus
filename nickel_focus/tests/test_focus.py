"""
Tests for :class:`focus.Focus` and :meth:`focus.FocusSequence.set_focus`, run
on the real service classes over the fake ``ktl`` (see ``fake_ktl.py``).
"""
import logging

import pytest

from nickel_focus import focus
from nickel_focus.services import KTLTimeoutError, NickelPoco


@pytest.fixture
def focus_on_fake(fake_telescope):
    return focus.Focus(NickelPoco()), fake_telescope


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == 'nickel_focus']


def test_set_to_moves_the_secondary_and_logs(focus_on_fake, caplog):
    f, sim = focus_on_fake

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        f.set_to(350.)

    assert sim.focus == 350., 'the secondary should have been moved'
    assert f.current == 350., 'current should report the new position'
    assert sim.keyword('nickelpoco', 'POCSECLK').writes == ['off'], \
        'the secondary should be unlocked (and never explicitly re-locked)'
    messages = _messages(caplog)
    assert any('Unlocking secondary' in m for m in messages), \
        'set_to() should log that it is unlocking the secondary'
    assert any('Successfully changed focus to 350' in m for m in messages), \
        'set_to() should log the successful focus change'


def test_set_to_does_nothing_when_already_at_position(focus_on_fake, caplog):
    f, sim = focus_on_fake  # the simulator starts at 340

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        f.set_to(340.)

    assert any('No change needed' in m for m in _messages(caplog)), \
        'set_to() should log when the focus is already at the requested position'
    assert sim.ktl.writes == [], 'nothing should be written'


@pytest.mark.parametrize('value', [164., 501.])
def test_set_to_rejects_out_of_range_values(focus_on_fake, value):
    f, sim = focus_on_fake
    with pytest.raises(ValueError, match='out of range'):
        f.set_to(value)
    assert sim.ktl.writes == [], 'an out-of-range request should write nothing'


def test_set_to_refuses_when_movement_is_disabled(focus_on_fake):
    f, sim = focus_on_fake
    sim.movement_allowed = False
    with pytest.raises(ValueError, match='movement is disabled'):
        f.set_to(350.)
    assert sim.ktl.writes == [], 'a refused move should write nothing'


def test_set_to_reports_a_service_that_does_not_reply(focus_on_fake):
    f, sim = focus_on_fake
    sim.keyword('nickelpoco', 'POCSTOP').responsive = False
    with pytest.raises(KTLTimeoutError, match='No reply from nickelpoco.POCSTOP'):
        f.set_to(350.)
    assert sim.ktl.writes == [], 'nothing should be written without a reply'


def test_set_to_fails_if_the_secondary_does_not_relock(focus_on_fake):
    f, sim = focus_on_fake
    # Without the simulated move, the lock stays off once unlocked.
    sim.keyword('nickelpoco', 'POCSECPD').on_write = None
    with pytest.raises(ValueError, match='POCSECLK did not turn on'):
        f.set_to(350.)


def test_set_focus_waits_for_the_camera(fake_hardware):
    seq = focus.FocusSequence(poco=fake_hardware['poco'], cam=fake_hardware['cam'])
    fake_hardware['sim'].keyword('nscicam', 'EXPSTATE').value = 'Exposing'

    with pytest.raises(ValueError, match='Camera exposure state not ready'):
        seq.set_focus(350.)
    assert fake_hardware['sim'].focus == 340., 'the focus must not move during an exposure'


def test_set_focus_without_a_camera_reply_does_not_move(fake_hardware):
    seq = focus.FocusSequence(poco=fake_hardware['poco'], cam=fake_hardware['cam'])
    fake_hardware['sim'].keyword('nscicam', 'EXPSTATE').responsive = False

    with pytest.raises(KTLTimeoutError, match='No reply from nscicam.EXPSTATE'):
        seq.set_focus(350.)
    assert fake_hardware['sim'].focus == 340., 'the focus must not move if the camera is silent'


def test_sequence_without_both_services_has_no_hardware(fake_hardware):
    seq = focus.FocusSequence(poco=fake_hardware['poco'])
    assert seq._focus is None and seq._exposure is None, \
        'without both services there should be no hardware handles'
