
from dataclasses import dataclass
from pathlib import Path

from astropy.io import fits
from astropy.visualization import ImageNormalize, ZScaleInterval, LinearStretch
from IPython import embed
from matplotlib import pyplot, patches
import numpy as np

from nickel_focus import exposure
from nickel_focus import log
from nickel_focus.photometry import (
    extract_stamp, measure_quality, measure_sources, source_centroid
)
from nickel_focus import quadratic
from nickel_focus.services.base import require


class Focus:
    """
    Read and set the telescope focus (the secondary-mirror position).

    Parameters
    ----------
    poco : :class:`~nickel_focus.services.nickelpoco.NickelPoco`
        The telescope service.
    """
    def __init__(self, poco):
        self.poco = poco

    @property
    def current(self):
        """
        The current focus position (floating-point)
        """
        return self.poco.secondary_position()

    def set_to(self, focus_value):
        """
        Set the focus to the provided value.

        Movement must be enabled.  This does not check the camera; see
        :meth:`~nickel_focus.focus.FocusSequence.set_focus`, which also
        requires that no exposure is in progress.

        Parameters
        ----------
        focus_value : :obj:`int`, :obj:`float`
            The requested focus value.  Must be between 165 and 500, inclusive.
            If the requested position is already within 0.1 of the current
            value, no change is made.

        Raises
        ------
        ValueError
            Raised if the focus value is outside the allowed range, if movement
            is disabled, or if the secondary does not lock again within 30 s
            of the move.
        ~nickel_focus.services.base.KTLTimeoutError
            Raised if the telescope service doesn't reply to one of these
            checks.
        """

        # Check that the requested focus value is valid
        if focus_value < 165 or focus_value > 500:
            raise ValueError(f'Requested focus ({focus_value}) is out of range (165-500).')
        
        # Make sure movement is enabled.  Do NOT enable movement via this
        # script!
        require(
            self.poco.movement_allowed(timeout=0.5), 'Telescope movement is disabled!',
            'No reply from nickelpoco.POCSTOP; cannot tell whether telescope movement is allowed.'
        )

        # Check if the focus is already at the requested position.
        if abs(self.poco.secondary_demand() - focus_value) < .1:
            log.info(f'POCSECPD already set to {focus_value}. No change needed.')
            return

        # Change the focus
        log.info('Unlocking secondary')
        self.poco.set_secondary_lock(False)
        self.poco.read('POCSECLK')

        log.info(f'Actual position: {self.poco.secondary_position()}')
        self.poco.set_secondary_demand(focus_value)
        log.info(f'Desired position: {self.poco.secondary_demand()}')

        # TODO: If the lock doesn't come back on, explicitly set it to on?
        require(
            self.poco.secondary_locked(timeout=30),
            'POCSECLK did not turn on. Focus change failed.',
            'No reply from nickelpoco.POCSECLK; cannot tell whether the focus change finished.'
        )

        log.info(f"Successfully changed focus to {focus_value}")


