"""
Pointing corrections measured from an exposure: the on-sky offset that would
move a chosen star to the center of the image.

Pixel coordinates are 0-based, with each pixel's center at an integer (as in
:func:`~nickel_focus.photometry.image_quality`'s centroids).  On the Nickel
camera, RA and Dec both increase toward *decreasing* pixel number: East is at
low column numbers and North at low row numbers.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nickel_focus.focus import ARCSEC_PER_UNBINNED_PIXEL

MAX_POINTING_OFFSET = 300.
"""
The largest pointing offset accepted, in arcseconds (5 arcmin).  A larger
one is more likely the wrong star than a pointing error.  Unverified: to be
confirmed at the telescope.
"""

SNAP_RADIUS = 10.
"""
How far, in (binned) pixels, a detected star may be from the cursor to be
chosen as the star to center (see :func:`nearest_source`).
"""


@dataclass(frozen=True)
class PointingOffset:
    """
    The offset that moves a star in an exposure to the center of the image.

    Attributes
    ----------
    star : :obj:`tuple`
        The star's ``(x, y)`` pixel position.
    center : :obj:`tuple`
        The image center's ``(x, y)`` pixel position (see
        :func:`image_center`).
    east : :obj:`float`
        The offset to apply, in arcseconds on the sky: positive points the
        telescope further East.
    north : :obj:`float`
        The offset to apply, in arcseconds: positive points the telescope
        further North.
    exposure : :class:`pathlib.Path` or None
        The exposure the star was marked on.
    """
    star: tuple
    center: tuple
    east: float
    north: float
    exposure: Path | None = None

    @property
    def size(self):
        """The total offset, in arcseconds."""
        return float(np.hypot(self.east, self.north))

    def text(self):
        """The offset for display, e.g. ``'12.3″ E, 4.5″ N'``."""
        return offset_text(self.east, self.north)


def offset_text(east, north):
    """
    An offset for display, in arcseconds with one decimal and a compass
    direction for each axis, e.g. ``'12.3″ E, 4.5″ N'`` or ``'3.0″ W, 0.2″ S'``.
    """
    east_dir = 'E' if east >= 0 else 'W'
    north_dir = 'N' if north >= 0 else 'S'
    return f'{abs(east):.1f}″ {east_dir}, {abs(north):.1f}″ {north_dir}'


def image_center(shape):
    """
    The geometric center of an image of ``shape`` ``(ny, nx)``, as ``(x, y)``
    in 0-based pixel coordinates.  For an even size this falls on the edge
    between the two middle pixels (e.g. ``x = 1043.5`` for 2088 columns).
    """
    ny, nx = shape
    return ((nx - 1) / 2, (ny - 1) / 2)


def nearest_source(sources, x, y, radius=SNAP_RADIUS):
    """
    The detected source nearest ``(x, y)``, if one lies within ``radius``.

    Parameters
    ----------
    sources : :class:`numpy.ndarray` or None
        The ``(x, y)`` centroids of the detected sources, as an ``(N, 2)``
        array (see :attr:`~nickel_focus.focus.StepResult.source_positions`).
    x, y : :obj:`float`
        The position to search from, in pixels.
    radius : :obj:`float`, optional
        The largest distance accepted, in pixels.

    Returns
    -------
    :obj:`tuple` or None
        The nearest source's ``(x, y)``, or None if there are no sources or
        none within ``radius``.
    """
    if sources is None or len(sources) == 0:
        return None
    sources = np.asarray(sources, dtype=float)
    distance = np.hypot(sources[:, 0] - x, sources[:, 1] - y)
    nearest = int(np.argmin(distance))
    if distance[nearest] > radius:
        return None
    return float(sources[nearest, 0]), float(sources[nearest, 1])


def pointing_offset(star, shape, binning, exposure=None, max_offset=MAX_POINTING_OFFSET):
    """
    The pointing offset that would move ``star`` to the center of the image.

    The telescope must point at the star, so each offset is the star's
    distance from the center, in the sky direction: since RA and Dec increase
    toward decreasing pixel number, ``east = -(x - x0)*scale`` and
    ``north = -(y - y0)*scale``, with ``scale`` the plate scale
    (:data:`~nickel_focus.focus.ARCSEC_PER_UNBINNED_PIXEL` times the
    binning).

    Parameters
    ----------
    star : :obj:`tuple`
        The star's ``(x, y)`` pixel position.
    shape : :obj:`tuple`
        The image's ``(ny, nx)`` shape.
    binning : :obj:`int` or None
        The exposure's binning (see :func:`~nickel_focus.focus.exposure_binning`).
    exposure : :class:`pathlib.Path`, optional
        The exposure, recorded in the result.
    max_offset : :obj:`float`, optional
        The largest total offset accepted, in arcseconds.

    Returns
    -------
    :class:`PointingOffset`
        The offset.

    Raises
    ------
    ValueError
        Raised if the binning is unknown, or the offset is larger than
        ``max_offset``.
    """
    if binning is None:
        raise ValueError(
            'the exposure\'s binning is unknown, so its pixel scale (and the offset) can\'t be '
            'determined'
        )
    scale = ARCSEC_PER_UNBINNED_PIXEL * binning
    x0, y0 = image_center(shape)
    x, y = star
    offset = PointingOffset(
        star=(float(x), float(y)), center=(x0, y0), east=-(x - x0) * scale,
        north=-(y - y0) * scale, exposure=exposure
    )
    if offset.size > max_offset:
        raise ValueError(
            f'the offset ({offset.text()}, {offset.size:.1f}″ in all) is larger than the '
            f'{max_offset:.0f}″ limit; is this the right star?'
        )
    return offset
