"""Tests for :mod:`gui.views.focus_curve_panel`."""
import dataclasses

from matplotlib.colors import to_rgba
from matplotlib.markers import MarkerStyle
import numpy as np
import pytest

from nickel_focus import focus
from nickel_focus.gui.views.focus_curve_panel import ARCSEC_PER_UNBINNED_PIXEL, FocusCurvePanel


def _step_results(focus_sweep):
    seq = focus.ArchiveFocusSequence(focus_sweep['focus_values'], focus_sweep['files'])
    return list(seq.step(method='brightest'))


def test_add_result_accumulates_points(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = FocusCurvePanel()

    for r in results[:2]:
        panel.add_result(r)

    assert len(panel._results) == 2, 'panel should track every added result'
    assert panel.ax.collections, 'a scatter plot should exist once results are added'


def test_curve_and_vertex_appear_once_three_points_exist(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = FocusCurvePanel()

    for r in results[:2]:
        panel.add_result(r)
    assert not panel.ax.lines, 'fewer than 3 points: no fitted curve should be drawn yet'

    panel.add_result(results[2])
    assert panel.ax.lines, 'with 3+ points, the fitted quadratic curve should be drawn'


def test_curve_fit_recovers_known_best_focus(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = FocusCurvePanel()
    for r in results:
        panel.add_result(r)

    vertex_x = None
    for coll in panel.ax.collections:
        if coll.get_label().startswith('Best focus'):
            vertex_x = coll.get_offsets()[0][0]

    assert vertex_x is not None, 'a labeled best-focus point should be plotted'
    assert vertex_x == pytest.approx(focus_sweep['best_focus'], abs=5.), \
        'plotted vertex should match the known best focus'


def test_outlier_points_are_drawn_as_a_distinct_series(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    normal = [dataclasses.replace(r, is_outlier=False) for r in results[:2]]
    outlier = dataclasses.replace(results[2], is_outlier=True)

    panel = FocusCurvePanel()
    for r in normal:
        panel.add_result(r)
    panel.add_result(outlier)

    labels = [coll.get_label() for coll in panel.ax.collections]
    assert 'Measured' in labels, 'normal points should be drawn as their own series'
    assert 'Outlier' in labels, 'outlier points should be drawn as a visually distinct series'


def test_xlabel_is_not_clipped_when_the_panel_is_short(qapp, focus_sweep):
    # A fixed-margin figure can have its bottom label clipped once the
    # panel is squeezed shorter than that margin needs -- constrained
    # layout is what's supposed to prevent that.
    results = _step_results(focus_sweep)
    panel = FocusCurvePanel()
    for r in results:
        panel.add_result(r)
    panel.resize(300, 150)  # deliberately shorter than a fixed margin would assume
    panel.show()
    qapp.processEvents()

    panel.figure.canvas.draw()
    renderer = panel.figure.canvas.get_renderer()
    xlabel_bbox = panel.ax.xaxis.label.get_window_extent(renderer=renderer)

    assert xlabel_bbox.y0 >= panel.figure.bbox.y0, \
        "the x-axis label should stay within the figure's bounds, not get clipped off the bottom"


def test_reset_clears_the_plot(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = FocusCurvePanel()
    for r in results:
        panel.add_result(r)

    panel.reset()

    assert panel._results == [], 'reset should clear the stored results'
    assert not panel.ax.collections, 'reset should clear the scatter points'
    assert not panel.ax.lines, 'reset should clear the fitted curve'


def _synthetic_results(focus_sweep, points, binning=None):
    """Results at the given (focus, fwhm) points, built from a real one."""
    template = _step_results(focus_sweep)[0]
    return [
        dataclasses.replace(template, focus_value=x, fwhm=y, binning=binning)
        for x, y in points
    ]


# These three points lie on a parabola whose vertex, (355, 4.1), is beyond them.
_ONE_SIDED = [(340., 5.0), (345., 4.5), (350., 4.2)]


def test_limits_are_set_by_the_measured_points(qapp, focus_sweep):
    panel = FocusCurvePanel()
    for r in _synthetic_results(focus_sweep, _ONE_SIDED):
        panel.add_result(r)

    (x_low, x_high), (y_low, y_high) = panel.ax.get_xlim(), panel.ax.get_ylim()
    assert x_low < 340. and x_high > 350., 'the limits should include every measured point'
    assert y_low < 4.2 and y_high > 5.0, 'the limits should include every measured FWHM'
    assert x_high < 355., 'the best focus (x=355) should not widen the focus limits'
    assert y_low > 4.1, 'the best focus (FWHM 4.1) should not extend the FWHM limits'


def test_best_focus_is_an_open_black_star(qapp, focus_sweep):
    panel = FocusCurvePanel()
    for r in _step_results(focus_sweep):
        panel.add_result(r)

    best = [c for c in panel.ax.collections if c.get_label().startswith('Best focus')]
    assert len(best) == 1, 'there should be one best-focus marker'
    marker = best[0]
    star = MarkerStyle('*')
    assert np.allclose(marker.get_paths()[0].vertices,
                       star.get_path().transformed(star.get_transform()).vertices), \
        'the marker should be a star'
    assert len(marker.get_facecolors()) == 0, 'the star should be open (no fill)'
    assert np.allclose(marker.get_edgecolors()[0], to_rgba('black')), \
        'the star should be outlined in black'


@pytest.mark.parametrize('binning', [1, 2, 4])
def test_right_axis_gives_fwhm_in_arcsec(qapp, focus_sweep, binning):
    panel = FocusCurvePanel()
    for r in _synthetic_results(focus_sweep, _ONE_SIDED, binning=binning):
        panel.add_result(r)

    scale = ARCSEC_PER_UNBINNED_PIXEL * binning
    assert panel.ax_arcsec.get_ylabel() == 'FWHM (arcsec)', \
        'with a known binning, the right axis should be in arcsec'
    assert np.allclose(panel.ax_arcsec.get_ylim(), np.array(panel.ax.get_ylim()) * scale), \
        f'the right axis should be the left times {scale:.4f} arcsec per pixel'


@pytest.mark.parametrize('binnings', [[None, None, None], [2, 2, 4], [2, None, 2]])
def test_right_axis_repeats_pixels_without_one_known_binning(qapp, focus_sweep, binnings):
    panel = FocusCurvePanel()
    for r, binning in zip(_synthetic_results(focus_sweep, _ONE_SIDED), binnings):
        panel.add_result(dataclasses.replace(r, binning=binning))

    assert panel.ax_arcsec.get_visible(), 'the right axis should always be shown'
    assert panel.ax_arcsec.get_ylabel() == 'FWHM (pixels)', \
        'without one known binning, the right axis should be in pixels'
    assert np.allclose(panel.ax_arcsec.get_ylim(), panel.ax.get_ylim()), \
        'in pixels, the right axis should repeat the left'


def test_right_axis_shows_before_any_points(qapp):
    panel = FocusCurvePanel()

    assert panel.ax_arcsec.get_visible(), 'the right axis should show even with no points'
    assert panel.ax_arcsec.get_ylabel() == 'FWHM (pixels)', \
        'with no points, the right axis should be in pixels'


def test_right_axis_stays_on_the_right_after_redraws(qapp, focus_sweep):
    panel = FocusCurvePanel()
    for r in _synthetic_results(focus_sweep, _ONE_SIDED, binning=2):
        panel.add_result(r)  # each one clears and redraws both axes

    assert panel.ax_arcsec.yaxis.get_label_position() == 'right', \
        "the right axis's label should stay on the right"
    assert panel.ax_arcsec.yaxis.get_ticks_position() == 'right', \
        "the right axis's ticks should stay on the right"


def test_unmeasured_results_are_not_plotted(qapp, focus_sweep):
    result = _step_results(focus_sweep)[0]
    panel = FocusCurvePanel()

    panel.add_result(dataclasses.replace(result, fwhm=None, centroid=None, stamp=None))

    assert panel._results == [], 'a result with no FWHM should not be added to the curve'
