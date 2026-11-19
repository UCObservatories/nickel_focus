"""Tests for :mod:`photometry`."""
import logging

import numpy as np

from nickel_focus import photometry


def _source_frame():
    """A small synthetic image: one bright square source on a flat background."""
    data = np.full((51, 51), 200., dtype=float)
    data[20:31, 20:31] = 8000.
    return data


def test_find_sources_debug_logging(caplog):
    data = _source_frame()

    with caplog.at_level(logging.DEBUG, logger='nickel_focus'):
        photometry.find_sources(data)

    messages = [r.getMessage() for r in caplog.records if r.name == 'nickel_focus']
    assert any('Updated background' in m for m in messages), \
        'find_sources should log its background updates at debug level'
    assert any('converged' in m for m in messages), \
        'find_sources should log convergence progress at debug level'


def test_find_sources_silent_at_info_level(caplog):
    data = _source_frame()

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        photometry.find_sources(data)

    nickel_focus_records = [r for r in caplog.records if r.name == 'nickel_focus']
    assert nickel_focus_records == [], \
        'find_sources should produce no records at all once debug logging is off'


def test_evaluate_shape_warns_on_degenerate_sigma(caplog):
    # A single hot pixel has zero moment spread in both axes.
    data = np.zeros((10, 10))
    data[5, 5] = 100.
    source_mask = data > 0

    with caplog.at_level(logging.WARNING, logger='nickel_focus'):
        shape = photometry.evaluate_shape(data, source_mask)

    assert shape['FWHM'] is None, 'a degenerate source should report no FWHM'
    assert any(
        r.levelname == 'WARNING' and 'too small' in r.getMessage()
        for r in caplog.records if r.name == 'nickel_focus'
    ), 'a degenerate-sigma source should log a warning'


def test_evaluate_shape_warns_on_dissimilar_sigma(caplog):
    # A source spread widely in x but confined to two adjacent rows in y
    # gives a much larger sigma_x than sigma_y.
    data = np.zeros((12, 12))
    data[5, 0:10] = 100.
    data[6, 0:10] = 100.
    source_mask = data > 0

    with caplog.at_level(logging.WARNING, logger='nickel_focus'):
        shape = photometry.evaluate_shape(data, source_mask)

    assert shape['FWHM'] is None, 'a too-elliptical source should report no FWHM'
    assert any(
        r.levelname == 'WARNING' and 'too different' in r.getMessage()
        for r in caplog.records if r.name == 'nickel_focus'
    ), 'a too-elliptical source should log a warning'


def test_find_sources_reports_an_image_with_no_sources():
    import pytest
    blank = np.random.default_rng(0).normal(1000., 10., (100, 100))
    with pytest.raises(ValueError, match='No sources were detected'):
        photometry.find_sources(blank)


def test_measurement_is_separate_from_selection(focus_sweep):
    import pytest
    path = focus_sweep['files'][2]
    raw, bkg, src_data = photometry.measure_sources(path)

    row = photometry.select_source(src_data, 'brightest')
    assert row == int(np.argmax(src_data['CNTS'])), 'brightest should pick the most counts'
    x, y = photometry.source_centroid(src_data, row)
    assert photometry.select_source(src_data, (x + 1., y - 1.)) == row, \
        'a position should pick the nearest source'

    _, _, _, quality, coords = photometry.image_quality(path)
    assert quality == pytest.approx(photometry.source_quality(src_data, row)), \
        'image_quality should give the selected source\'s quality'
    assert coords == pytest.approx((x, y)), 'image_quality should give its centroid'
    assert photometry.measure_quality(src_data, 'brightest') == (pytest.approx(quality), row), \
        'measure_quality should agree with image_quality'


def test_extract_stamp_is_clipped_at_the_edges():
    data = np.arange(100.).reshape(10, 10)
    # Columns -2..4 and rows 5..11 requested: clipped to columns 0..4, rows
    # 5..9.  Unclipped, the start column of -2 gave an empty cutout.
    stamp = photometry.extract_stamp(data, (1., 8.), 6)
    assert stamp.shape == (5, 5), \
        'a cutout near the corner should be clipped, not wrap around the image'
    assert stamp[0, 0] == data[5, 0], 'the cutout should start at the clipped edge'


def test_image_quality_is_the_fwhm(tmp_path):
    # A Gaussian star of known FWHM: the moment width times 2.355 should
    # recover it (a little high, since the segment truncates no wings here
    # but noise adds some).
    import pytest
    from astropy.io import fits
    from nickel_focus.tests.conftest import gaussian_frame
    path = tmp_path / 'star.fits'
    fits.writeto(path, gaussian_frame(4.0, amplitude=20000.))

    _, _, src_data, quality, _ = photometry.image_quality(path)

    assert photometry.SIGMA_TO_FWHM == pytest.approx(2.3548, abs=1e-4), \
        'a Gaussian FWHM should be about 2.355 sigma'
    assert quality == pytest.approx(4.0, rel=0.1), \
        'the image quality should be the FWHM, not the moment width'
    sigma = (src_data['SIGX'][0] + src_data['SIGY'][0]) / 2
    assert quality == pytest.approx(photometry.SIGMA_TO_FWHM * sigma), \
        'the FWHM should be 2.355 times the mean moment width'


def test_moment2d_of_a_known_distribution():
    import pytest
    x = np.array([0., 1., 2., 3.])
    y = np.array([0., 0., 2., 2.])
    z = np.array([1., 1., 1., 1.])
    tot, cx, cy, sx, sy = photometry.moment2d(x, y, z)
    assert tot == 4., 'the total should be the sum of the weights'
    assert (cx, cy) == (1.5, 1.), 'the centroid should be the weighted mean'
    assert sx == pytest.approx(np.std(x)) and sy == pytest.approx(np.std(y)), \
        'the widths should be the weighted (population) standard deviations'