class FocusPlot:
    """
    Live view of a focus sequence in progress.

    Shows the most recent exposure (with the measured source boxed, and flagged
    if its centroid looks like an outlier relative to the rest of the sequence
    collected so far), a grid of the per-exposure source stamps, and an evolving
    plot of FWHM versus focus value.

    Parameters
    ----------
    nstamps : :obj:`int`
        Number of stamps to reserve space for (an upper bound on the number of
        exposures expected in the sequence).
    ncols : :obj:`int`, optional
        Number of columns in the stamp grid.
    """
    def __init__(self, nstamps, ncols=4):
        self.ncols = ncols
        self.nrows = int(np.ceil(nstamps / ncols))

        self.fig = pyplot.figure(
            figsize=(6 + 3*self.ncols, max(3*self.nrows, 6)), constrained_layout=True
        )
        subfigs = self.fig.subfigures(1, 3, width_ratios=[1.2, self.ncols, 1.2])

        self.frame_ax = subfigs[0].subplots(1, 1)
        self.frame_ax.set_aspect('equal')

        self.stamp_axes = subfigs[1].subplots(self.nrows, self.ncols, squeeze=False)
        for ax in self.stamp_axes.flat:
            ax.axis('off')

        self.curve_ax = subfigs[2].subplots(1, 1)
        self._reset_curve_axis()

        pyplot.ion()
        pyplot.show(block=False)
        pyplot.pause(0.1)

    def _reset_curve_axis(self):
        self.curve_ax.clear()
        self.curve_ax.set_xlabel('Focus Value')
        self.curve_ax.set_ylabel('FWHM (pixels)')
        self.curve_ax.set_title('Focus Curve')
        self.curve_ax.grid(True, alpha=0.3)

    def update_frame(self, data, coords, stamp_size, label, focus_value, is_outlier):
        """Show the most recent full frame, with the measured source boxed."""
        self.frame_ax.clear()
        norm = ImageNormalize(data, interval=ZScaleInterval(), stretch=LinearStretch())
        self.frame_ax.imshow(data, cmap='viridis', origin='lower', norm=norm)
        self.frame_ax.set_xticks([])
        self.frame_ax.set_yticks([])
        self.frame_ax.set_title(f'{label}  Focus: {focus_value:.1f}')

        half = stamp_size / 2
        color = 'yellow' if is_outlier else 'red'
        rect = patches.Rectangle(
            (coords[0]-half, coords[1]-half), stamp_size, stamp_size, linewidth=2,
            edgecolor=color, facecolor='none'
        )
        self.frame_ax.add_patch(rect)
        if is_outlier:
            self.frame_ax.text(
                coords[0], coords[1]+half+5, 'Outlier centroid', color='yellow', fontsize=10,
                ha='center'
            )
        self._draw()

    def add_stamp(self, index, stamp, focus_value, fwhm, is_outlier=False):
        """Show the source stamp for the index-th exposure taken so far."""
        row, col = divmod(index, self.ncols)
        if row >= self.nrows:
            log.warning(f'No plot space left for stamp {index}; skipping.')
            return
        ax = self.stamp_axes[row, col]
        ax.clear()
        ax.axis('off')
        norm = ImageNormalize(stamp, interval=ZScaleInterval(), stretch=LinearStretch())
        ax.imshow(stamp, origin='lower', cmap='viridis', norm=norm)
        title_color = 'darkorange' if is_outlier else 'black'
        ax.set_title(f'Focus {focus_value:.1f}, FWHM {fwhm:.2f}', fontsize=9, color=title_color)
        self._draw()

    def update_curve(self, focus_values, fwhm_values):
        """
        Redraw the focus-vs-FWHM curve, including the best-fit quadratic once
        there are enough points to fit one.
        """
        self._reset_curve_axis()
        self.curve_ax.scatter(focus_values, fwhm_values, color='tab:blue')

        if len(focus_values) >= 3:
            a, b, c = quadratic.fit_quadratic(focus_values, fwhm_values)
            x_vertex, y_vertex = quadratic.vertex(a, b, c)
            x_smooth = np.linspace(min(focus_values), max(focus_values), 50)
            y_smooth = a*x_smooth**2 + b*x_smooth + c
            self.curve_ax.plot(x_smooth, y_smooth, 'r-')
            self.curve_ax.scatter(
                [x_vertex], [y_vertex], color='green', zorder=3,
                label=f'Best focus: {x_vertex:.1f}'
            )
            self.curve_ax.legend(loc='best')

        self._draw()

    def _draw(self):
        self.fig.canvas.draw_idle()
        pyplot.pause(0.001)

    @staticmethod
    def is_outlier(centroids, threshold=2.0):
        """
        Check whether the most recently added centroid in ``centroids`` is an
        outlier relative to the full set collected so far (see
        :func:`centroid_outliers`).
        """
        return bool(centroid_outliers(centroids, threshold=threshold)[-1])


