"""
Tabbed telescope/focus-sequence configuration and controls.

One tab per action (Slew, Single, Grid, Auto, Replay), plus Log and Help.
This view only exposes configuration and emits signals for requested
actions; it never constructs a :class:`~nickel_focus.focus.FocusSequence` or
talks to :class:`~nickel_focus.gui.model.focus_worker.FocusWorker` itself --
that's the Controller's job.
"""
from pathlib import Path
import re

from nickel_focus.gui.qt import QtCore, QtGui, QtWidgets


def _int_spin(minimum, maximum, value):
    """
    A :class:`PySide6.QtWidgets.QSpinBox` with no increment/decrement buttons.
    """
    spin = QtWidgets.QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
    return spin


def _float_spin(minimum, maximum, value):
    """
    A :class:`PySide6.QtWidgets.QDoubleSpinBox` with no increment/decrement
    buttons.
    """
    spin = QtWidgets.QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
    return spin


class ExposureTimeSpinBox(QtWidgets.QDoubleSpinBox):
    """
    An exposure-time field whose edits take effect only when Enter is pressed,
    like the data-acquisition GUI's.

    While the field holds an edit that hasn't been committed, its background is
    light red.  Pressing Enter commits it: the background clears and
    :attr:`valueCommitted` is emitted.  Values set by the program
    (:meth:`set_committed_value`) are committed as they're set.  The committed
    value -- not an uncommitted edit -- is what :meth:`committed_value`
    returns.

    Attributes
    ----------
    valueCommitted : :class:`PySide6.QtCore.Signal`
        Emitted with the new value when the user presses Enter.
    """
    valueCommitted = QtCore.Signal(float)

    PENDING_STYLE = 'QDoubleSpinBox { background-color: #ffcdd2; }'
    """Style sheet for a field with an uncommitted edit (light red)."""

    def __init__(self, minimum, maximum, value, parent=None):
        super().__init__(parent)
        self.setRange(minimum, maximum)
        # The camera reports exposure times to the millisecond.
        self.setDecimals(3)
        self.setValue(value)
        self.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._committed = self.value()
        self._pending = False
        self.lineEdit().textEdited.connect(lambda text: self._set_pending(True))

    def committed_value(self):
        """The last committed value (ignoring any uncommitted edit)."""
        return self._committed

    def is_pending(self):
        """Whether the field holds an uncommitted edit."""
        return self._pending

    def set_committed_value(self, value):
        """Show ``value`` as the committed value, discarding any uncommitted edit."""
        self.setValue(value)
        self._committed = self.value()
        self._set_pending(False)

    def stepBy(self, steps):
        """Arrow-key/wheel steps are edits like any other: pending until Enter."""
        super().stepBy(steps)
        self._set_pending(True)

    def keyPressEvent(self, event):
        """Commit the edit on Enter/Return."""
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            self.interpretText()
            self._committed = self.value()
            self._set_pending(False)
            self.valueCommitted.emit(self._committed)
        super().keyPressEvent(event)

    def _set_pending(self, pending):
        self._pending = pending
        self.setStyleSheet(self.PENDING_STYLE if pending else '')


