"""
A one-line bar holding the Quit button and the hardware status indicators.
"""
from nickel_focus.gui.qt import QtCore, QtWidgets


class StatusIndicator(QtWidgets.QWidget):
    """
    One status indicator: a colored circle followed by left-aligned text.

    The circle is a small fixed-size :class:`PySide6.QtWidgets.QLabel` drawn
    round by a style sheet (``border-radius`` of half its width); the text is
    an ordinary :class:`PySide6.QtWidgets.QLabel`.  Any width beyond what the
    two need is left empty to the right of the text, so each circle stays
    visibly next to its own text however wide the indicator is stretched.

    Parameters
    ----------
    text : :obj:`str`
        The initial text.
    parent : :class:`PySide6.QtWidgets.QWidget`, optional
        Passed through to the base :class:`PySide6.QtWidgets.QWidget`
        constructor.

    Attributes
    ----------
    dot : :class:`PySide6.QtWidgets.QLabel`
        The colored circle.
    label : :class:`PySide6.QtWidgets.QLabel`
        The text.
    """

    def __init__(self, text, parent=None):
        super().__init__(parent)
        # Sized to the text's font, so the circle scales with it (e.g. on a
        # display with a larger default font); kept even so it's round.
        diameter = 2 * round(0.35 * self.fontMetrics().height())
        self.dot = QtWidgets.QLabel()
        self.dot.setFixedSize(diameter, diameter)
        self.label = QtWidgets.QLabel(text)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def text(self):
        """The indicator's current text."""
        return self.label.text()

    def set_state(self, state, color, text):
        """
        Show ``state`` as ``color`` and replace the text.

        Parameters
        ----------
        state : :obj:`str`
            The state's name, stored as the ``'state'`` dynamic property.
        color : :obj:`str`
            The circle's fill color (any color a Qt style sheet accepts).
        text : :obj:`str`
            The new text.
        """
        self.label.setText(text)
        # A Qt "dynamic property": arbitrary named state attached to the
        # widget, used here so the current state can be queried (e.g. by
        # tests) without parsing it back out of the style sheet.
        self.setProperty('state', state)
        # Qt style sheets use CSS-like syntax to style a single widget; a
        # border radius of half the (square) widget's width draws a circle.
        radius = self.dot.width() // 2
        self.dot.setStyleSheet(f'background-color: {color}; border-radius: {radius}px;')


class StatusBar(QtWidgets.QWidget):
    """
    A Quit button followed by one indicator per piece of hardware state an
    observer needs at a glance, all on a single row.

    Each indicator is a
    :class:`~nickel_focus.gui.views.status_bar.StatusIndicator` -- a colored
    circle next to left-aligned text -- whose color reflects one of three
    states: ``'ok'`` (green), ``'warn'`` (yellow), or ``'error'`` (red), set by
    :meth:`~nickel_focus.gui.views.status_bar.StatusBar.set_indicator`.  Which
    hardware value maps to which state is the Controller's job (see
    :meth:`~nickel_focus.gui.controller.Controller._show_indicator`);
    this view only displays what it's told.  Every indicator starts out red,
    which is also what it shows whenever the hardware state can't be read
    (e.g., no ``ktl`` connection).

    Attributes
    ----------
    quitRequested : :class:`PySide6.QtCore.Signal`
        Emitted when the Quit button is clicked.
    INDICATORS : :obj:`dict`
        Each indicator's key (as passed to
        :meth:`~nickel_focus.gui.views.status_bar.StatusBar.set_indicator`)
        mapped to its default text, in display order.
    STATE_COLORS : :obj:`dict`
        Each indicator state mapped to its circle's color.
    """
    quitRequested = QtCore.Signal()

    INDICATORS = {
        'movement': 'Slew',
        'track': 'Track',
        'target': 'On Target',
        'focus': 'Focus',
        'exp': 'Exp',
    }

    STATE_COLORS = {
        'ok': "#009808",     # green
        'warn': "#fee902",   # yellow
        'error': "#fb0400",  # red
    }

    def __init__(self, parent=None):
        super().__init__(parent)

        self.quit_button = QtWidgets.QPushButton('Quit')
        self.quit_button.clicked.connect(self.quitRequested.emit)

        # Margins are left to whatever contains this bar (see MainWindow,
        # which lines its edges up with the image panel below it).
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.quit_button)

        self.indicators = {}
        for key, text in self.INDICATORS.items():
            indicator = StatusIndicator(text)
            self.indicators[key] = indicator
            # Equal stretch factors share the row's leftover width evenly,
            # so the indicators stay the same width as one another.
            layout.addWidget(indicator, 1)
            self.set_indicator(key, 'error')

    def set_running(self, running):
        """
        Disable the Quit button while an operation (a sequence, single exposure,
        reanalysis, or slew) is running, and re-enable it once nothing is.
        Quitting mid-operation would tear down the worker thread while it's
        still running, and couldn't stop the exposure or slew itself anyway.
        The indicators are unaffected.
        """
        self.quit_button.setEnabled(not running)

    def set_indicator(self, key, state, text=None):
        """
        Set one indicator's color and, optionally, its text.

        Parameters
        ----------
        key : :obj:`str`
            Which indicator; one of
            :attr:`~nickel_focus.gui.views.status_bar.StatusBar.INDICATORS`.
        state : :obj:`str`
            ``'ok'``, ``'warn'``, or ``'error'``; see
            :attr:`~nickel_focus.gui.views.status_bar.StatusBar.STATE_COLORS`.
        text : :obj:`str`, optional
            New text for the indicator.  If ``None``, the indicator's default
            text (from
            :attr:`~nickel_focus.gui.views.status_bar.StatusBar.INDICATORS`) is
            shown.
        """
        self.indicators[key].set_state(
            state, self.STATE_COLORS[state], self.INDICATORS[key] if text is None else text
        )