def centroid_outliers(centroids, threshold=2.0):
    """
    Which of a star's ``(x, y)`` centroids (one per exposure) are outliers,
    using the median-distance statistic of the original focus-finding code:
    a centroid is an outlier if its distance from the median position exceeds
    the mean distance by more than ``threshold`` standard deviations.  With
    fewer than three centroids, none is.

    Returns
    -------
    :class:`numpy.ndarray`
        A boolean flag for each centroid.
    """
    _centroids = np.atleast_2d(centroids).astype(float)
    if len(_centroids) < 3:
        return np.zeros(len(_centroids), dtype=bool)
    median = np.median(_centroids, axis=0)
    distances = np.sqrt(np.sum((_centroids - median)**2, axis=1))
    outlier_threshold = np.mean(distances) + threshold * np.std(distances)
    return distances > outlier_threshold


@dataclass
class StepResult:
    """
    The measurement produced by one step of a focus sequence.

    Attributes
    ----------
    index : :obj:`int`
        0-based position of this step within the sequence.
    focus_value : :obj:`float`
        The focus value used for this exposure.
    exposure : :class:`pathlib.Path`
        Path to the exposure's FITS file.
    fwhm : :obj:`float` or None
        Measured image quality of the selected source: its FWHM, in pixels
        (see :func:`~nickel_focus.photometry.source_quality`), or None if the
        exposure couldn't be measured (see ``photometry_error``).
    frame : :class:`numpy.ndarray`
        Background-subtracted image data for the full exposure (if it couldn't
        be measured, the image less its median).
    centroid : :obj:`tuple` or None
        The (x,y) centroid of the selected source, or None if not measured.
    is_outlier : :obj:`bool`
        Whether this step's centroid is an outlier relative to the rest of the
        sequence collected so far; see
        :meth:`~nickel_focus.focus.FocusPlot.is_outlier`.
    object_name : :obj:`str` or None
        The exposure's ``OBJECT`` header value (see
        :func:`~nickel_focus.focus.exposure_object_name`), or None if it has
        none.
    binning : :obj:`int` or None
        The exposure's binning (1, 2, or 4; see
        :func:`~nickel_focus.focus.exposure_binning`), or None if unknown.
    raw : :class:`numpy.ndarray` or None
        The exposure's image data as read from the file (before background
        subtraction), in the file's own type -- 16-bit unsigned integers for
        the Nickel camera, a quarter of the size of ``frame`` -- or None if
        not kept.
    sources : :class:`astropy.table.Table` or None
        The measurements of every source detected in the exposure (see
        :func:`~nickel_focus.photometry.measure_sources`), or None if the
        exposure couldn't be measured or they weren't kept.  See also
        :attr:`source_positions`.
    source_index : :obj:`int` or None
        The row in ``sources`` of the selected source (whose ``fwhm`` and
        ``centroid`` these are), or None if not measured.
    photometry_error : :obj:`str` or None
        Why the exposure couldn't be measured, or None if it was (see
        :func:`measure_exposure`).
    """
    index: int
    focus_value: float
    exposure: Path
    fwhm: float | None
    frame: np.ndarray
    centroid: tuple | None
    is_outlier: bool
    object_name: str | None = None
    binning: int | None = None
    raw: np.ndarray | None = None
    sources: object = None
    source_index: int | None = None
    photometry_error: str | None = None

    @property
    def measured(self):
        """Whether the exposure's image quality was measured."""
        return self.fwhm is not None

    @property
    def source_positions(self):
        """
        The ``(x, y)`` centroids of every detected source, as an ``(N, 2)``
        array (empty if there are none; see :func:`source_centroids`).
        """
        if self.sources is None:
            return np.empty((0, 2))
        return source_centroids(self.sources)

    def stamp(self):
        """
        A cutout of ``frame`` around the selected source,
        :data:`STAMP_SIZE_FWHM` FWHMs across (see
        :func:`~nickel_focus.photometry.extract_stamp`), made when asked for;
        None if not measured.
        """
        if not self.measured:
            return None
        return extract_stamp(self.frame, self.centroid, int(self.fwhm * STAMP_SIZE_FWHM))


def source_centroids(src_data):
    """
    The ``(x, y)`` centroids of the sources in a
    :func:`~nickel_focus.photometry.measure_sources` table, as an ``(N, 2)``
    array.
    """
    return np.column_stack(
        [np.asarray(src_data['CENX'], dtype=float), np.asarray(src_data['CENY'], dtype=float)]
    )


