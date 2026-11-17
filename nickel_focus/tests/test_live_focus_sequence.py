"""
Tests for :class:`focus.GridFocusSequence`/
:class:`focus.AutomatedFocusSequence`, driven against the fake ``ktl``'s
simulated telescope and camera (see the ``fake_hardware`` fixture in
``conftest.py``) rather than real ``ktl``/telescope/camera hardware.

:mod:`test_focus_sequence` only exercises
:class:`focus.ArchiveFocusSequence`, which never touches
:class:`focus.Focus`/:class:`exposure.Exposure` at all. These tests
validate the *software logic* -- stepping, focus-range checks, the
adaptive curve-following algorithm -- not the real KTL keyword protocol,
which can only be checked at the telescope.
"""
import pytest

from nickel_focus import focus


def _services(fake_hardware):
    """The fake telescope and camera services, as sequence keyword arguments."""
    return {'poco': fake_hardware['poco'], 'cam': fake_hardware['cam']}


def test_hardware_is_built_on_the_given_services(fake_hardware):
    seq = focus.GridFocusSequence(340., 5., nstep=3, **_services(fake_hardware))
    assert seq._focus.poco is fake_hardware['poco'], \
        'Focus should use the given telescope service'
    assert seq._exposure.cam is fake_hardware['cam'], \
        'Exposure should use the given camera service'


def test_grid_sequence_commands_expected_focus_values(fake_hardware):
    seq = focus.GridFocusSequence(340., 5., nstep=5, **_services(fake_hardware))

    results = list(seq.step(method='brightest'))

    assert [r.focus_value for r in results] == [340., 345., 350., 355., 360.], \
        'GridFocusSequence should command exactly its target_focus values, in order'
    assert fake_hardware['sim'].focus == 360., \
        'the simulated focus should end at the last commanded value'

    best_focus, best_fwhm = seq.fit_best_focus(seq.observed_focus, seq.img_quality)
    assert best_focus == pytest.approx(fake_hardware['best_focus'], abs=5.), \
        'fitting a grid run against the fake hardware should recover the known best focus'


def test_grid_sequence_rejects_out_of_range_focus(fake_hardware):
    # 160 is below the 165 minimum
    seq = focus.GridFocusSequence(160., 5., nstep=3, **_services(fake_hardware))
    with pytest.raises(ValueError, match='out of range'):
        list(seq.step(method='brightest'))


def test_automated_sequence_converges_near_best_focus(fake_hardware):
    seq = focus.AutomatedFocusSequence(340., 5., maxsteps=12, **_services(fake_hardware))

    results = list(seq.step(method='brightest'))

    assert 3 <= len(results) <= 12, 'the adaptive search should finish within its step budget'
    best_focus, best_fwhm = seq.fit_best_focus(seq.observed_focus, seq.img_quality)
    assert best_focus == pytest.approx(fake_hardware['best_focus'], abs=5.), \
        'the adaptive curve-following search should converge near the known best focus'


def test_automated_sequence_starting_past_best_focus_still_converges(fake_hardware):
    # Starting on the *other* side of the true best focus (350) exercises
    # the "direction = -1" branch of step_focus(), not just the default
    # "search upward first" case above.
    seq = focus.AutomatedFocusSequence(365., 5., maxsteps=12, **_services(fake_hardware))

    list(seq.step(method='brightest'))

    best_focus, best_fwhm = seq.fit_best_focus(seq.observed_focus, seq.img_quality)
    assert best_focus == pytest.approx(fake_hardware['best_focus'], abs=5.), \
        'the adaptive search should converge regardless of which side it starts on'
