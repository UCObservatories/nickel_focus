"""
Test configuration for GUI-specific tests.

Skips the entire `tests/gui/` subtree if PySide6 isn't installed, so the
rest of the test suite (Qt-free) is unaffected either way. Also forces
the offscreen Qt platform plugin, so these tests never need a real
display.
"""
import os

import pytest

pytest.importorskip('PySide6')

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from nickel_focus import log
from nickel_focus.gui.log_handler import QtLogHandler


@pytest.fixture(scope='session')
def qapp():
    """
    The (session-wide, singleton) QApplication needed to create any Qt widget,
    built the same way the GUI builds it (including its widget style).
    """
    from nickel_focus.gui import launcher
    return launcher.build_app()


@pytest.fixture(autouse=True)
def _remove_qt_log_handlers():
    """
    Remove any `QtLogHandler` a test's `Controller` added to the
    `nickel_focus.log` singleton once that test finishes, so a handler
    pointing at a destroyed window never lingers into a later test module.
    """
    yield
    log.remove_handlers_of_type(QtLogHandler)