def measure_exposure(exposure_file, method='brightest'):
    """
    Measure an exposure for a :class:`StepResult`: every source (see
    :func:`~nickel_focus.photometry.measure_sources`), then the image quality
    by ``method`` (see :func:`~nickel_focus.photometry.measure_quality`),
    without letting a failed measurement keep the image from being shown.

    If the measurement fails (e.g. no source is detected), a warning is
    logged and the image is still returned, less its median, with no
    measurement.

    Parameters
    ----------
    exposure_file : :obj:`str`, :class:`pathlib.Path`
        The exposure's FITS file.
    method : :obj:`str`, :obj:`tuple`, optional
        The photometry method (see
        :func:`~nickel_focus.photometry.image_quality`).

    Returns
    -------
    :obj:`dict`
        The :class:`StepResult` fields that come from the image: ``fwhm``,
        ``frame``, ``raw``, ``sources``, ``source_index``, ``centroid``, and
        ``photometry_error`` (None if the measurement succeeded).

    Raises
    ------
    OSError
        Raised (by :mod:`astropy.io.fits`) if the file itself can't be read.
    """
    try:
        raw, bkg, src_data = measure_sources(exposure_file)
        img_quality, row = measure_quality(src_data, method)
    except Exception as e:
        with fits.open(exposure_file) as hdu:
            raw = np.array(hdu[0].data)
        log.warning(
            f'Could not measure the image quality of {Path(exposure_file).name}: {e}  '
            'Showing it without a measurement.'
        )
        return dict(
            fwhm=None, frame=raw.astype(float) - np.median(raw), raw=raw, sources=None,
            source_index=None, centroid=None, photometry_error=str(e)
        )
    return dict(
        fwhm=img_quality, frame=raw.astype(float) - bkg, raw=raw, sources=src_data,
        source_index=row, centroid=source_centroid(src_data, row), photometry_error=None
    )


ARCSEC_PER_UNBINNED_PIXEL = 0.1665
"""The Nickel science camera's expected plate scale, per unbinned pixel."""

STAMP_SIZE_FWHM = 4.
"""
The size of a source's cutout, and of its box on a displayed image, in
FWHMs (about 9.4 times the profile's moment width).
"""


def common_binning(binnings):
    """
    The binning shared by every exposure in ``binnings`` (an iterable of
    :obj:`int` or None), or None if any is unknown or they differ (or there are
    none).
    """
    unique = set(binnings)
    if len(unique) != 1 or None in unique:
        return None
    return unique.pop()


def fwhm_arcsec(fwhm, binning):
    """
    An FWHM in pixels converted to arcseconds (see
    :data:`ARCSEC_PER_UNBINNED_PIXEL`), or None if either is unknown.
    """
    if fwhm is None or binning is None:
        return None
    return fwhm * ARCSEC_PER_UNBINNED_PIXEL * binning


def fwhm_text(fwhm, binning):
    """
    An FWHM for display: in pixels, and also in arcseconds if ``binning`` is
    known (see :data:`ARCSEC_PER_UNBINNED_PIXEL`), e.g. ``'3.21 pix (1.07
    arcsec)'`` or ``'3.21 pix'``; ``'not measured'`` if ``fwhm`` is None.
    """
    if fwhm is None:
        return 'not measured'
    text = f'{fwhm:.2f} pix'
    arcsec = fwhm_arcsec(fwhm, binning)
    if arcsec is not None:
        text += f' ({arcsec:.2f} arcsec)'
    return text


def exposure_object_name(path):
    """
    The ``OBJECT`` value in a FITS file's primary header (for a new exposure,
    what the camera's ``OBJECT`` keyword held), or None if the header has none
    or the file can't be read.
    """
    try:
        value = fits.getheader(path).get('OBJECT')
    except OSError:
        return None
    return None if value is None or str(value).strip() == '' else str(value).strip()


