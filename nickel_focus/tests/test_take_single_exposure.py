"""
Tests for :func:`focus.FocusSequence.take_single_exposure`, and for
:func:`focus.FocusSequence.execute`'s ``goto=True`` path, which is built on it.
"""
import pytest

from nickel_focus import focus


def _services(fake_hardware):
    """The fake telescope and camera services, as sequence keyword arguments."""
    return {'poco': fake_hardware['poco'], 'cam': fake_hardware['cam']}


def test_take_single_exposure_requires_hardware(focus_sweep):
    seq = focus.ArchiveFocusSequence(focus_sweep['focus_values'], focus_sweep['files'])
    with pytest.raises(ValueError, match='No telescope focus/exposure control'):
        seq.take_single_exposure(350., method='brightest')


def test_take_single_exposure_moves_and_measures(fake_hardware):
    seq = focus.FocusSequence(**_services(fake_hardware))

    result = seq.take_single_exposure(345., method='brightest')

    assert fake_hardware['sim'].focus == 345., 'take_single_exposure should move the focus'
    assert result.index == 0, "a standalone exposure isn't part of a larger sequence"
    assert result.focus_value == 345., 'result should report the focus value it was taken at'
    assert result.fwhm > 0, 'measured FWHM should be positive'

    # No sequence bookkeeping should be touched -- this is a standalone
    # operation, not a step.
    assert seq.observed_focus == [], 'take_single_exposure should not append to observed_focus'
    assert seq.exposures == [], 'take_single_exposure should not append to exposures'
    assert seq.step_iter == 0, 'take_single_exposure should not advance step_iter'


def test_take_single_exposure_out_of_range_raises(fake_hardware):
    seq = focus.FocusSequence(**_services(fake_hardware))
    with pytest.raises(ValueError, match='out of range'):
        seq.take_single_exposure(50., method='brightest')  # below the 165 minimum


def test_execute_with_goto_now_works_against_fake_hardware(fake_hardware):
    # Regression test: execute(goto=True) once called a nonexistent
    # measure_fwhm() and never appended its verification exposure to
    # self.exposures before trying to read it back.
    seq = focus.GridFocusSequence(340., 5., nstep=5, **_services(fake_hardware))

    best_focus, best_fwhm = seq.execute(goto=True, plot=False, method='brightest')

    assert best_focus == pytest.approx(fake_hardware['best_focus'], abs=5.), \
        'the fitted best focus should still be correct'
    assert fake_hardware['sim'].focus == best_focus, \
        'goto=True should move the telescope to the fitted best focus'
