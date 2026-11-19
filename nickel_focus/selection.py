"""
The stars whose measurements make up the focus curve, chosen from the
photometry already kept for each exposure (no remeasurement).

A :class:`StarSelection` covers one sequence's (or one Single exposure's)
exposures, as :class:`~nickel_focus.focus.StepResult` objects in acquisition
order.  It starts from the *first star*: the source each exposure's
measurement selected (the brightest, or the one nearest a marked position).
Stars can then be added, matched by position across the exposures, or
removed, and the pooled measurements of every selected star fit together.
"""
from dataclasses import dataclass

import numpy as np

from nickel_focus import quadratic
from nickel_focus.focus import centroid_outliers
from nickel_focus.photometry import source_centroid, source_quality
from nickel_focus.pointing import SNAP_RADIUS, nearest_source

MATCH_RADIUS = 10.
"""
How far, in (binned) pixels, a star may move between exposures and still be
matched as the same star.  Probably more than needed.
"""

LIMIT_PADDING = 0.3
"""
How far the focus curve's fixed FWHM range extends beyond the best fit's
range, above and below, as a fraction of that range (see
:meth:`StarSelection.plot_limits`).
"""

FOCUS_MARGIN = 0.05
"""
The margin added on each side of the focus values' range for the focus
curve's fixed focus range, as a fraction of that range (matplotlib's default).
"""


@dataclass
class Star:
    """
    One selected star: its source in each exposure.

    Attributes
    ----------
    rows : :obj:`list`
        For each exposure, the row of the star's source in that exposure's
        source table, or None if it has no match there.
    outliers : :obj:`list`
        For each exposure, whether the star's position there is an outlier
        (see :func:`~nickel_focus.focus.centroid_outliers`).
    first : :obj:`bool`
        Whether this is the first star (each exposure's own selected source).
    """
    rows: list
    outliers: list
    first: bool = False


@dataclass(frozen=True)
class StarPoint:
    """
    One measurement of one selected star.

    Attributes
    ----------
    exposure : :obj:`int`
        The exposure's position in :attr:`StarSelection.results`.
    focus_value : :obj:`float`
        The exposure's focus value.
    fwhm : :obj:`float`
        The star's image quality in that exposure.
    centroid : :obj:`tuple`
        The star's ``(x, y)`` position in that exposure.
    is_outlier : :obj:`bool`
        Whether that position is an outlier for the star.
    """
    exposure: int
    focus_value: float
    fwhm: float
    centroid: tuple
    is_outlier: bool