def exposure_binning(path):
    """
    An exposure's binning, from the scale of its "CCD pixel" WCS in the FITS
    primary header: ``CD1_1C`` and ``CD2_2C``, which should be equal and 1, 2,
    or 4.

    Returns
    -------
    :obj:`int` or None
        The binning, or None if it can't be determined -- the file can't be
        read, either keyword is missing, or the values differ or aren't 1, 2,
        or 4 -- in which case a warning is logged.
    """
    name = Path(path).name
    try:
        header = fits.getheader(path)
    except OSError as e:
        log.warning(f'Could not read the binning of {name}: {e}; treating it as unknown.')
        return None
    x, y = header.get('CD1_1C'), header.get('CD2_2C')
    if x is None or y is None:
        log.warning(
            f'Could not read the binning of {name}: its header has no CD1_1C/CD2_2C; treating '
            'it as unknown.'
        )
        return None
    if x != y or x not in (1, 2, 4):
        log.warning(
            f'Unexpected binning in {name}: CD1_1C={x}, CD2_2C={y} (expected equal values of 1, '
            '2, or 4); treating it as unknown.'
        )
        return None
    return int(x)


class FocusSequence:
    """
    Perform a focus sequence.

    This is the base class for other derived classes that specify how the focus
    sequence proceeds.

    Parameters
    ----------
    poco : :class:`~nickel_focus.services.nickelpoco.NickelPoco`, optional
        The telescope service, used to move the focus.
    cam : :class:`~nickel_focus.services.nscicam.NSciCam`, optional
        The camera service, used to take exposures.

    The hardware handles ``_focus`` (:class:`~nickel_focus.focus.Focus`) and
    ``_exposure`` (:class:`~nickel_focus.exposure.Exposure`) are built only if
    both services are given; otherwise both are None, as for sequences that
    never touch hardware (e.g., replaying exposures already on disk).

    Attributes
    ----------
    object_name : :obj:`str`
        The name of the object being observed, recorded with each exposure
        (see :meth:`~nickel_focus.focus.FocusSequence.exposure_object`);
        ``'unknown'`` unless set.
    """
    def __init__(self, poco=None, cam=None):
        connect = poco is not None and cam is not None
        self._focus = Focus(poco) if connect else None
        self._exposure = exposure.Exposure(cam) if connect else None
        self.object_name = 'unknown'
        self.method = None
        self.reset()

    def reset(self):
        self.observed_focus = []
        self.exposures = []
        self.img_quality = []
        self.binnings = []
        self.step_iter = 0
        self.centroids = []

    @property
    def binning(self):
        """
        The binning shared by every exposure collected so far, or None if any
        is unknown or they differ (see :func:`~nickel_focus.focus.common_binning`).
        """
        return common_binning(self.binnings)

    def set_focus(self, focus_value):
        """
        Move the focus to ``focus_value``, once the camera is idle.

        Every focus move a sequence makes goes through here, so the focus never
        moves during an exposure.

        Raises
        ------
        ValueError
            Raised if the camera's exposure state isn't ``Ready`` within 0.5 s,
            or if :meth:`~nickel_focus.focus.Focus.set_to` fails.
        ~nickel_focus.services.base.KTLTimeoutError
            Raised if the camera or telescope service doesn't reply to a check.
        """
        require(
            self._exposure.cam.exposure_state_is('Ready', timeout=0.5),
            'Camera exposure state not ready. Cannot change focus.',
            'No reply from nscicam.EXPSTATE; cannot tell whether the camera is idle, so the '
            'focus was not changed.'
        )
        self._focus.set_to(focus_value)

    @property
    def expected_steps(self):
        raise NotImplementedError(
            'Subclasses must report the (maximum) number of steps in the sequence, used to size '
            'the live plot.'
        )

    def step(self, method='brightest'):
        """
        Advance the focus sequence one exposure at a time.

        This is the engine shared by
        :meth:`~nickel_focus.focus.FocusSequence.execute` (used by the CLI
        script) and any other driver of the sequence (e.g., a GUI worker
        thread): each iteration sets the focus, takes or retrieves an exposure,
        measures its image quality, and yields the result.  It does not decide
        when the sequence should stop (see
        :meth:`~nickel_focus.focus.FocusSequence.continue_sequence`) or what to
        do once it has; callers are responsible for calling
        :meth:`~nickel_focus.focus.FocusSequence.reset` beforehand and for
        reacting to each :class:`~nickel_focus.focus.StepResult` as it's yielded
        (e.g., to update a plot).

        Parameters
        ----------
        method : :obj:`str`, :obj:`tuple`, optional
            The photometry method passed to
            :func:`~nickel_focus.photometry.image_quality`.

        Yields
        ------
        StepResult
            The result of the most recently completed step.

        Raises
        ------
        ValueError
            Raised after yielding an exposure that couldn't be measured (see
            :func:`measure_exposure`), which is still yielded so it can be
            shown: the sequence can't go on without its measurement.
        """
        self.method = method
        while self.continue_sequence():
            self.observed_focus += [self.step_focus()]
            self.exposures += [self.take_exposure()]
            measurement = measure_exposure(self.exposures[-1], method=method)
            self.img_quality += [measurement['fwhm']]
            self.binnings += [exposure_binning(self.exposures[-1])]
            self.centroids += [measurement['centroid']]

            result = StepResult(
                index=self.step_iter,
                focus_value=self.observed_focus[-1],
                exposure=self.exposures[-1],
                is_outlier=(
                    measurement['fwhm'] is not None and FocusPlot.is_outlier(self.centroids)
                ),
                object_name=exposure_object_name(self.exposures[-1]),
                binning=self.binnings[-1],
                **measurement,
            )
            self.step_iter += 1
            yield result
            if not result.measured:
                raise ValueError(
                    f'Could not measure {result.exposure.name} ({result.photometry_error}); '
                    'stopping the sequence.'
                )

    def reanalyze(self, method='brightest'):
        """
        Re-run photometry on every exposure this sequence has already collected,
        using a new ``method``, without taking any new exposures.

        This replaces the stored ``img_quality``/``centroids`` in place; ``observed_focus`` and ``exposures`` (and thus
        which exposures exist and what focus values they were taken at) are left
        untouched.  It's meant for interactively changing which source is used
        for the FWHM measurement (e.g., after a user clicks a different star in
        the displayed image) and seeing the effect on every exposure already
        taken, live or archived, without re-observing.  If nothing has been
        collected yet (``exposures`` is empty), this yields nothing.

        Parameters
        ----------
        method : :obj:`str`, :obj:`tuple`, optional
            The photometry method passed to
            :func:`~nickel_focus.photometry.image_quality`.

        Yields
        ------
        StepResult
            The updated result for each already-collected exposure, in the order
            they were originally taken.

        Raises
        ------
        ValueError
            Raised after yielding an exposure that couldn't be measured, as
            for :meth:`~nickel_focus.focus.FocusSequence.step`.
        """
        self.method = method
        focus_values = list(self.observed_focus)
        exposures = list(self.exposures)

        self.img_quality = []
        self.centroids = []

        for i, (focus_value, exposure_file) in enumerate(zip(focus_values, exposures)):
            measurement = measure_exposure(exposure_file, method=method)
            self.img_quality += [measurement['fwhm']]
            self.centroids += [measurement['centroid']]

            result = StepResult(
                index=i,
                focus_value=focus_value,
                exposure=exposure_file,
                is_outlier=(
                    measurement['fwhm'] is not None and FocusPlot.is_outlier(self.centroids)
                ),
                object_name=exposure_object_name(exposure_file),
                # Already known from when the exposure was collected.
                binning=(
                    self.binnings[i] if i < len(self.binnings)
                    else exposure_binning(exposure_file)
                ),
                **measurement,
            )
            yield result
            if not result.measured:
                raise ValueError(
                    f'Could not measure {Path(exposure_file).name} ({result.photometry_error}); '
                    'stopping the reanalysis.'
                )

    def take_single_exposure(self, focus_value, method='brightest', **exp_kwargs):
        """
        Move to ``focus_value``, take one exposure, and measure its image
        quality -- one iteration of
        :meth:`~nickel_focus.focus.FocusSequence.step`'s body, without any
        sequence bookkeeping (``step_iter``, ``observed_focus``,
        ``img_quality``, etc.  are left untouched).

        This backs the GUI's Single tab, which takes one exposure at any focus
        value; moving to a fitted best focus (the tab's default value) is just
        one use of it.

        Parameters
        ----------
        focus_value : :obj:`int`, :obj:`float`
            The focus value to move to before exposing.
        method : :obj:`str`, :obj:`tuple`, optional
            The photometry method passed to
            :func:`~nickel_focus.photometry.image_quality`.
        **exp_kwargs
            Passed to :meth:`~nickel_focus.services.nscicam.ExposureConfig.configure`
            before exposing (``record``, ``speed``, ``binning``, ``exptime``).

        Returns
        -------
        StepResult
            The measurement from the single exposure.  Its ``index`` is always
            0, since it's not part of any larger sequence.  If the exposure
            couldn't be measured, it's still returned, with no measurement
            (see :func:`measure_exposure`).
        """
        if self._focus is None or self._exposure is None:
            raise ValueError(
                'No telescope focus/exposure control is available (no telescope/camera '
                'connection); cannot take a single exposure.'
            )
        self.set_focus(focus_value)
        self._exposure.cfg.configure(**exp_kwargs)
        exposure_file = self.take_exposure()
        return StepResult(
            index=0,
            focus_value=self._focus.current,
            exposure=exposure_file,
            is_outlier=False,
            object_name=exposure_object_name(exposure_file),
            binning=exposure_binning(exposure_file),
            **measure_exposure(exposure_file, method=method),
        )

    def execute(self, goto=True, method='brightest', plot=True, **exp_kwargs):

        if self._exposure is not None:
            self._exposure.cfg.configure(**exp_kwargs)
        self.reset()

        self.plot = FocusPlot(self.expected_steps) if plot else None

        for result in self.step(method=method):
            # An exposure that couldn't be measured ends the sequence (step
            # raises next); there's nothing to plot for it.
            if self.plot is not None and result.measured:
                self.plot.update_frame(
                    result.frame, result.centroid, int(result.fwhm * STAMP_SIZE_FWHM),
                    result.exposure.stem,
                    result.focus_value, result.is_outlier
                )
                # Cut only now, for the plot.
                self.plot.add_stamp(
                    result.index, result.stamp(), result.focus_value, result.fwhm,
                    is_outlier=result.is_outlier
                )
                self.plot.update_curve(self.observed_focus, self.img_quality)

        best_focus, best_img_quality = self.fit_best_focus(self.observed_focus, self.img_quality)

        if goto:
            self.take_single_exposure(best_focus, method=method)

        return best_focus, best_img_quality

    def continue_sequence(self):
        raise NotImplementedError(
            'Method used to check if the sequence should continue is not implemented by the '
            'base class.'
        )

    def step_focus(self):
        raise NotImplementedError(
            'Method used to increment the focus setting is not implemented by the base class!'
        )

    OBJECT_MAX_LENGTH = 68
    """
    The longest ``OBJECT`` written: the most a FITS header string value can
    hold.
    """

    def exposure_object(self):
        """
        The camera's ``OBJECT`` for the next exposure: the object name and the
        current focus position, ``'<object_name>: focus=<focus>'`` (focus to
        one decimal place).

        At most :attr:`OBJECT_MAX_LENGTH` characters: a longer object name is
        shortened, ending in ``'...'``, so the focus is always kept.
        """
        suffix = f': focus={self._focus.current:.1f}'
        name = self.object_name
        room = self.OBJECT_MAX_LENGTH - len(suffix)
        if len(name) > room:
            name = name[:room - 3] + '...'
        return name + suffix

    def take_exposure(self):
        """
        Take an exposure at the current focus, with ``OBJECT`` set by
        :meth:`~nickel_focus.focus.FocusSequence.exposure_object`, and return
        its file path.
        """
        self._exposure.expose(object_name=self.exposure_object())
        return self._exposure.path.previous

    @staticmethod
    def fit_best_focus(focus, img_quality):
        """
        Fit a quadratic to image quality vs. focus and return its vertex,
        ``(best_focus, best_img_quality)``.  Exposures that couldn't be
        measured (image quality None) are left out.

        Raises
        ------
        ValueError
            Raised if fewer than three measured exposures remain.
        """
        pairs = [(f, q) for f, q in zip(focus, img_quality) if q is not None]
        focus = [f for f, _ in pairs]
        img_quality = [q for _, q in pairs]
        if len(focus) < 3:
            raise ValueError('Insufficient number of focus values observed.')
        a, b, c = quadratic.fit_quadratic(focus, img_quality)
        return quadratic.vertex(a, b, c)


