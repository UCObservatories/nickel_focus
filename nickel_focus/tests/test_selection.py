"""Tests for :mod:`nickel_focus.selection`: choosing the stars on the focus curve."""
import numpy as np
import pytest

from nickel_focus import focus
from nickel_focus import selection
from nickel_focus.selection import StarSelection
from nickel_focus.tests.conftest import THREE_STAR_SHIFTS

SHIFTS = THREE_STAR_SHIFTS


@pytest.fixture
def three_star_results(three_star_sweep):
    """Measured results for the five-exposure sequence of the three stars."""
    seq = focus.ArchiveFocusSequence(three_star_sweep['focus_values'], three_star_sweep['files'])
    return list(seq.step(method='brightest'))


def test_starts_with_the_first_star(three_star_results):
    sel = StarSelection(three_star_results)
    points = sel.points()

    assert len(sel.stars) == 1 and sel.stars[0].first, 'the selection should start with one star'
    assert len(points) == 5, 'the first star should have a point in every exposure'
    assert [p.fwhm for p in points] == [r.fwhm for r in three_star_results], \
        "the first star's points should be each exposure's own measurement"
    assert points[0].centroid == pytest.approx((30., 30.), abs=0.5), \
        'the first star should be the brightest'


def test_append_builds_the_first_star_as_results_arrive(three_star_results):
    sel = StarSelection()
    for result in three_star_results[:3]:
        sel.append(result)
    assert len(sel.points()) == 3, 'each appended exposure should add a point for the first star'


def test_add_matches_a_star_across_exposures(three_star_results):
    sel = StarSelection(three_star_results)

    action, position, matched = sel.add(2, 69., 41.)

    assert action == 'added', 'a star not yet selected should be added'
    assert position == pytest.approx((69., 40.5), abs=0.5), 'the click should snap to the star'
    assert matched == 5, 'the star should be matched in every exposure'
    added = [p for p in sel.points() if p.centroid[0] > 60]
    assert len(added) == 5, 'the added star should contribute a point per exposure'
    for p in added:
        x0, y0 = 70. + SHIFTS[p.exposure][0], 40. + SHIFTS[p.exposure][1]
        assert p.centroid == pytest.approx((x0, y0), abs=0.5), \
            'each exposure should be matched to the same star, at its shifted position'


def test_a_star_missing_from_an_exposure_has_no_point_there(three_star_results):
    sel = StarSelection(three_star_results)

    _, _, matched = sel.add(0, 50., 75.)

    assert matched == 4, 'the third star is missing from the last exposure'
    assert all(p.exposure != 4 for p in sel.points() if p.centroid[1] > 60), \
        'the missing exposure should give that star no point'


def test_adding_a_selected_star_matches_it_again(three_star_results):
    sel = StarSelection(three_star_results)

    action, _, _ = sel.add(1, 31., 29.)  # the brightest, already the first star

    assert action == 'replaced', 'adding a star already selected should re-match it'
    assert len(sel.stars) == 1, 'the star should not be duplicated'
    assert not sel.stars[0].first, 'the re-matched star is now matched by position'
    assert len(sel.points()) == 5, 'the re-matched star should still have a point per exposure'


def test_add_away_from_any_star_raises(three_star_results):
    sel = StarSelection(three_star_results)
    with pytest.raises(ValueError, match='No star found within'):
        sel.add(0, 5., 95.)
    assert len(sel.stars) == 1, 'a failed add should change nothing'


def test_remove_any_star_including_the_first(three_star_results):
    sel = StarSelection(three_star_results)
    sel.add(0, 70., 40.)

    position = sel.remove(3, 31., 31.)

    assert position == pytest.approx((30.5, 31.), abs=0.5), 'the click should snap to the star'
    assert len(sel.stars) == 1 and not sel.stars[0].first, 'the first star should be removed'
    assert all(p.centroid[0] > 60 for p in sel.points()), 'only the added star should remain'


def test_removing_a_star_not_selected_raises(three_star_results):
    sel = StarSelection(three_star_results)
    with pytest.raises(ValueError, match='not in the plotted data'):
        sel.remove(0, 70., 40.)


def test_remove_everything_then_add(three_star_results):
    sel = StarSelection(three_star_results)
    sel.remove(0, 30., 30.)

    assert sel.points() == [], 'with every star removed there should be no data'
    with pytest.raises(ValueError, match='No data to fit'):
        sel.fit()

    sel.add(0, 70., 40.)
    *_, best_focus, _ = sel.fit()
    assert best_focus == pytest.approx(350., abs=2.), 'adding a star again should allow a fit'