class LogView(QtWidgets.QPlainTextEdit):
    """
    A read-only, console-like view of log output.

    - **Font:** the system's fixed-width font, sized to fit about
      :attr:`TARGET_COLUMNS` characters across the view whenever it's resized
      (within :attr:`MIN_POINT_SIZE` to :attr:`MAX_POINT_SIZE`).
    - **Colors:** text is expected as the console's log formatter writes it,
      with ANSI escape codes for color and bold (see
      :func:`~nickel_focus.pkg.logger.color_text`); :meth:`append_text`
      renders them rather than showing them.
    - **History:** the most recent :attr:`MAX_LINES` lines are kept; the view
      stays scrolled to the newest line unless the user has scrolled up.
    """

    TARGET_COLUMNS = 80
    """The number of characters the font is sized to fit across the view."""

    MIN_POINT_SIZE = 6.
    """The smallest font size, in points, however narrow the view."""

    MAX_POINT_SIZE = 18.
    """The largest font size, in points, however wide the view."""

    MAX_LINES = 5000
    """The number of most recent lines kept."""

    _ANSI = re.compile('\x1b\\[([0-9;]*)m')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # The style hint and fixed pitch make Qt pick a monospace face even
        # where the system names only a generic family (e.g. "monospace")
        # it can't resolve.
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        self.setFont(font)
        # A QPlainTextEdit grows without bound by default; only the oldest
        # lines are dropped.
        self.setMaximumBlockCount(self.MAX_LINES)

    def columns(self):
        """How many characters currently fit across the view."""
        width = self.viewport().width() - 2 * self.document().documentMargin()
        return int(width // QtGui.QFontMetricsF(self.font()).horizontalAdvance('M'))

    def resizeEvent(self, event):
        """Resize the font to fit :attr:`TARGET_COLUMNS` characters across."""
        super().resizeEvent(event)
        self._fit_font()

    def _fit_font(self):
        width = self.viewport().width() - 2 * self.document().documentMargin()
        if width <= 0:
            return
        font = QtGui.QFont(self.font())

        def fits(size):
            font.setPointSizeF(size)
            advance = QtGui.QFontMetricsF(font).horizontalAdvance('M')
            return advance * self.TARGET_COLUMNS <= width

        # Start from a proportional estimate, then step (in quarter points)
        # to the largest size that still fits: glyph widths change in
        # discrete steps, so the estimate alone can land either side.
        advance = QtGui.QFontMetricsF(font).horizontalAdvance('M')
        size = font.pointSizeF() * width / (self.TARGET_COLUMNS * advance)
        size = round(min(max(size, self.MIN_POINT_SIZE), self.MAX_POINT_SIZE) * 4) / 4
        while size < self.MAX_POINT_SIZE and fits(size + 0.25):
            size += 0.25
        while size > self.MIN_POINT_SIZE and not fits(size):
            size -= 0.25
        if size != self.font().pointSizeF():
            font.setPointSizeF(size)
            self.setFont(font)

    def append_text(self, text):
        """
        Append ``text`` (one log record, possibly several lines) as a new line,
        rendering its ANSI color and bold codes.
        """
        scrollbar = self.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum()

        document = self.document()
        cursor = QtGui.QTextCursor(document)
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        if not document.isEmpty():
            cursor.insertBlock()
        char_format = QtGui.QTextCharFormat()
        position = 0
        for match in self._ANSI.finditer(text):
            if match.start() > position:
                cursor.insertText(text[position:match.start()], char_format)
            char_format = self._apply_codes(char_format, match.group(1))
            position = match.end()
        if position < len(text):
            cursor.insertText(text[position:], char_format)

        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    @staticmethod
    def _apply_codes(char_format, codes):
        """
        The text format after the ANSI SGR ``codes`` (e.g. ``'1;38;2;253;174;97'``):
        ``0`` (or none) resets, ``1`` is bold, ``38;2;r;g;b`` sets the color.
        Other codes are ignored.
        """
        values = [int(v) for v in codes.split(';') if v != '']
        new_format = QtGui.QTextCharFormat(char_format)
        i = 0
        if not values:
            return QtGui.QTextCharFormat()
        while i < len(values):
            value = values[i]
            if value == 0:
                new_format = QtGui.QTextCharFormat()
            elif value == 1:
                new_format.setFontWeight(QtGui.QFont.Weight.Bold)
            elif value == 38 and values[i + 1:i + 2] == [2] and len(values) >= i + 5:
                new_format.setForeground(QtGui.QColor(*values[i + 2:i + 5]))
                i += 4
            i += 1
        return new_format


class RadioChoice(QtWidgets.QWidget):
    """
    A choice among a few options, shown as a row of mutually exclusive radio
    buttons side by side.

    Parameters
    ----------
    options : iterable of :obj:`str`
        The options, in order.  The first is chosen initially.
    parent : :class:`PySide6.QtWidgets.QWidget`, optional
        Passed through to the base class.

    Attributes
    ----------
    chosen : :class:`PySide6.QtCore.Signal`
        Emitted with the option's text when the user clicks one of the
        buttons -- never when the choice is set by the program
        (:meth:`set_current`).
    """
    chosen = QtCore.Signal(str)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addStretch(1)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.buttonClicked.connect(lambda button: self.chosen.emit(button.text()))
        for option in options:
            self.add_option(option)
        self._group.buttons()[0].setChecked(True)

    def options(self):
        """The options, in order."""
        return [button.text() for button in self._group.buttons()]

    def add_option(self, text):
        """Add an option (after the existing ones), not chosen."""
        button = QtWidgets.QRadioButton(text)
        self._group.addButton(button)
        self._layout.insertWidget(self._layout.count() - 1, button)

    def button(self, text):
        """The radio button for option ``text``, or None if there isn't one."""
        for button in self._group.buttons():
            if button.text() == text:
                return button
        return None

    def current(self):
        """The chosen option's text."""
        return self._group.checkedButton().text()

    def set_current(self, text):
        """Choose option ``text`` (which must exist), without emitting :attr:`chosen`."""
        self.button(text).setChecked(True)


def _exposure_field_rows():
    """
    Build exposure-time/speed/binning fields, used identically by the Single,
    Grid, and Auto tabs (all of which take real exposures).

    Returns
    -------
    tuple
        ``(rows, exptime_spin, speed_choice, binning_choice)``, where ``rows``
        is a list of ``(label, widget)`` pairs suitable for
        :func:`~nickel_focus.gui.views.focus_control_panel._group_box`,
        ``exptime_spin`` is an :class:`ExposureTimeSpinBox`, and the choices
        are :class:`RadioChoice` rows.
    """
    exptime_spin = ExposureTimeSpinBox(0.1, 600., 5.)
    speed_choice = RadioChoice(['Slow', 'Fast'])
    binning_choice = RadioChoice(['1,1', '2,2', '4,4'])
    rows = [
        ('Exposure time (s):', exptime_spin),
        ('Speed:', speed_choice),
        ('Binning:', binning_choice),
    ]
    return rows, exptime_spin, speed_choice, binning_choice


def _two_column_row(left_rows, right_rows):
    r"""
    Lay ``left_rows``/``right_rows`` (each a list of ``(label, widget)`` pairs)
    out as two side-by-side :class:`PySide6.QtWidgets.QFormLayout` objects, so
    related fields share one row instead of stacking -- e.g. the Slew tab's RA
    and Dec fields.

    Returns
    -------
    :class:`PySide6.QtWidgets.QHBoxLayout`
    """
    # QFormLayout is a layout specialized for exactly this
    # "label: field" arrangement -- addRow() places each label to the
    # left of its widget and keeps every label in the form the same
    # width, so the fields line up in a column even though the labels
    # themselves are different lengths.
    left_form = QtWidgets.QFormLayout()
    for label, widget in left_rows:
        left_form.addRow(label, widget)
    right_form = QtWidgets.QFormLayout()
    for label, widget in right_rows:
        right_form.addRow(label, widget)

    row = QtWidgets.QHBoxLayout()
    row.addLayout(left_form)
    row.addLayout(right_form)
    return row


def _tighten_margins(widget):
    """
    Shrink ``widget``'s layout margins and inter-row spacing from the style's
    defaults (11px margins, 6px spacing -- sized to comfortably fit a titled
    group box's title, and to keep rows from ever looking crowded even when a
    form has just one or two of them) down to a tighter 6px/2px -- used
    throughout this panel's group boxes and tab pages to cut down on unused
    white space, since otherwise that default padding is repeated on every box
    and between every row.

    Parameters
    ----------
    widget : :class:`PySide6.QtWidgets.QWidget`
        The widget whose layout's margins/spacing should be tightened; must
        already have a layout set.
    """
    layout = widget.layout()
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(2)


def _group_box(rows):
    """
    A titleless :class:`PySide6.QtWidgets.QGroupBox` containing ``rows`` laid
    out as a :class:`PySide6.QtWidgets.QFormLayout` -- the border alone groups
    the fields.  Every action tab splits its fields into side-by-side, untitled
    group boxes like this; no title is needed, since each box's own fields
    already identify its purpose and the tab title says what the whole page is
    for.

    Parameters
    ----------
    rows : list
        ``(label, field)`` pairs, in the order they should appear; ``field`` may
        be a widget or a layout (a :class:`PySide6.QtWidgets.QFormLayout` row
        accepts either).  With ``label`` None, ``field`` spans the whole row,
        starting under the labels rather than the fields.

    Returns
    -------
    :class:`PySide6.QtWidgets.QGroupBox`
    """
    form = QtWidgets.QFormLayout()
    for label, field in rows:
        if label is None:
            form.addRow(field)
        else:
            form.addRow(label, field)
    group = QtWidgets.QGroupBox()
    group.setLayout(form)
    _tighten_margins(group)
    return group


def _button_row(*buttons):
    """
    A :class:`PySide6.QtWidgets.QHBoxLayout` containing each of ``buttons``, in
    order.
    """
    row = QtWidgets.QHBoxLayout()
    for button in buttons:
        row.addWidget(button)
    return row


def _add_centered_label(layout, text):
    """
    Add a :class:`PySide6.QtWidgets.QLabel` to ``layout``, horizontally centered
    over whatever ``layout`` holds below it, rather than stretched to its full
    width or left-aligned.

    Parameters
    ----------
    layout : :class:`PySide6.QtWidgets.QBoxLayout`
        The layout to add the label to.
    text : :obj:`str`
        The label's text.
    """
    layout.addWidget(QtWidgets.QLabel(text), alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)


def _tab_widget(layout):
    """
    Wrap ``layout`` in a scrollable page, ready to hand to
    :meth:`PySide6.QtWidgets.QTabWidget.addTab` -- every ``_build_*_tab`` method
    below ends by returning this.

    Scrollable (rather than a bare :class:`PySide6.QtWidgets.QWidget`) so a tab
    whose content doesn't fit the available height scrolls internally, keeping
    the tab bar itself fixed in place instead of scrolling out of view along
    with everything else; this is also why
    :class:`~nickel_focus.gui.views.main_window.MainWindow` doesn't need to size
    the control panel around its tallest page (Help).  The frame is turned off since the surrounding
    :class:`PySide6.QtWidgets.QTabWidget` already visually frames each page;
    without this, a page would show two nested borders.
    """
    widget = QtWidgets.QWidget()
    widget.setLayout(layout)
    _tighten_margins(widget)

    scroll = QtWidgets.QScrollArea()
    scroll.setWidget(widget)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    return scroll


class FocusControlPanel(QtWidgets.QWidget):
    """
    Sequence configuration/action tabs -- plus live status/history on the Log
    tab and short usage reminders on the Help tab.

    One tab per action -- Slew, Single, Grid, Auto, Replay -- each showing only
    the fields relevant to it.  Single/Grid/Auto/Replay each lay their fields
    out as two side-by-side, titleless :class:`PySide6.QtWidgets.QGroupBox`
    sub-panels (see
    :func:`~nickel_focus.gui.views.focus_control_panel._group_box`) -- exposure
    parameters (or, for Replay, on-disk file input) on the left, the focus
    value(s) that define the sequence on the right -- with its own acquisition
    button(s) below both, spanning the full tab width rather than belonging to
    either sub-panel: Single has "Acquire"; Grid and Auto have "Acquire" and
    "Interrupt"; Replay has "Load". Log and Help have no action buttons.
    Because a :class:`PySide6.QtWidgets.QTabWidget` only lets the user interact
    with the currently visible page, each button unambiguously means "do this
    tab's action" -- there's no need to track which tab is active to decide what
    a click means, only to decide what
    :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.get_focus_sequence_type`/
    :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.get_focus_sequence_config`
    should return once a click has already happened.

    The Slew tab (first in the list) is a small control panel around
    :class:`~nickel_focus.slew.NickelTelescopePointing`/
    :func:`~nickel_focus.slew.find_nearest_target`, mirroring
    :class:`~nickel_focus.scripts.slew_to_nearest.NickelSlewToNearest`: a live
    "current position" display and a manually-editable RA/Dec target with its
    own "Move to Target" button on the left, with an "Offset by ..." button
    below it that applies a pending pointing offset (see
    :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.show_pointing_offset`);
    a starlist file/browse field, an
    object-name search string, and the three "Find nearest..." buttons (which
    populate the target fields rather than moving the telescope themselves) on
    the right.  Unlike the other tabs, it has no ``get_*_config`` counterpart --
    each button emits its own request signal with exactly the text fields it
    needs, since there is no single "the currently active tab's config" to read
    (this tab is never in competition with Single/Grid/Auto/Replay for what
    :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.startRequested`
    means).

    The Single, Grid, and Auto tabs each have the same exposure fields
    (exposure time, speed, binning), which show -- and set -- the camera's
    current settings.  Choosing a speed or binning, or entering an exposure
    time and pressing Enter, emits
    :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.exposureSettingEdited`;
    an exposure time that's been edited but not entered is shown light red
    (see :class:`ExposureTimeSpinBox`).  Keeping the three tabs in step, and
    the camera in step with them, is the Controller's job (via
    :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.set_exposure_config`).

    The Single tab doubles as "move to best focus": its focus field defaults to
    the most recent fitted best focus (via
    :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.show_best_focus`),
    so acquiring a single exposure there with the default value *is* moving to
    best focus; changing the value first tests any other focus instead.  There
    is no separate confirmation step -- reviewing/editing the value before
    pressing Acquire is the confirmation.

    Every measurement uses the brightest detected source by default; hovering
    over a different source in the image and pressing 'm' measures that one
    instead (see :class:`~nickel_focus.gui.views.image_panel.ImagePanel`),
    until a move is started from the Slew tab (see
    :meth:`~nickel_focus.gui.controller.Controller.move_to_target`).  Either way, the coordinates actually measured are logged, and so appear
    on the Log tab.

    The Log tab is a :class:`LogView` repeating the console's log output
    (see
    :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.append_log_line`).

    Attributes
    ----------
    startRequested : :class:`PySide6.QtCore.Signal`
        Emitted when Grid/Auto's "Acquire" or Replay's "Load" is clicked; the
        Controller should read
        :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.get_focus_sequence_type`
        and
        :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.get_focus_sequence_config`
        (and
        :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.get_exposure_config`
        for Grid/Auto) to build and run the sequence.
    stopRequested : :class:`PySide6.QtCore.Signal`
        Emitted when Grid/Auto's "Interrupt" is clicked.
    takeSingleExposureRequested : :class:`PySide6.QtCore.Signal`
        Emitted with a focus value when Single's "Acquire" is clicked.
    moveToTargetRequested : :class:`PySide6.QtCore.Signal`
        Emitted with ``(ra_text, dec_text)`` when the Slew tab's "Move to
        Target" is clicked; the Controller should parse these and call
        :meth:`~nickel_focus.slew.NickelTelescopePointing.slew_to`.
    findNearestObjectRequested : :class:`PySide6.QtCore.Signal`
        Emitted with ``(file_text, search_text)`` when the Slew tab's "Find
        nearest object" is clicked -- either may be an empty string, meaning
        "use the default" (see :func:`~nickel_focus.slew.find_nearest_target`).
    findNearestPointingRequested : :class:`PySide6.QtCore.Signal`
        Emitted when the Slew tab's "Find nearest pointing star" is clicked; the
        Controller should search the packaged default catalog with
        ``obj_search_str='Pointing'``, ignoring whatever is currently in the
        file/search fields.
    findNearestFocusRequested : :class:`PySide6.QtCore.Signal`
        Emitted when the Slew tab's "Find nearest focus star" is clicked; like
        ``findNearestPointingRequested``, but with
        ``obj_search_str='Focusing'``.
    applyPointingOffsetRequested : :class:`PySide6.QtCore.Signal`
        Emitted when the Slew tab's "Offset by ..." button is clicked; the
        Controller should apply its pending pointing offset (see
        :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.show_pointing_offset`).
    useCurrentFocusRequested : :class:`PySide6.QtCore.Signal`
        Emitted when the Single tab's "Use current focus" is clicked; the
        Controller should fill the focus value with the telescope's current
        focus (see
        :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.set_single_focus`).
    exposureSettingEdited : :class:`PySide6.QtCore.Signal`
        Emitted with ``(key, value)`` when the user commits an exposure setting
        on the Single, Grid, or Auto tab: ``('exptime', float)`` when an
        exposure time is entered, or ``('speed', str)``/``('binning', str)``
        when a speed or binning button is clicked.
    """
    startRequested = QtCore.Signal()
    stopRequested = QtCore.Signal()
    takeSingleExposureRequested = QtCore.Signal(float)
    moveToTargetRequested = QtCore.Signal(str, str)
    findNearestObjectRequested = QtCore.Signal(str, str)
    findNearestPointingRequested = QtCore.Signal()
    findNearestFocusRequested = QtCore.Signal()
    useCurrentFocusRequested = QtCore.Signal()
    applyPointingOffsetRequested = QtCore.Signal()
    exposureSettingEdited = QtCore.Signal(str, object)

    NO_POINTING_OFFSET = 'Offset by —'
    """The pointing-offset button's label while no offset is pending."""


    def __init__(self, parent=None):
        super().__init__(parent)

        # Whether something is running, and whether a pointing offset is
        # pending: the pointing-offset button needs both (see
        # _update_offset_button).
        self._running = False
        self._offset_pending = False

        self.slew_tab = self._build_slew_tab()
        self.single_tab = self._build_single_tab()
        self.grid_tab = self._build_grid_tab()
        self.auto_tab = self._build_auto_tab()
        self.replay_tab = self._build_replay_tab()
        self.log_tab = self._build_log_tab()
        self.help_tab = self._build_help_tab()

        # QTabWidget shows exactly one of its pages (each an ordinary
        # widget, built above) at a time, switched via the row of tabs
        # it draws automatically from the label given to addTab().
        # Pages that aren't currently showing still exist as real
        # widgets in memory -- their state (a spin box's value, etc.)
        # isn't lost when you switch away -- they're just not painted or
        # interactive until their tab is selected again.
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.slew_tab, 'Slew')
        self.tabs.addTab(self.single_tab, 'Single')
        self.tabs.addTab(self.grid_tab, 'Grid')
        self.tabs.addTab(self.auto_tab, 'Auto')
        self.tabs.addTab(self.replay_tab, 'Replay')
        self.tabs.addTab(self.log_tab, 'Log')
        self.tabs.addTab(self.help_tab, 'Help')

        # Report committed exposure-setting edits from any of the three
        # tabs that take exposures. RadioChoice.chosen fires only for the
        # user's own clicks, never for set_exposure_config.
        for exptime_spin, speed_choice, binning_choice in self._exposure_widgets():
            exptime_spin.valueCommitted.connect(
                lambda value: self.exposureSettingEdited.emit('exptime', value)
            )
            speed_choice.chosen.connect(
                lambda text: self.exposureSettingEdited.emit('speed', text)
            )
            binning_choice.chosen.connect(
                lambda text: self.exposureSettingEdited.emit('binning', text)
            )

        # Every input widget across the five actionable tabs, locked
        # uniformly while anything is running (hardware exclusivity:
        # only one operation at a time, so there's no reason to allow
        # editing a different tab's configuration meanwhile). The tab
        # bar itself is deliberately never disabled, so the Log tab
        # stays reachable while a sequence runs.
        self._config_widgets = [
            self.slew_target_name_edit, self.slew_target_ra_edit, self.slew_target_dec_edit,
            self.slew_file_edit, self.single_current_focus_button,
            self.slew_browse_button, self.slew_search_edit, self.single_focus_spin,
            self.single_exptime_spin, self.single_speed_choice, self.single_binning_choice,
            self.grid_start_spin, self.grid_step_spin, self.grid_nstep_spin,
            self.grid_exptime_spin, self.grid_speed_choice, self.grid_binning_choice,
            self.auto_start_spin, self.auto_step_spin, self.auto_maxsteps_spin,
            self.auto_exptime_spin, self.auto_speed_choice, self.auto_binning_choice,
            self.replay_datadir_edit, self.replay_browse_button, self.replay_prefix_edit,
            self.replay_suffix_edit, self.replay_obsnum_spin, self.replay_start_spin,
            self.replay_step_spin, self.replay_nstep_spin,
        ]
        # The "go" buttons on the five actionable tabs, disabled while
        # anything is running.
        self._acquire_buttons = [
            self.slew_move_button, self.slew_find_object_button,
            self.slew_find_pointing_button, self.slew_find_focus_button,
            self.single_acquire_button, self.grid_acquire_button,
            self.auto_acquire_button, self.replay_load_button,
        ]
        # The "stop" buttons on Grid/Auto, enabled only while something
        # is running -- either one works, since `stopRequested` always
        # targets whatever's actually running, regardless of which tab
        # is currently visible.
        self._interrupt_buttons = [self.grid_interrupt_button, self.auto_interrupt_button]

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.setContentsMargins(0, 0, 0, 0)

    # -- widget construction ----------------------------------------------

    def _build_acquire_interrupt_row(self):
        """
        An "Acquire"/"Interrupt" button pair wired to
        :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.startRequested`/
        :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.stopRequested`,
        used identically by the Grid and Auto tabs (the only two tabs with a
        running, interruptible sequence). Interrupt starts disabled; see
        :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.set_running`/
        :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.set_stopping`
        for how the two buttons' enabled states are actually managed once the
        panel is in use.

        Returns
        -------
        tuple
            ``(button_row, acquire_button, interrupt_button)``.
        """
        acquire_button = QtWidgets.QPushButton('Acquire')
        # Connecting a button's `clicked` signal straight to another
        # signal's `.emit` -- rather than to a Python method that then
        # calls `.emit()` itself -- is a common Qt shorthand: `.emit` is
        # itself just a callable, so anything that can be a slot
        # (including it) can be connected directly.
        acquire_button.clicked.connect(self.startRequested.emit)
        interrupt_button = QtWidgets.QPushButton('Interrupt')
        interrupt_button.setEnabled(False)
        interrupt_button.clicked.connect(self.stopRequested.emit)
        return _button_row(acquire_button, interrupt_button), acquire_button, interrupt_button

    def _build_slew_tab(self):
        """
        Build the Slew tab as two untitled :class:`PySide6.QtWidgets.QGroupBox`
        sub-panels: current RA-Dec, the target (name, RA-Dec), "Move to
        Target", and "Offset by ..." on the left; starlist file/browse, a target-name search
        pattern, and a single row of three "Find nearest ..." buttons
        ("Object", "Pointing *", "Focusing *" -- ``*`` standing in for "star")
        on the right.  Unlike the other action
        tabs, the two panels here are separate sub-workflows (move to a known
        position vs. find a target), and each ends in its own button(s) rather
        than sharing one row below both.
        """
        # Short "RA:"/"Dec:" field labels, not "Current RA:"/"Target RA:"
        # etc. -- those are wide enough (with two side-by-side columns
        # per row) to noticeably widen this tab beyond every other one,
        # and with it the whole window's minimum width; a short section
        # header above each row disambiguates instead, at a fraction of
        # the width cost.
        self.slew_current_ra_label = QtWidgets.QLabel('—')
        self.slew_current_dec_label = QtWidgets.QLabel('—')
        self.slew_target_name_edit = QtWidgets.QLineEdit()
        self.slew_target_ra_edit = QtWidgets.QLineEdit()
        self.slew_target_dec_edit = QtWidgets.QLineEdit()
        # The name is filled in by "Find nearest ..." and can be edited.
        # Typing coordinates (textEdited fires only for the user's own
        # edits) means the target is no longer the one named, so the name
        # is cleared.
        for edit in (self.slew_target_ra_edit, self.slew_target_dec_edit):
            edit.textEdited.connect(self.slew_target_name_edit.clear)
        # Wide enough for a full sexagesimal value (e.g. '+00:00:00.0')
        # to show without scrolling -- a bare default QLineEdit is only
        # wide enough for a few characters. `sizeHint()` on a QLineEdit
        # already containing that text turned out to reserve much more
        # than the text itself needs (a fixed, style-dependent margin,
        # not something proportional to the text); the font metrics'
        # bare advance for the text plus a small fixed pad is tighter
        # and still leaves room to spare (confirmed against
        # `cursorRect()` with the text entered).
        coord_width = (
            self.slew_target_ra_edit.fontMetrics().horizontalAdvance('+00:00:00.0') + 10
        )
        self.slew_target_ra_edit.setMinimumWidth(coord_width)
        self.slew_target_dec_edit.setMinimumWidth(coord_width)
        current_row = _two_column_row(
            [('RA:', self.slew_current_ra_label)],
            [('Dec:', self.slew_current_dec_label)],
        )
        name_row = QtWidgets.QFormLayout()
        name_row.addRow('Name:', self.slew_target_name_edit)
        target_row = _two_column_row(
            [('RA:', self.slew_target_ra_edit)],
            [('Dec:', self.slew_target_dec_edit)],
        )
        self.slew_move_button = QtWidgets.QPushButton('Move to Target')
        self.slew_move_button.clicked.connect(
            lambda: self.moveToTargetRequested.emit(
                self.slew_target_ra_edit.text(), self.slew_target_dec_edit.text()
            )
        )

        position_layout = QtWidgets.QVBoxLayout()
        _add_centered_label(position_layout, 'Current coordinates')
        position_layout.addLayout(current_row)
        _add_centered_label(position_layout, 'Target')
        position_layout.addLayout(name_row)
        position_layout.addLayout(target_row)
        # Applies a pending pointing offset; disabled until there is one
        # (see show_pointing_offset), and labeled with it.
        self.slew_offset_button = QtWidgets.QPushButton(self.NO_POINTING_OFFSET)
        self.slew_offset_button.setEnabled(False)
        self.slew_offset_button.clicked.connect(self.applyPointingOffsetRequested.emit)

        position_layout.addLayout(_button_row(self.slew_move_button))
        position_layout.addLayout(_button_row(self.slew_offset_button))
        position_layout.addStretch(1)
        position_group = QtWidgets.QGroupBox()
        position_group.setLayout(position_layout)
        _tighten_margins(position_group)

        self.slew_file_edit = QtWidgets.QLineEdit('')
        self.slew_browse_button = QtWidgets.QPushButton('Browse…')
        self.slew_browse_button.clicked.connect(self._on_slew_browse_clicked)
        file_row = QtWidgets.QHBoxLayout()
        file_row.addWidget(self.slew_file_edit, 1)
        file_row.addWidget(self.slew_browse_button)

        self.slew_search_edit = QtWidgets.QLineEdit('')

        # Short button labels ("Object"/"Pointing */"Focusing *", the
        # asterisk standing in for "star") so the three fit in one row
        # instead of stacking -- the "Find nearest ..." label above
        # supplies the words those labels leave out.
        self.slew_find_object_button = QtWidgets.QPushButton('Object')
        self.slew_find_object_button.clicked.connect(
            lambda: self.findNearestObjectRequested.emit(
                self.slew_file_edit.text(), self.slew_search_edit.text()
            )
        )
        self.slew_find_pointing_button = QtWidgets.QPushButton('Pointing *')
        self.slew_find_pointing_button.clicked.connect(self.findNearestPointingRequested.emit)
        self.slew_find_focus_button = QtWidgets.QPushButton('Focusing *')
        self.slew_find_focus_button.clicked.connect(self.findNearestFocusRequested.emit)

        find_row = _button_row(
            self.slew_find_object_button, self.slew_find_pointing_button,
            self.slew_find_focus_button
        )
        # Tighter than the default gap between widgets in a layout --
        # frees up width for the Position panel next to it without
        # actually shrinking any button.
        find_row.setSpacing(2)

        # Narrow enough that neither dictates the Search panel's width
        # on its own -- that's the button row above, now the widest
        # thing here. Still wide enough to show a short search pattern
        # or filename without immediately scrolling.
        text_width = QtWidgets.QLineEdit('M' * 8).sizeHint().width()
        self.slew_file_edit.setMinimumWidth(text_width)
        self.slew_search_edit.setMinimumWidth(text_width)

        search_layout = QtWidgets.QVBoxLayout()
        _add_centered_label(search_layout, 'Starlist')
        search_layout.addLayout(file_row)
        _add_centered_label(search_layout, 'Target pattern')
        search_layout.addWidget(self.slew_search_edit)
        _add_centered_label(search_layout, 'Find nearest ...')
        search_layout.addLayout(find_row)
        search_layout.addStretch(1)
        search_group = QtWidgets.QGroupBox()
        search_group.setLayout(search_layout)
        _tighten_margins(search_group)

        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(position_group, 1)
        layout.addWidget(search_group, 1)
        return _tab_widget(layout)

    def _build_single_tab(self):
        """
        Build the Single tab as two side-by-side sub-panels -- exposure
        settings, focus value (with a "Use current focus" button below it, and
        the latest exposure's FWHM below that) --
        with "Acquire" below both, spanning the full tab width rather than
        belonging to either one.
        """
        self.single_focus_spin = _int_spin(165, 500, 340)
        (
            exposure_rows, self.single_exptime_spin, self.single_speed_choice,
            self.single_binning_choice
        ) = _exposure_field_rows()
        self.single_current_focus_button = QtWidgets.QPushButton('Use current focus')
        self.single_current_focus_button.clicked.connect(self.useCurrentFocusRequested.emit)
        # The latest Single exposure's FWHM (see set_single_fwhm).
        self.single_fwhm_label = QtWidgets.QLabel()
        self.set_single_fwhm(None, None)
        focus_rows = [
            ('Focus value:', self.single_focus_spin),
            ('', self.single_current_focus_button),
            # The whole row, so it starts under "Focus value:".
            (None, self.single_fwhm_label),
        ]

        self.single_acquire_button = QtWidgets.QPushButton('Acquire')
        self.single_acquire_button.clicked.connect(
            lambda: self.takeSingleExposureRequested.emit(self.single_focus_spin.value())
        )

        columns = QtWidgets.QHBoxLayout()
        columns.addWidget(_group_box(exposure_rows), 1)
        columns.addWidget(_group_box(focus_rows), 1)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(columns)
        layout.addLayout(_button_row(self.single_acquire_button))
        layout.addStretch(1)
        return _tab_widget(layout)

    def _build_grid_tab(self):
        """
        Build the Grid tab as two side-by-side sub-panels -- exposure settings,
        start focus/step size/number of steps -- with Acquire/Interrupt below
        both, spanning the full tab width.
        """
        self.grid_start_spin = _int_spin(165, 500, 340)
        self.grid_step_spin = _int_spin(1, 100, 5)
        self.grid_nstep_spin = _int_spin(3, 100, 5)
        (
            exposure_rows, self.grid_exptime_spin, self.grid_speed_choice,
            self.grid_binning_choice
        ) = _exposure_field_rows()
        focus_rows = [
            ('Start focus:', self.grid_start_spin),
            ('Step size:', self.grid_step_spin),
            ('Number of steps:', self.grid_nstep_spin),
        ]
        (
            button_row, self.grid_acquire_button, self.grid_interrupt_button
        ) = self._build_acquire_interrupt_row()

        columns = QtWidgets.QHBoxLayout()
        columns.addWidget(_group_box(exposure_rows), 1)
        columns.addWidget(_group_box(focus_rows), 1)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(columns)
        layout.addLayout(button_row)
        layout.addStretch(1)
        return _tab_widget(layout)

    def _build_auto_tab(self):
        """
        Build the Auto tab as two side-by-side sub-panels -- exposure settings,
        start focus/step size/max steps -- with Acquire/Interrupt below both,
        spanning the full tab width.
        """
        self.auto_start_spin = _int_spin(165, 500, 340)
        self.auto_step_spin = _int_spin(1, 100, 5)
        self.auto_maxsteps_spin = _int_spin(2, 100, 12)
        (
            exposure_rows, self.auto_exptime_spin, self.auto_speed_choice,
            self.auto_binning_choice
        ) = _exposure_field_rows()
        focus_rows = [
            ('Start focus:', self.auto_start_spin),
            ('Step size:', self.auto_step_spin),
            ('Max steps:', self.auto_maxsteps_spin),
        ]
        (
            button_row, self.auto_acquire_button, self.auto_interrupt_button
        ) = self._build_acquire_interrupt_row()

        columns = QtWidgets.QHBoxLayout()
        columns.addWidget(_group_box(exposure_rows), 1)
        columns.addWidget(_group_box(focus_rows), 1)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(columns)
        layout.addLayout(button_row)
        layout.addStretch(1)
        return _tab_widget(layout)

    def _build_replay_tab(self):
        """
        Build the Replay tab as two side-by-side sub-panels -- on-disk file
        input (data directory, filename fields), the focus grid describing the
        sequence to replay -- with "Load" below both, spanning the full tab
        width.
        """
        self.replay_datadir_edit = QtWidgets.QLineEdit('.')
        self.replay_browse_button = QtWidgets.QPushButton('Browse…')
        self.replay_browse_button.clicked.connect(self._on_browse_clicked)
        self.replay_prefix_edit = QtWidgets.QLineEdit('n')
        self.replay_suffix_edit = QtWidgets.QLineEdit('.fits')
        self.replay_obsnum_spin = _int_spin(0, 999999, 0)
        self.replay_start_spin = _int_spin(165, 500, 340)
        self.replay_step_spin = _int_spin(1, 100, 5)
        self.replay_nstep_spin = _int_spin(3, 100, 5)

        datadir_row = QtWidgets.QHBoxLayout()
        datadir_row.addWidget(self.replay_datadir_edit, 1)
        datadir_row.addWidget(self.replay_browse_button)

        # "Data directory" centered above the text box/browse button,
        # rather than a QFormLayout label beside them -- matching the
        # Slew tab's "Starlist" field (see `_build_slew_tab`).
        file_layout = QtWidgets.QVBoxLayout()
        _add_centered_label(file_layout, 'Data directory')
        file_layout.addLayout(datadir_row)
        filename_form = QtWidgets.QFormLayout()
        filename_form.addRow('Prefix:', self.replay_prefix_edit)
        filename_form.addRow('Suffix:', self.replay_suffix_edit)
        filename_form.addRow('Obsnum:', self.replay_obsnum_spin)
        file_layout.addLayout(filename_form)
        file_group = QtWidgets.QGroupBox()
        file_group.setLayout(file_layout)
        _tighten_margins(file_group)

        focus_rows = [
            ('Start focus:', self.replay_start_spin),
            ('Step size:', self.replay_step_spin),
            ('Number of steps:', self.replay_nstep_spin),
        ]

        self.replay_load_button = QtWidgets.QPushButton('Load')
        self.replay_load_button.clicked.connect(self.startRequested.emit)

        columns = QtWidgets.QHBoxLayout()
        columns.addWidget(file_group, 1)
        columns.addWidget(_group_box(focus_rows), 1)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(columns)
        layout.addLayout(_button_row(self.replay_load_button))
        layout.addStretch(1)
        return _tab_widget(layout)

    def _build_log_tab(self):
        """
        Build the Log tab: a :class:`LogView` repeating the console's log
        output, and nothing else.
        """
        self.log_widget = LogView()

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.log_widget)
        return _tab_widget(layout)

    def _build_help_tab(self):
        """
        Build the Help tab: a single block of short, rich-text usage reminders.
        """
        text = (
            '<b>Slew</b> — move the telescope to a target. Enter RA/Dec '
            'directly, or use "Find nearest..." to search a starlist '
            '(the packaged pointing/focus catalog by default) and fill '
            'them in, then press "Move to Target."<br><br>'
            '<b>Pointing offset</b> — after a Single exposure, hover over '
            "the star that should be at the center of the image and press 'p'. "
            'A line from the star to the image center shows the offset, which '
            'the Slew tab\'s "Offset by ..." button then applies (and marks '
            'the telescope on target). Take another Single exposure to '
            'check, and repeat if needed.<br><br>'
            '<b>Single</b> — take one exposure at the given focus. '
            'Defaults to the most recent fitted best focus, so acquiring '
            'it as-is moves to best focus; change the value first to '
            'test any other focus, or press "Use current focus" to expose '
            'at the focus the telescope is at now. The FWHM of the latest '
            'Single exposure is shown below.<br><br>'
            '<b>Exposure settings</b> (Single, Grid, Auto) — show the '
            "camera's current settings, including changes made from other "
            'programs, and are the same on all three tabs. Choosing a speed '
            'or binning changes the camera immediately; a new exposure time '
            'turns light red until you press Enter, which sends it to the '
            'camera.<br><br>'
            '<b>Grid</b> — step through an evenly spaced focus grid.<br>'
            '<b>Auto</b> — adaptively search for the best focus.<br>'
            '<b>Replay</b> — reprocess an existing set of exposures from disk.<br>'
            '<b>Log</b> — the log, as shown in the terminal.<br><br>'
            '<b>Source selection</b> — the brightest star is measured '
            "automatically by default (see the Log for its coordinates); "
            "hover over a different star in the image and press 'm' to "
            'measure that one instead. The marked star is used until you '
            'start a move from the Slew tab (a slew started from another '
            'program does not clear it). Press \'r\' over the image to fit '
            'the whole frame in view again.<br><br>'
            '<b>Acquire / Load</b> — run the action configured in that '
            'tab.<br>'
            '<b>Interrupt</b> — stop a running Grid/Auto sequence between '
            'steps (does not abort the current exposure).<br><br>'
            '<b>Status bar</b> (above the image) — green is ready, yellow '
            'needs attention, red is an error or unavailable (all red '
            'without a telescope connection). Slew: telescope motion '
            'allowed. Track: tracking on. On Target: on target (yellow while '
            'moving). Focus: current focus value; green when the secondary '
            'is locked. Exp: camera ready.'
        )
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        # A QLabel treats its text as plain text by default -- this is
        # what makes it interpret the <b>/<br> tags above as HTML markup
        # instead of displaying them as literal characters.
        label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(label)
        layout.addStretch(1)
        return _tab_widget(layout)

    # -- public API used by the Controller/MainWindow -----------------------

    def preferred_height_excluding_help(self):
        """
        The tallest *minimum* height among every tab page except Help, plus the
        tab bar -- i.e.  how tall this panel should start out so every
        *interactive* tab displays without scrolling by default.

        Not a hard floor: every tab page is individually scrollable (see
        :func:`~nickel_focus.gui.views.focus_control_panel._tab_widget`), so
        this panel can be shrunk well below this value -- any tab that no longer
        fits just scrolls internally, and the tab bar itself (outside all of
        that scrolling) stays put either way.  Help is excluded because it's a
        large block of static reference text that's taller than every other tab
        combined with the tab bar; sizing the *initial* height around it too
        would waste space under the six shorter tabs every time the window
        opens, for the sake of a tab that's fine to need a scrollbar
        immediately.

        Each candidate tab's *content widget*
        :meth:`PySide6.QtWidgets.QWidget.minimumSizeHint` is used here, not the
        tab page's own (a :class:`PySide6.QtWidgets.QScrollArea`, whose
        :meth:`PySide6.QtWidgets.QWidget.sizeHint` reflects a generically
        "comfortable" size that can run well past what its content actually
        needs -- e.g.  the Log tab's :class:`LogView`,
        which is happy to be much shorter than its
        :meth:`PySide6.QtWidgets.QWidget.sizeHint`, and would otherwise count
        as the tallest tab here).

        Returns
        -------
        :obj:`int`
            Preferred height, in pixels, that fits every tab page except Help
            without scrolling.
        """
        other_tabs = [
            self.slew_tab, self.single_tab, self.grid_tab, self.auto_tab, self.replay_tab,
            self.log_tab
        ]
        tallest_other = max(tab.widget().minimumSizeHint().height() for tab in other_tabs)
        tab_bar_height = self.tabs.tabBar().sizeHint().height()
        # `QTabWidget` reserves a couple more pixels around the page
        # itself (its pane frame) beyond just the tab bar -- without
        # this, the tallest tab ends up a hair short of fitting and gets
        # an unnecessary, barely-there scrollbar (confirmed against the
        # actual rendered tab: a couple of pixels was all it took).
        return tallest_other + tab_bar_height + 4

    def get_focus_sequence_type(self):
        """
        The sequence type for the currently active tab, as ``'grid'``,
        ``'automated'``, or ``'archive'`` -- or ``None`` for any other tab
        (Slew, Single, Log, Help; none of which can have triggered
        :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.startRequested`
        in the first place).
        """
        tab = self.tabs.currentWidget()
        if tab is self.grid_tab:
            return 'grid'
        if tab is self.auto_tab:
            return 'automated'
        if tab is self.replay_tab:
            return 'archive'
        return None

    def get_focus_sequence_config(self):
        """
        Return the currently active tab's configuration as a :obj:`dict`. Only
        includes keys relevant to that tab -- e.g.  a Grid config has no
        ``datadir``, an Auto config has ``maxsteps`` instead of ``nstep`` --
        matching the CLI arguments of
        :class:`~nickel_focus.scripts.focus.NickelFocus`. Empty for any other
        tab (Slew, Single, Log, Help).
        """
        tab = self.tabs.currentWidget()
        if tab is self.grid_tab:
            return {
                'start': self.grid_start_spin.value(),
                'step': self.grid_step_spin.value(),
                'nstep': self.grid_nstep_spin.value(),
            }
        if tab is self.auto_tab:
            return {
                'start': self.auto_start_spin.value(),
                'step': self.auto_step_spin.value(),
                'maxsteps': self.auto_maxsteps_spin.value(),
            }
        if tab is self.replay_tab:
            return {
                'datadir': Path(self.replay_datadir_edit.text()),
                'prefix': self.replay_prefix_edit.text(),
                'suffix': self.replay_suffix_edit.text(),
                'obsnum': self.replay_obsnum_spin.value(),
                'start': self.replay_start_spin.value(),
                'step': self.replay_step_spin.value(),
                'nstep': self.replay_nstep_spin.value(),
            }
        return {}

    def _exposure_widgets(self):
        """The ``(exptime_spin, speed_choice, binning_choice)`` of Single, Grid, and Auto."""
        return (
            (self.single_exptime_spin, self.single_speed_choice, self.single_binning_choice),
            (self.grid_exptime_spin, self.grid_speed_choice, self.grid_binning_choice),
            (self.auto_exptime_spin, self.auto_speed_choice, self.auto_binning_choice),
        )

    def get_exposure_config(self):
        """
        Return the currently active tab's exposure settings as a :obj:`dict`
        with keys ``exptime`` (:obj:`float`), ``speed``, ``binning``
        (:obj:`str`) -- passed to
        :meth:`~nickel_focus.services.nscicam.ExposureConfig.configure` before a live
        exposure.  The exposure time is the committed one: an edit that hasn't
        been entered is ignored.  Empty for any tab other than Single, Grid, or
        Auto, since those are the only ones that take new exposures.
        """
        tab = self.tabs.currentWidget()
        if tab is self.single_tab:
            exptime, speed, binning = (
                self.single_exptime_spin, self.single_speed_choice, self.single_binning_choice
            )
        elif tab is self.grid_tab:
            exptime, speed, binning = (
                self.grid_exptime_spin, self.grid_speed_choice, self.grid_binning_choice
            )
        elif tab is self.auto_tab:
            exptime, speed, binning = (
                self.auto_exptime_spin, self.auto_speed_choice, self.auto_binning_choice
            )
        else:
            return {}
        return {
            'exptime': exptime.committed_value(), 'speed': speed.current(),
            'binning': binning.current()
        }

    def set_exposure_config(self, config):
        """
        Set the exposure fields on every tab that takes exposures (Single, Grid,
        Auto) -- e.g. to the camera's current settings.  An exposure time set
        this way is committed (see :class:`ExposureTimeSpinBox`), discarding
        any uncommitted edit.  Nothing is emitted.

        Parameters
        ----------
        config : :obj:`dict`
            Any subset of the keys returned by
            :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.get_exposure_config`:
            ``exptime`` (:obj:`float`), ``speed``, ``binning`` (:obj:`str`).
            Missing keys are left unchanged.

        Returns
        -------
        :obj:`list`
            The keys (``speed``/``binning``) whose value wasn't one of the
            options, and so was added as a new button.  An ``exptime``
            outside the field's range is clamped to it rather than reported.
        """
        exposure_widgets = self._exposure_widgets()
        added = []
        for key, index in (('speed', 1), ('binning', 2)):
            if key in config and exposure_widgets[0][index].button(config[key]) is None:
                added.append(key)
                for widgets in exposure_widgets:
                    widgets[index].add_option(config[key])
        for exptime_spin, speed_choice, binning_choice in exposure_widgets:
            if 'exptime' in config:
                exptime_spin.set_committed_value(config['exptime'])
            if 'speed' in config:
                speed_choice.set_current(config['speed'])
            if 'binning' in config:
                binning_choice.set_current(config['binning'])
        return added

    def set_hardware_tabs_enabled(self, enabled):
        """
        Enable or disable the four tabs that need a live ``ktl`` connection to
        do anything (Slew, Single, Grid, Auto) -- called once from
        :meth:`~nickel_focus.gui.controller.Controller.__init__` (see there for
        how ``enabled`` is decided). This is a one-time, ``ktl``-availability
        toggle, independent of
        :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.set_running`'s
        per-action running state.

        Disabling a tab via :meth:`PySide6.QtWidgets.QTabWidget.setTabEnabled`
        grays it out and disables every widget on it, so it stays visible but
        can't be clicked into or interacted with; the Replay tab -- the only one
        that works with no ``ktl`` connection at all -- is left alone, and
        becomes the tab shown when the window first opens.

        Parameters
        ----------
        enabled : :obj:`bool`
            Whether the four ``ktl``-driven tabs should be enabled.
        """
        for tab in (self.slew_tab, self.single_tab, self.grid_tab, self.auto_tab):
            self.tabs.setTabEnabled(self.tabs.indexOf(tab), enabled)
        if not enabled:
            self.tabs.setCurrentWidget(self.replay_tab)

    def set_running(self, running):
        """Toggle widget states for whether an action is currently running."""
        self._running = running
        for widget in self._config_widgets:
            widget.setEnabled(not running)
        for button in self._acquire_buttons:
            button.setEnabled(not running)
        for button in self._interrupt_buttons:
            button.setEnabled(running)
        self._update_offset_button()

    def show_pointing_offset(self, text):
        """
        Show a pending pointing offset on the Slew tab's offset button, e.g.
        ``'Offset by 12.3″ E, 4.5″ N'``, enabling it while nothing is running;
        or, with ``text`` None, return it to
        :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.NO_POINTING_OFFSET`
        and disable it.

        Parameters
        ----------
        text : :obj:`str` or None
            The offset, already formatted (see
            :meth:`~nickel_focus.pointing.PointingOffset.text`), or None if
            no offset is pending.
        """
        self._offset_pending = text is not None
        self.slew_offset_button.setText(
            self.NO_POINTING_OFFSET if text is None else f'Offset by {text}'
        )
        self._update_offset_button()

    def _update_offset_button(self):
        """Enable the offset button only with an offset pending and nothing running."""
        self.slew_offset_button.setEnabled(self._offset_pending and not self._running)

    def set_stopping(self):
        """
        Reflect an Interrupt request that hasn't taken effect yet (the running
        sequence only stops once the current exposure finishes): disable the
        Interrupt buttons until it does.
        """
        for button in self._interrupt_buttons:
            button.setEnabled(False)

    def set_current_position(self, ra_text, dec_text):
        """
        Update the Slew tab's live "current position" display.

        Parameters
        ----------
        ra_text : :obj:`str`
            The telescope's current right ascension, already formatted for
            display (e.g.  ``'05:30:00'``) -- formatting an `Angle` is the
            Controller's job, not this passive view's.
        dec_text : :obj:`str`
            The telescope's current declination, formatted the same way.
        """
        self.slew_current_ra_label.setText(ra_text)
        self.slew_current_dec_label.setText(dec_text)

    def show_nearest_target(self, ra_text, dec_text, name):
        """
        Populate the Slew tab's target Name and RA/Dec fields with a found
        nearest target.

        Parameters
        ----------
        ra_text : :obj:`str`
            The nearest target's right ascension, already formatted for display.
        dec_text : :obj:`str`
            The nearest target's declination, already formatted for display.
        name : :obj:`str`
            The nearest target's name, from its catalog.
        """
        self.slew_target_name_edit.setText(name)
        self.slew_target_ra_edit.setText(ra_text)
        self.slew_target_dec_edit.setText(dec_text)

    def get_target_name(self):
        """
        The Slew tab's target name (filled in by "Find nearest ...", possibly
        edited, and cleared when the target coordinates are typed in), or None
        if it's empty.
        """
        name = self.slew_target_name_edit.text().strip()
        return name or None

    def get_target_coordinates(self):
        """The Slew tab's target ``(ra_text, dec_text)``, as entered."""
        return self.slew_target_ra_edit.text(), self.slew_target_dec_edit.text()

    def set_single_focus(self, focus_value):
        """
        Set the Single tab's focus value -- rounded, since it's a whole-unit
        telescope position (and clamped to the field's range).
        """
        self.single_focus_spin.setValue(round(focus_value))

    def set_single_fwhm(self, pixels, arcsec):
        """
        Show the latest Single exposure's FWHM below "Use current focus", e.g.
        ``'FWHM: 3.21 (pix); 1.07 (arcsec)'``.  Either value may be None
        (not measured, or the binning is unknown), shown as ``—``; with
        both None (e.g. before any exposure) the label reads
        ``'FWHM: — (pix); — (arcsec)'``.

        Parameters
        ----------
        pixels : :obj:`float` or None
            The FWHM in pixels.
        arcsec : :obj:`float` or None
            The FWHM in arcseconds.
        """
        def text(value):
            return '—' if value is None else f'{value:.2f}'

        self.single_fwhm_label.setText(f'FWHM: {text(pixels)} (pix); {text(arcsec)} (arcsec)')

    def show_best_focus(self, best_focus):
        """
        Set a completed sequence's fitted best focus as the Single tab's
        default focus value -- rounded, since a focus value is a whole-unit
        telescope position even though the fit itself can land on a fractional
        one.

        Parameters
        ----------
        best_focus : :obj:`float`
            The fitted best focus.
        """
        self.single_focus_spin.setValue(round(best_focus))

    def append_log_line(self, text):
        """
        Append one log record, as the console's formatter wrote it, to the Log
        tab (see :meth:`LogView.append_text`).

        Parameters
        ----------
        text : :obj:`str`
            The formatted record (possibly several lines, e.g. a traceback).
        """
        self.log_widget.append_text(text)

    # -- internals ----------------------------------------------------------

    def _on_browse_clicked(self):
        """
        Slot for the
        :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.replay_browse_button`
        ``clicked`` signal: open the native OS directory picker and, unless the
        user cancels (signaled by an empty string, not ``None``), fill it into
        :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.replay_datadir_edit`.
        """
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Select archive directory', self.replay_datadir_edit.text()
        )
        if directory != '':
            self.replay_datadir_edit.setText(directory)

    def _on_slew_browse_clicked(self):
        """
        Slot for the
        :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.slew_browse_button`
        ``clicked`` signal: open the native OS file picker and, unless the user
        cancels (signaled by an empty string, not ``None``), fill the selected
        path into
        :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.slew_file_edit`.
        """
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Select starlist file', self.slew_file_edit.text()
        )
        if path != '':
            self.slew_file_edit.setText(path)