class GridFocusSequence(FocusSequence):
    def __init__(self, start, step, end=None, nstep=None, poco=None, cam=None):

        super().__init__(poco=poco, cam=cam)

        if end is None and nstep is None:
            raise ValueError('Must provide either the ending value or the number of steps.')
        self.nstep = nstep if end is None else int((end-start)/step+1)
        _end = start + (self.nstep-1)*step
        self.target_focus = np.round(np.linspace(start, _end, self.nstep))

    @property
    def expected_steps(self):
        return self.nstep

    def continue_sequence(self):
        return self.step_iter < self.nstep

    def step_focus(self):
        self.set_focus(self.target_focus[self.step_iter])
        return self._focus.current


class AutomatedFocusSequence(FocusSequence):
    def __init__(self, start, step, maxsteps=12, poco=None, cam=None):

        if maxsteps is None or maxsteps < 2:
            raise ValueError('maxsteps cannot be None and must be at least 2.')

        super().__init__(poco=poco, cam=cam)

        self.start = start
        # NOTE: Named `step_size`, not `step`, because `step` is the name
        # of the generator method inherited from FocusSequence -- an
        # instance attribute of the same name would shadow it entirely
        # (seq.step(...) would try to call a float).
        self.step_size = step
        self.direction = None
        self.last = None
        self.maxsteps = maxsteps

    def reset(self):
        super().reset()
        self.direction = None
        self.last = None

    @property
    def expected_steps(self):
        return self.maxsteps

    def continue_sequence(self):
        return (
            self.step_iter < self.maxsteps
            and (
                self.step_iter < 2 or self.last is None
                or (self.last is not None and self.step_iter < self.last)
            )
        )

    def step_focus(self):
        if self.step_iter == 0:
            next_focus = self.start
        elif self.step_iter == 1:
            next_focus = self.start + self.step_size
        elif self.step_iter == 2 and self.img_quality[0] > self.img_quality[1]:
            self.direction = 1
            next_focus = self.observed_focus[1] + self.step_size
        elif self.step_iter == 2 and self.img_quality[0] < self.img_quality[1]:
            self.direction = -1
            next_focus = self.observed_focus[0] - self.step_size
        elif (
            self.last is None and self.step_iter > 2
            and self.img_quality[-1] > self.img_quality[-2]
        ):
            self.last = self.step_iter + 2
            if self.last > self.maxsteps:
                log.warning(
                    f'Number of steps to fulfill sequence ({self.last}) is more than the '
                    f'maximum number of steps requested ({self.maxsteps}).'
                )
            next_focus = self.observed_focus[-1] + self.direction * self.step_size
        else:
            next_focus = self.observed_focus[-1] + self.direction * self.step_size
        self.set_focus(next_focus)
        return self._focus.current


class ArchiveFocusSequence(FocusSequence):
    def __init__(self, observed_focus, exposures):
        # Only replays exposures already on disk, so never needs hardware.
        super().__init__()

        self._observed_focus = observed_focus
        self._exposures = exposures
        self.nstep = len(self._observed_focus)

    @property
    def expected_steps(self):
        return self.nstep

    def continue_sequence(self):
        return self.step_iter < self.nstep

    def step_focus(self):
        return self._observed_focus[self.step_iter]

    def take_exposure(self):
        return self._exposures[self.step_iter]

