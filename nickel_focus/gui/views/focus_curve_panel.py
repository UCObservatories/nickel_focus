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
    Scatter of FWHM versus focus value for a sequence's results so far, with the
    best-fit quadratic and its vertex (an open black star) overlaid once there
    are enough points to fit one (matching
    :meth:`~nickel_focus.focus.FocusSequence.fit_best_focus`'s own 3-point
    minimum). Outlier points (:attr:`~nickel_focus.focus.StepResult.is_outlier`)
    are drawn as a distinct color/marker from normal ones.

    The plot limits are set by the measured points alone: the fitted curve and
    best focus are drawn within them, and may fall outside.

    The left axis is FWHM in pixels; the right axis (:attr:`ax_arcsec`, a
    ``twinx`` of :attr:`ax`) gives the same in arcseconds, at
    :data:`~nickel_focus.focus.ARCSEC_PER_UNBINNED_PIXEL` times the exposures' binning
    (:attr:`~nickel_focus.focus.StepResult.binning`), when every point's
    binning is known and the same.  Otherwise it repeats the left axis, in
    pixels.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results = []

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
        """Add one more :class:`~nickel_focus.focus.StepResult` and redraw."""
        self._results.append(result)
        self._render()

    def reset(self):
        """Clear the plot: no points, no fitted curve."""
        self._results = []
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
        if not self._results:
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

        # The limits come from the measured points alone: fix them now, so
        # the fitted curve and best focus drawn below can't change them.
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

        self.ax.legend(loc='best')
        self.canvas.draw_idle()

    def _set_right_axis(self):
        """
        Match the right-hand FWHM axis to the left axis's (already fixed)
        limits: in arcseconds if every result has the same known binning,
        otherwise in pixels, repeating the left axis.
        """
        binning = common_binning(r.binning for r in self._results)
        if binning is not None:
            scale = ARCSEC_PER_UNBINNED_PIXEL * binning
            self.ax_arcsec.set_ylabel('FWHM (arcsec)')
        else:
            scale = 1.
        low, high = self.ax.get_ylim()
        self.ax_arcsec.set_ylim(low * scale, high * scale)