def test_pooled_fit(three_star_results):
    sel = StarSelection(three_star_results)
    sel.add(0, 70., 40.)
    sel.add(0, 50., 75.)

    a, b, c, best_focus, best_fwhm = sel.fit()

    assert len(sel.points()) == 14, 'the fit should pool every star (5 + 5 + 4 points)'
    assert best_focus == pytest.approx(350., abs=2.), 'the pooled fit should find the best focus'
    assert a > 0, 'the fitted quadratic should open upward'


def test_too_few_points_to_fit(three_star_results):
    sel = StarSelection(three_star_results[:2])
    with pytest.raises(ValueError, match='Too few points'):
        sel.fit()


def test_stars_in_and_mean_fwhm(three_star_results):
    sel = StarSelection(three_star_results)
    sel.add(0, 70., 40.)

    stars = sel.stars_in(2)
    assert len(stars) == 2, 'both selected stars should be in the exposure'
    assert sel.mean_fwhm(2) == pytest.approx(np.mean([p.fwhm for p in stars])), \
        'the mean FWHM should average the selected stars'
    sel.remove(0, 30., 30.)
    sel.remove(0, 70., 40.)
    assert sel.mean_fwhm(2) is None, 'with no stars selected there should be no mean FWHM'


def test_unmeasured_exposure_has_no_points(three_star_results):
    import dataclasses
    results = list(three_star_results)
    results[1] = dataclasses.replace(
        results[1], fwhm=None, centroid=None, sources=None, source_index=None
    )
    sel = StarSelection(results)
    sel.add(0, 70., 40.)

    assert all(p.exposure != 1 for p in sel.points()), \
        'an unmeasured exposure should give no star a point'


def test_replace_updates_an_exposure(three_star_results):
    sel = StarSelection(three_star_results)
    sel.add(0, 70., 40.)
    sel.replace(2, three_star_results[2])
    assert len(sel.points()) == 10, 'replacing an exposure should keep every star matched'


def test_match_radius():
    assert selection.MATCH_RADIUS == 10., 'stars should be matched within 10 binned pixels'


def test_centroid_outliers_flag_a_star_far_from_the_rest():
    # One stray point among n can't exceed (n - 1)/sqrt(n) standard
    # deviations, which is below 2 for n <= 5; so use eight.
    centroids = [(30., 30.), (30.5, 30.), (29.5, 30.5), (30., 29.5), (30.2, 30.3),
                 (29.8, 29.7), (30.1, 29.9), (45., 45.)]
    flags = focus.centroid_outliers(centroids)
    assert list(flags) == [False] * 7 + [True], \
        'only the position far from the rest should be an outlier'
    assert list(focus.centroid_outliers(centroids[:2])) == [False, False], \
        'with fewer than three positions none should be an outlier'


def test_plot_limits_follow_the_best_fit(three_star_results):
    sel = StarSelection(three_star_results)
    a, b, c, best_focus, best_fwhm = sel.fit()

    (focus_low, focus_high), (fwhm_low, fwhm_high) = sel.plot_limits()

    # The curve's range over 340-360: from its vertex to its highest end.
    high = max(a * f**2 + b * f + c for f in (340., 360.))
    padding = 0.3 * (high - best_fwhm)
    assert fwhm_low == pytest.approx(max(0., best_fwhm - padding), rel=1e-3), \
        "the lower limit should be the fit's minimum less 30% of its range"
    assert fwhm_high == pytest.approx(high + padding, rel=1e-3), \
        "the upper limit should be the fit's maximum plus 30% of its range"
    assert (focus_low, focus_high) == pytest.approx((339., 361.)), \
        'the focus range should be the focus values with a 5% margin on each side'


def test_plot_limits_never_go_below_zero(three_star_results):
    import dataclasses
    # A steep curve with a small minimum: 30% of its range below the minimum
    # would be negative.
    results = [
        dataclasses.replace(r, fwhm=0.1 + 0.05 * (r.focus_value - 350.)**2)
        for r in three_star_results
    ]
    (_, _), (fwhm_low, fwhm_high) = StarSelection(results).plot_limits()
    assert fwhm_low == 0., 'the lower limit should stop at 0'
    assert fwhm_high > 5., 'the upper limit should still cover the curve'


def test_plot_limits_without_a_fit_use_the_points(three_star_results):
    sel = StarSelection(three_star_results[:2])
    (_, _), (fwhm_low, fwhm_high) = sel.plot_limits()
    fwhms = [r.fwhm for r in three_star_results[:2]]
    padding = 0.3 * (max(fwhms) - min(fwhms))
    assert (fwhm_low, fwhm_high) == pytest.approx((min(fwhms) - padding, max(fwhms) + padding)), \
        "with too few points to fit, the points' range should be used"


def test_plot_limits_with_no_points(three_star_results):
    sel = StarSelection(three_star_results)
    sel.remove(0, 30., 30.)
    assert sel.plot_limits() is None, 'with no points there should be no limits'