class StarSelection:
    """
    The selected stars for a set of exposures, and their pooled fit.

    Parameters
    ----------
    results : iterable, optional
        The exposures' :class:`~nickel_focus.focus.StepResult` objects, in
        acquisition order.  More can be added with :meth:`append`.  The
        selection starts with the first star alone.

    Attributes
    ----------
    results : :obj:`list`
        The exposures.
    stars : :obj:`list`
        The selected :class:`Star` objects.
    """

    def __init__(self, results=()):
        self.results = []
        self.stars = [Star(rows=[], outliers=[], first=True)]
        for result in results:
            self.append(result)

    def append(self, result):
        """
        Add an exposure (e.g. as a sequence takes it).  The first star uses
        the exposure's own selected source and outlier flag; any other star is
        matched to it by position.
        """
        self.results.append(result)
        index = len(self.results) - 1
        for star in self.stars:
            if star.first:
                star.rows.append(result.source_index)
                star.outliers.append(bool(result.is_outlier))
            else:
                star.rows.append(self._match(index, self._reference_position(star)))
                star.outliers = self._outliers(star.rows)

    def replace(self, index, result):
        """
        Replace an exposure's result (e.g. remeasured), keeping its place.
        The first star uses its selected source and outlier flag; any other
        star is matched again.
        """
        self.results[index] = result
        for star in self.stars:
            if star.first:
                star.rows[index] = result.source_index
                star.outliers[index] = bool(result.is_outlier)
            else:
                star.rows[index] = self._match(index, self._reference_position(star))
                star.outliers = self._outliers(star.rows)

    # -- choosing stars --------------------------------------------------------

    def add(self, index, x, y):
        """
        Add the star nearest ``(x, y)`` in exposure ``index``, matched in every
        other exposure to the nearest source within :data:`MATCH_RADIUS` of
        its position.  If a selected star already uses that source in that
        exposure, that star is replaced by the new match instead.

        Returns
        -------
        action : :obj:`str`
            ``'added'`` or ``'replaced'``.
        position : :obj:`tuple`
            The star's ``(x, y)`` in exposure ``index``.
        matched : :obj:`int`
            In how many exposures the star was found (including ``index``).

        Raises
        ------
        ValueError
            Raised if exposure ``index`` has no measured source within
            :data:`~nickel_focus.pointing.SNAP_RADIUS` of ``(x, y)``.
        """
        row, position = self._snap(index, x, y)
        rows = [
            row if i == index else self._match(i, position)
            for i in range(len(self.results))
        ]
        star = Star(rows=rows, outliers=self._outliers(rows))
        existing = self._star_using(index, row)
        if existing is None:
            self.stars.append(star)
            action = 'added'
        else:
            self.stars[self.stars.index(existing)] = star
            action = 'replaced'
        return action, position, sum(r is not None for r in rows)

    def remove(self, index, x, y):
        """
        Remove the selected star whose source in exposure ``index`` is nearest
        ``(x, y)``.  Any star may be removed, including the first.

        Returns
        -------
        :obj:`tuple`
            The removed star's ``(x, y)`` in exposure ``index``.

        Raises
        ------
        ValueError
            Raised if there's no measured source near ``(x, y)``, or the star
            there isn't selected.
        """
        row, position = self._snap(index, x, y)
        existing = self._star_using(index, row)
        if existing is None:
            raise ValueError(
                f'The star at ({position[0]:.1f}, {position[1]:.1f}) is not in the plotted data.'
            )
        self.stars.remove(existing)
        return position

    # -- the selected data -----------------------------------------------------

    def points(self):
        """Every selected star's measurements, pooled, as :class:`StarPoint` objects."""
        return [point for star in self.stars for point in self._star_points(star)]

    def stars_in(self, index):
        """The selected stars' measurements in exposure ``index``."""
        return [p for p in self.points() if p.exposure == index]

    def mean_fwhm(self, index):
        """The mean image quality of the selected stars in exposure ``index``, or None."""
        points = self.stars_in(index)
        if not points:
            return None
        return float(np.mean([p.fwhm for p in points]))

    def fit(self):
        """
        Fit one quadratic to the pooled measurements of every selected star.

        Returns
        -------
        :obj:`tuple`
            ``(a, b, c, best_focus, best_fwhm)``: the quadratic's coefficients
            and its vertex.

        Raises
        ------
        ValueError
            Raised if there are no measurements, or fewer than three.
        """
        points = self.points()
        if not points:
            raise ValueError('No data to fit')
        if len(points) < 3:
            raise ValueError(f'Too few points to fit ({len(points)}; at least 3 are needed)')
        a, b, c = quadratic.fit_quadratic(
            [p.focus_value for p in points], [p.fwhm for p in points]
        )
        best_focus, best_fwhm = quadratic.vertex(a, b, c)
        return a, b, c, best_focus, best_fwhm

    def plot_limits(self):
        """
        Fixed limits for the focus curve, so they don't change as stars are
        added or removed, based on the current best fit (the Controller asks
        when a sequence finishes, when the fit is the first star's).

        The FWHM range is the fitted curve's full range over the plotted
        focus values (its minimum to its highest point), extended by
        :data:`LIMIT_PADDING` of that range above and below, but never below
        0.  Without a fit (fewer than three points), the measured points'
        range is used instead.  The focus range is the measured exposures'
        focus values, with :data:`FOCUS_MARGIN` on each side.

        Returns
        -------
        :obj:`tuple` or None
            ``((focus_low, focus_high), (fwhm_low, fwhm_high))``, or None if
            there are no points.
        """
        points = self.points()
        if not points:
            return None
        point_focus = [p.focus_value for p in points]
        try:
            a, b, c, *_ = self.fit()
        except (ValueError, ZeroDivisionError):
            fwhms = np.array([p.fwhm for p in points])
        else:
            # The curve as drawn: over the plotted focus values.
            x = np.linspace(min(point_focus), max(point_focus), 200)
            fwhms = a * x**2 + b * x + c
        low, high = float(np.min(fwhms)), float(np.max(fwhms))
        padding = LIMIT_PADDING * (high - low)
        if padding == 0:
            # A flat range (e.g. a single point).
            padding = 0.1 * abs(high) or 1.
        measured = [r.focus_value for r in self.results if r.measured]
        focus_low, focus_high = min(measured), max(measured)
        margin = FOCUS_MARGIN * (focus_high - focus_low) or 1.
        return (
            (float(focus_low - margin), float(focus_high + margin)),
            (max(0., low - padding), high + padding),
        )

    # -- internals ---------------------------------------------------------------

    def _star_points(self, star):
        points = []
        for i, (row, outlier) in enumerate(zip(star.rows, star.outliers)):
            result = self.results[i]
            if star.first:
                # The exposure's own measurement (which may combine sources).
                if not result.measured:
                    continue
                fwhm, centroid = result.fwhm, result.centroid
            else:
                if row is None or result.sources is None:
                    continue
                fwhm = source_quality(result.sources, row)
                centroid = source_centroid(result.sources, row)
            points.append(StarPoint(i, result.focus_value, fwhm, centroid, bool(outlier)))
        return points

    def _snap(self, index, x, y):
        """The row and position of the measured source nearest ``(x, y)``."""
        result = self.results[index]
        positions = result.source_positions
        position = nearest_source(positions, x, y)
        if position is None:
            raise ValueError(f'No star found within {SNAP_RADIUS:.0f} pixels of ({x:.1f}, {y:.1f}).')
        row = int(np.argmin(np.hypot(positions[:, 0] - position[0], positions[:, 1] - position[1])))
        return row, position

    def _match(self, index, position):
        """The row of the source within MATCH_RADIUS of ``position`` in exposure ``index``."""
        if position is None:
            return None
        positions = self.results[index].source_positions
        match = nearest_source(positions, *position, radius=MATCH_RADIUS)
        if match is None:
            return None
        return int(np.argmin(np.hypot(positions[:, 0] - match[0], positions[:, 1] - match[1])))

    def _reference_position(self, star):
        """The star's mean position over the exposures it's matched in, or None."""
        positions = [
            source_centroid(self.results[i].sources, row)
            for i, row in enumerate(star.rows) if row is not None
        ]
        return tuple(np.mean(positions, axis=0)) if positions else None

    def _outliers(self, rows):
        """Each exposure's outlier flag for a star with these rows (matched ones only)."""
        matched = [i for i, row in enumerate(rows) if row is not None]
        flags = [False] * len(rows)
        if matched:
            centroids = [source_centroid(self.results[i].sources, rows[i]) for i in matched]
            for i, flag in zip(matched, centroid_outliers(centroids)):
                flags[i] = bool(flag)
        return flags

    def _star_using(self, index, row):
        """The selected star whose source in exposure ``index`` is ``row``, or None."""
        return next((star for star in self.stars if star.rows[index] == row), None)
