"""
Tests for the ktl-free parts of :mod:`focus`: the
:func:`focus.FocusSequence.step`/:func:`focus.FocusSequence.execute`
sequence-stepping engine, exercised via :class:`focus.ArchiveFocusSequence`
so no :class:`focus.Focus`/:class:`exposure.Exposure` hardware objects are
ever constructed.
"""
import logging
from pathlib import Path

import numpy as np
import pytest

from nickel_focus import focus
from nickel_focus.photometry import image_quality


def _make_sequence(focus_sweep):
    return focus.ArchiveFocusSequence(focus_sweep['focus_values'], focus_sweep['files'])


def test_archive_sequence_has_no_hardware(focus_sweep):
    seq = _make_sequence(focus_sweep)
    assert seq._focus is None, 'ArchiveFocusSequence should not construct a Focus object'
    assert seq._exposure is None, 'ArchiveFocusSequence should not construct an Exposure object'


def test_step_yields_one_result_per_exposure(focus_sweep):
    seq = _make_sequence(focus_sweep)

    results = list(seq.step(method='brightest'))

    assert len(results) == len(focus_sweep['files']), 'wrong number of steps yielded'
    for i, result in enumerate(results):
        assert result.index == i, 'StepResult.index should count up from 0'
        assert result.focus_value == focus_sweep['focus_values'][i], 'wrong focus value'
        assert isinstance(result.exposure, Path), 'StepResult.exposure should be a Path'
        assert result.exposure == focus_sweep['files'][i], 'wrong exposure file'
        assert result.fwhm > 0, 'measured FWHM should be positive'
        assert result.frame.shape == (101, 101), 'frame should match the written FITS shape'
        assert result.stamp.size > 0, 'stamp cutout should not be empty'
        assert isinstance(result.is_outlier, (bool, np.bool_)), 'is_outlier should be boolean'

    # The sequence's own bookkeeping should match what step() yielded.
    assert seq.observed_focus == focus_sweep['focus_values'], 'observed_focus out of sync'
    assert seq.img_quality == [r.fwhm for r in results], 'img_quality out of sync with results'
    assert seq.step_iter == len(focus_sweep['files']), 'step_iter should equal steps taken'


def test_step_can_be_driven_one_at_a_time(focus_sweep):
    # Mirrors how a future GUI worker thread would drive the sequence:
    # advancing it one step at a time (so it can check a stop flag between
    # steps) rather than consuming it in one `for` loop.
    seq = _make_sequence(focus_sweep)
    stepper = seq.step(method='brightest')

    first = next(stepper)
    assert first.index == 0, 'first yielded result should have index 0'
    assert seq.step_iter == 1, 'step_iter should advance after one next()'

    second = next(stepper)
    assert second.index == 1, 'second yielded result should have index 1'
    assert seq.step_iter == 2, 'step_iter should advance after a second next()'


def test_execute_recovers_known_best_focus(focus_sweep):
    seq = _make_sequence(focus_sweep)

    best_focus, best_fwhm = seq.execute(goto=False, plot=False, method='brightest')

    assert seq.plot is None, 'plot=False should skip FocusPlot entirely'
    assert best_focus == pytest.approx(focus_sweep['best_focus'], abs=5.), \
        'fitted best focus should match the synthetic sweep vertex'

    # The moment-based FWHM in photometry.py is measured over a
    # thresholded source mask, which is expected to be systematically
    # biased relative to the Gaussian profile's analytic FWHM (truncating
    # the wings before computing the second moment underestimates it) --
    # so check self-consistency against a direct measurement of the frame
    # taken nearest the true best focus, rather than against the analytic
    # input value.
    middle = focus_sweep['focus_values'].index(focus_sweep['best_focus'])
    _, _, _, direct_fwhm, _, _ = image_quality(focus_sweep['files'][middle],
                                                method='brightest')
    assert best_fwhm == pytest.approx(direct_fwhm, rel=0.3), \
        'fitted best FWHM should match a direct measurement at the true best focus'


def test_execute_with_plot_does_not_require_a_display(focus_sweep):
    # `plot=True` must still work under the non-interactive Agg backend
    # conftest.py selects, so this exercises FocusPlot without needing a
    # real display.
    seq = _make_sequence(focus_sweep)

    seq.execute(goto=False, plot=True, method='brightest')

    assert seq.plot is not None, 'plot=True should construct a FocusPlot'


def test_execute_with_goto_requires_hardware(focus_sweep):
    seq = _make_sequence(focus_sweep)
    with pytest.raises(ValueError, match='No telescope focus/exposure control'):
        seq.execute(goto=True, plot=False)


def test_fit_best_focus_requires_at_least_three_points():
    with pytest.raises(ValueError):
        focus.FocusSequence.fit_best_focus([340., 345.], [4., 3.])


def test_reanalyze_updates_measurements_in_place(focus_sweep):
    seq = _make_sequence(focus_sweep)
    seq.execute(goto=False, plot=False, method='brightest')

    original_focus = list(seq.observed_focus)
    original_exposures = list(seq.exposures)
    original_step_iter = seq.step_iter

    results = list(seq.reanalyze(method='brightest'))

    # reanalyze() must not touch which exposures exist or when they were
    # taken -- only the measurements derived from them.
    assert seq.observed_focus == original_focus, 'reanalyze() must not change observed_focus'
    assert seq.exposures == original_exposures, 'reanalyze() must not change exposures'
    assert seq.step_iter == original_step_iter, 'reanalyze() must not change step_iter'

    assert len(results) == len(original_exposures), 'wrong number of results yielded'
    for i, result in enumerate(results):
        assert result.index == i, 'StepResult.index should count up from 0'
        assert result.focus_value == original_focus[i], 'wrong focus value'
        assert result.exposure == original_exposures[i], 'wrong exposure file'
        assert result.fwhm > 0, 'measured FWHM should be positive'

    # Re-running the same method against the same files should be
    # deterministic, and the sequence's own bookkeeping should match.
    assert seq.img_quality == [r.fwhm for r in results], 'img_quality out of sync with results'


