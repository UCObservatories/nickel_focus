"""Simulated typing, for GUI tests that need real key events."""
from nickel_focus.gui.qt import QtCore, QtGui, QtWidgets


def press(widget, key, text=''):
    """Send one key press (and release) to ``widget``."""
    for event_type in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease):
        event = QtGui.QKeyEvent(event_type, key, QtCore.Qt.KeyboardModifier.NoModifier, text)
        QtWidgets.QApplication.sendEvent(widget, event)


def type_text(spin, text):
    """Replace a spin box's text by typing ``text`` (without pressing Enter)."""
    spin.selectAll()
    for char in text:
        press(spin, QtCore.Qt.Key.Key_unknown, char)


def type_and_enter(spin, text):
    """Type ``text`` into a spin box, then press Enter."""
    type_text(spin, text)
    press(spin, QtCore.Qt.Key.Key_Return)
