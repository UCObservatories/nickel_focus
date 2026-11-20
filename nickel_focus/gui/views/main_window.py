"""
Top-level window laying out the panels: the Quit button and status indicators
above the exposure display on the left, and the focus curve above the control
tabs on the right.
"""
from nickel_focus.gui.qt import QtCore, QtWidgets
from nickel_focus.gui.views.image_panel import ImagePanel
from nickel_focus.gui.views.focus_curve_panel import FocusCurvePanel
from nickel_focus.gui.views.focus_control_panel import FocusControlPanel
from nickel_focus.gui.views.status_bar import StatusBar


class MainWindow(QtWidgets.QMainWindow):
    """
    Lays out :class:`~nickel_focus.gui.views.status_bar.StatusBar` above
    :class:`~nickel_focus.gui.views.image_panel.ImagePanel` (left),
    :class:`~nickel_focus.gui.views.focus_curve_panel.FocusCurvePanel` (top
    right), and
    :class:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel`
    (bottom right), with user-draggable splitters between the left and right
    sides and between the two right-hand panels.  Purely structural --
    wiring the panels together is :class:`~nickel_focus.gui.controller.Controller`'s
    job, not this class's.
    """

    #: Layout spacing, in pixels, between the status bar and the image
    #: panel's Exposure row below it.
    _STATUS_BAR_SPACING = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Nickel Focus GUI')
        # Guards the one-time image-panel-square adjustment in
        # `showEvent` -- see there for why it can't just happen here.
        self._sized_image_panel = False

        # Each of these is a QWidget subclass -- constructing one here
        # doesn't show anything on screen by itself; a widget only
        # becomes visible once it's placed into a visible parent's
        # layout (below) and that parent is eventually shown.
        self.status_bar = StatusBar()
        self.image_panel = ImagePanel()
        self.curve_panel = FocusCurvePanel()
        self.control_panel = FocusControlPanel()

        # Every `FocusControlPanel` tab page is individually scrollable
        # (see `_tab_widget`), so, unlike a single scroll area wrapped
        # around the whole panel, its own tab bar never scrolls out of
        # view along with a tall page's content -- it's a sibling of the
        # scrolling area inside the `QTabWidget`, not part of it. That
        # also means this panel needs no scroll wrapper or explicit
        # minimum size of its own here: each page degrades to its own
        # internal scrollbars however small the splitter below makes it.

        # QSplitter arranges its child widgets in a row or column with a
        # user-draggable handle between each pair, unlike a plain layout
        # (QVBoxLayout/QHBoxLayout elsewhere in this codebase), whose
        # relative sizes the user can't adjust interactively. Splitters
        # nest like any other widget: `right` (curve plot over the
        # control panel) becomes one side of `central` (status bar and
        # image panel beside that whole right-hand column).
        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        right.addWidget(self.curve_panel)
        right.addWidget(self.control_panel)
        # Give any extra vertical space (window taller than both panes'
        # combined size) to the curve plot, not the control panel -- a
        # bigger focus curve is actually useful, while the tabs only
        # ever need enough room for whichever one is currently open (and
        # scroll internally otherwise). A stretch factor only governs
        # *extra* space beyond each widget's own initial share, so
        # control_panel still grows on its own if a taller tab (or the
        # user dragging the handle) asks for more than that.
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 0)
        # Stretch factors alone only govern *later* resizes -- a
        # splitter's very first layout instead defaults to giving each
        # child a share proportional to its own `sizeHint()`, which
        # would still hand the control panel most of the window here.
        # An explicit initial split, giving the control panel just
        # enough for a typical (non-Help) tab and the curve panel
        # everything else (a size larger than any real window, clamped
        # down to whatever's actually available), is what actually makes
        # that the default instead.
        right.setSizes([1000000, self.control_panel.preferred_height_excluding_help()])

        # The status bar is a fixed single row stacked directly above the
        # image panel (an ordinary layout, not a splitter -- there's
        # nothing to gain from letting the user resize a one-line bar).
        # The column's margins come from the image panel's own layout:
        # the status bar gets the same left/top/right margins, so the two
        # rows' edges line up. The image panel's top margin is dropped and
        # the gap between the two rows is a small fixed spacing instead
        # (the widgets' own frames add a couple of pixels on top of it),
        # so the status bar sits tight against the Exposure row.
        image_layout = self.image_panel.layout()
        margin_left, margin_top, margin_right, margin_bottom = image_layout.getContentsMargins()
        self.status_bar.setContentsMargins(margin_left, margin_top, margin_right, 0)
        image_layout.setContentsMargins(margin_left, 0, margin_right, margin_bottom)
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(self._STATUS_BAR_SPACING)
        left_layout.addWidget(self.status_bar)
        left_layout.addWidget(self.image_panel, 1)

        central = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        central.addWidget(left)
        central.addWidget(right)
        # Give both sides of the horizontal splitter equal claim on any
        # extra space when the window is resized (an equal, nonzero
        # stretch factor on each), rather than one side absorbing all of
        # it while the other stays fixed.
        central.setStretchFactor(0, 1)
        central.setStretchFactor(1, 1)

        # A QMainWindow manages one designated central widget (menus,
        # toolbars, and dockable panels -- none used here -- go around
        # it); this is what actually makes `central` (and everything
        # nested inside it) appear when the window is shown.
        self.setCentralWidget(central)
        self._size_to_screen(1200, 600)

    def showEvent(self, event):
        """
        This overrides a :class:`PySide6.QtWidgets.QWidget` *event handler*
        rather than connecting to a signal (contrast
        :class:`~nickel_focus.gui.controller.Controller`, which is all
        signal/slot connections): Qt calls this method itself whenever the
        window is shown, so overriding it is the standard way to run code at
        that moment.

        The first time this window actually becomes visible, give the left
        column a share of ``central``'s width that makes the image panel (the
        column's height minus the status bar above it) start out square.
        ``central``'s
        geometry doesn't reflect the
        :meth:`~nickel_focus.gui.views.main_window.MainWindow._size_to_screen`
        resize until the window is genuinely shown -- reading it any earlier in
        :meth:`~nickel_focus.gui.views.main_window.MainWindow.__init__` (even
        after a plain :meth:`PySide6.QtWidgets.QWidget.resize`, even after
        spinning the event loop with no :meth:`PySide6.QtWidgets.QWidget.show`)
        still returns a stale default, so this can't be done any sooner than
        here.  Guarded to run once -- later show events (e.g.  un-minimizing)
        must never undo a splitter drag the user made in between.
        """
        super().showEvent(event)
        if not self._sized_image_panel:
            self._sized_image_panel = True
            central = self.centralWidget()
            left_width = central.height() - self.status_bar.height()
            central.setSizes([left_width, central.width() - left_width])

    def _size_to_screen(self, preferred_width, preferred_height):
        """
        Size the window to ``(preferred_width, preferred_height)``, or smaller
        if the screen's available area (excluding docks/taskbars) can't fit that
        -- so the resize handles are never pushed off screen -- then center it.
        """
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            self.resize(preferred_width, preferred_height)
            return
        available = screen.availableGeometry()
        width = min(preferred_width, int(available.width() * 0.9))
        height = min(preferred_height, int(available.height() * 0.9))
        self.resize(width, height)
        self.move(available.center() - self.rect().center())