def test_reanalyze_with_no_exposures_yields_nothing(focus_sweep):
    seq = _make_sequence(focus_sweep)
    assert list(seq.reanalyze(method='brightest')) == [], \
        'reanalyze() should yield nothing when no exposures have been collected'


def test_reanalyze_tracks_current_method(focus_sweep):
    seq = _make_sequence(focus_sweep)
    seq.execute(goto=False, plot=False, method='brightest')
    assert seq.method == 'brightest', 'execute() should record the method it used'

    list(seq.reanalyze(method='weighted'))
    assert seq.method == 'weighted', 'reanalyze() should update the recorded method'


def test_reanalyze_with_coordinate_method(focus_sweep):
    seq = _make_sequence(focus_sweep)
    seq.execute(goto=False, plot=False, method='brightest')
    brightest_fwhm = list(seq.img_quality)

    # Each synthetic frame has a single source near the center of the
    # 101x101 frame; selecting it by coordinate should recover the same
    # measurement as "brightest".
    coord_results = list(seq.reanalyze(method=(50., 50.)))
    assert [r.fwhm for r in coord_results] == pytest.approx(brightest_fwhm), \
        'coordinate-based selection of the only source should match "brightest"'


def test_exposure_object_name_reads_the_header(tmp_path):
    from astropy.io import fits
    import numpy as np
    named = tmp_path / 'named.fits'
    header = fits.Header()
    header['OBJECT'] = 'Pointing12: focus=345.0'
    fits.writeto(named, np.zeros((4, 4)), header=header)
    unnamed = tmp_path / 'unnamed.fits'
    fits.writeto(unnamed, np.zeros((4, 4)))

    assert focus.exposure_object_name(named) == 'Pointing12: focus=345.0', \
        "the OBJECT header value should be returned"
    assert focus.exposure_object_name(unnamed) is None, 'a header without OBJECT gives None'
    assert focus.exposure_object_name(tmp_path / 'missing.fits') is None, \
        'an unreadable file gives None'


def _fits_with(tmp_path, name, **cards):
    from astropy.io import fits
    import numpy as np
    header = fits.Header()
    for key, value in cards.items():
        header[key] = value
    path = tmp_path / name
    fits.writeto(path, np.zeros((4, 4)), header=header)
    return path


@pytest.mark.parametrize('binning', [1, 2, 4])
def test_exposure_binning_reads_the_ccd_pixel_wcs(tmp_path, binning):
    path = _fits_with(tmp_path, 'binned.fits', CD1_1C=float(binning), CD2_2C=float(binning))
    assert focus.exposure_binning(path) == binning, \
        'the binning should come from CD1_1C/CD2_2C'


def test_exposure_binning_is_none_without_the_wcs(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger='nickel_focus'):
        assert focus.exposure_binning(_fits_with(tmp_path, 'plain.fits')) is None, \
            'without CD1_1C/CD2_2C, the binning is unknown'
        assert focus.exposure_binning(tmp_path / 'missing.fits') is None, \
            'an unreadable file gives an unknown binning'
    messages = [r.getMessage() for r in caplog.records]
    assert any('plain.fits' in m and 'no CD1_1C/CD2_2C' in m for m in messages), \
        'a header without the binning should be logged as a warning'
    assert any('missing.fits' in m and 'Could not read the binning' in m for m in messages), \
        'an unreadable file should be logged as a warning'


@pytest.mark.parametrize('cards', [{'CD1_1C': 2., 'CD2_2C': 4.}, {'CD1_1C': 3., 'CD2_2C': 3.}])
def test_unexpected_binning_is_unknown_and_logged(tmp_path, caplog, cards):
    path = _fits_with(tmp_path, 'odd.fits', **cards)

    with caplog.at_level(logging.WARNING, logger='nickel_focus'):
        assert focus.exposure_binning(path) is None, \
            'unequal values, or values other than 1, 2, or 4, should give an unknown binning'
    assert any('Unexpected binning' in r.getMessage() for r in caplog.records), \
        'an unexpected binning should be logged as a warning'


@pytest.mark.parametrize('fwhm, binning, expected', [
    (3.21, None, '3.21 pix'),
    (3., 2, '3.00 pix (1.00 arcsec)'),
    (6., 1, '6.00 pix (1.00 arcsec)'),
    (1.5, 4, '1.50 pix (1.00 arcsec)'),
])
def test_fwhm_text(fwhm, binning, expected):
    assert focus.fwhm_text(fwhm, binning) == expected, \
        f'FWHM {fwhm} at binning {binning} should read {expected!r}'


@pytest.mark.parametrize('binnings, expected', [
    ([2, 2, 2], 2), ([2, 4], None), ([2, None], None), ([], None),
])
def test_common_binning(binnings, expected):
    assert focus.common_binning(binnings) == expected, \
        f'{binnings} should have common binning {expected}'
