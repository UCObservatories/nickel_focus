"""
Live FWHM-vs-focus plot for a focus sequence.
"""
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from nickel_focus import quadratic
from nickel_focus.focus import ARCSEC_PER_UNBINNED_PIXEL, common_binning
from nickel_focus.gui.qt import QtWidgets


class FocusCurvePanel(QtWidgets.QWidget):
    """
    Scatter of FWHM versus focus value, with the best-fit quadratic and its
    vertex (an open black star) overlaid once there are enough points to fit
    one (matching :meth:`~nickel_focus.focus.FocusSequence.fit_best_focus`'s
    own 3-point minimum).  The points are the selected stars' pooled
    measurements (see
    :meth:`~nickel_focus.gui.views.focus_curve_panel.FocusCurvePanel.set_points`),
    all drawn alike and fit together; outliers (position outliers, see
    :func:`~nickel_focus.focus.centroid_outliers`) are drawn as a distinct
    color/marker.

    The plot limits are set by the measured points alone while a sequence
    runs, and the fitted curve and best focus are drawn within them, and may
    fall outside.  Once the sequence (or a remeasurement) finishes, the
    Controller fixes them (see
    :meth:`~nickel_focus.gui.views.focus_curve_panel.FocusCurvePanel.fix_limits`),
    so adding and removing stars doesn't move them; :meth:`reset` releases
    them.

    The left axis is FWHM in pixels; the right axis (:attr:`ax_arcsec`, a
    ``twinx`` of :attr:`ax`) gives the same in arcseconds, at
    :data:`~nickel_focus.focus.ARCSEC_PER_UNBINNED_PIXEL` times the exposures' binning
    (:attr:`~nickel_focus.focus.StepResult.binning`), when every point's
    binning is known and the same.  Otherwise it repeats the left axis, in
    pixels.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # The plotted points (each with focus_value, fwhm, is_outlier), and
        # each one's binning.
        self._results = []
        self._binnings = []
        # ((focus_low, focus_high), (fwhm_low, fwhm_high)), or None to follow
        # the points (see fix_limits).
        self._fixed_limits = None

        # 'constrained' layout recomputes margins on every draw so the
        # axis labels/title always have enough room, regardless of how
        # small the splitter/window makes this panel -- unlike a fixed
        # margin, which gets clipped once the panel shrinks below it.
        self.figure = Figure(figsize=(5, 4), layout='constrained')
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax_arcsec = self.ax.twinx()
        self._reset_axis()

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.canvas)

    def add_result(self, result):
        """
        Add one more :class:`~nickel_focus.focus.StepResult` and redraw.  A
        result that couldn't be measured (``fwhm`` None) has no point, so it's
        ignored.
        """
        if not result.measured:
            return
        self._results.append(result)
        self._binnings.append(result.binning)
        self._render()

    def set_points(self, points, binnings):
        """
        Replace the plotted points and redraw.

        Parameters
        ----------
        points : :obj:`list`
            The points: objects with ``focus_value``, ``fwhm``, and
            ``is_outlier`` (e.g. :class:`~nickel_focus.selection.StarPoint`).
        binnings : :obj:`list`
            Each point's exposure binning (an :obj:`int` or None), for the
            right axis.
        """
        self._results = list(points)
        self._binnings = list(binnings)
        self._render()

    def fix_limits(self, focus_limits, fwhm_limits):
        """
        Fix the plot limits (e.g. once a sequence has finished), so they no
        longer follow the points; :meth:`reset` releases them.

        Parameters
        ----------
        focus_limits, fwhm_limits : :obj:`tuple`
            ``(low, high)`` for the focus (x) and FWHM in pixels (left y) axes.
        """
        self._fixed_limits = (tuple(focus_limits), tuple(fwhm_limits))
        self._render()

    def reset(self):
        """Clear the plot -- no points, no fitted curve -- and release fixed limits."""
        self._results = []
        self._binnings = []
        self._fixed_limits = None
        self._reset_axis()
        self.canvas.draw_idle()

    def _reset_axis(self):
        """
        Clear the plot and redraw its static decorations (labels, title, grid)
        -- everything
        :meth:`~nickel_focus.gui.views.focus_curve_panel.FocusCurvePanel._render`
        would otherwise have to re-apply after every
        :meth:`matplotlib.axes.Axes.clear`, factored out since both
        :meth:`~nickel_focus.gui.views.focus_curve_panel.FocusCurvePanel._render`
        and
        :meth:`~nickel_focus.gui.views.focus_curve_panel.FocusCurvePanel.reset`
        need exactly this.
        """
        self.ax.clear()
        self.ax.set_xlabel('Focus Value')
        self.ax.set_ylabel('FWHM (pixels)')
        self.ax.set_title('Focus Curve')
        self.ax.grid(True, alpha=0.3)
        self.ax_arcsec.clear()
        # Clearing a twinx axis puts its ticks and label back on the left.
        self.ax_arcsec.yaxis.tick_right()
        self.ax_arcsec.yaxis.set_label_position('right')
        self.ax_arcsec.set_ylabel('FWHM (pixels)')

    def _render(self):
        """
        Redraw the whole plot from
        :attr:`~nickel_focus.gui.views.focus_curve_panel.FocusCurvePanel._results`:
        points, plus the fitted curve/vertex if enough exist.
        """
        self._reset_axis()
        if not self._results and self._fixed_limits is None:
            self.canvas.draw_idle()
            return

        normal = [r for r in self._results if not r.is_outlier]
        outliers = [r for r in self._results if r.is_outlier]

        if normal:
            self.ax.scatter(
                [r.focus_value for r in normal], [r.fwhm for r in normal], color='tab:blue',
                marker='o', label='Measured'
            )
        if outliers:
            self.ax.scatter(
                [r.focus_value for r in outliers], [r.fwhm for r in outliers], color='gold',
                marker='x', label='Outlier'
            )

        # The fixed limits, or else those of the measured points alone; either
        # way, set now, so the fitted curve and best focus drawn below can't
        # change them.
        if self._fixed_limits is not None:
            self.ax.set_xlim(*self._fixed_limits[0])
            self.ax.set_ylim(*self._fixed_limits[1])
        else:
            self.ax.autoscale_view()
        self.ax.set_autoscale_on(False)
        self._set_right_axis()

        focus_values = [r.focus_value for r in self._results]
        fwhm_values = [r.fwhm for r in self._results]
        if len(self._results) >= 3:
            try:
                a, b, c = quadratic.fit_quadratic(focus_values, fwhm_values)
                x_vertex, y_vertex = quadratic.vertex(a, b, c)
            except (ValueError, ZeroDivisionError):
                pass
            else:
                x_smooth = np.linspace(min(focus_values), max(focus_values), 50)
                y_smooth = a*x_smooth**2 + b*x_smooth + c
                self.ax.plot(x_smooth, y_smooth, 'r-')
                self.ax.scatter(
                    [x_vertex], [y_vertex], marker='*', s=200, facecolors='none',
                    edgecolors='black', zorder=3, label=f'Best focus: {x_vertex:.1f}'
                )

        if self._results:
            self.ax.legend(loc='best')
        self.canvas.draw_idle()

    def _set_right_axis(self):
        """
        Match the right-hand FWHM axis to the left axis's (already fixed)
        limits: in arcseconds if every result has the same known binning,
        otherwise in pixels, repeating the left axis.
        """
        binning = common_binning(self._binnings)
        if binning is not None:
            scale = ARCSEC_PER_UNBINNED_PIXEL * binning
            self.ax_arcsec.set_ylabel('FWHM (arcsec)')
        else:
            scale = 1.
        low, high = self.ax.get_ylim()
        self.ax_arcsec.set_ylim(low * scale, high * scale)
