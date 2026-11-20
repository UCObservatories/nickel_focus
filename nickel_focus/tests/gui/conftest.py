"""
Test configuration for GUI-specific tests.

Skips the entire `tests/gui/` subtree if PySide6 isn't installed, so the
rest of the test suite (Qt-free) is unaffected either way. Also forces
the offscreen Qt platform plugin, so these tests never need a real
display.
"""
import gc
import os

import pytest

pytest.importorskip('PySide6')

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

# Bound once here, not imported at teardown: a test may hide PySide6 from
# imports (see test_qt_shim.py) until its own teardown.
from PySide6 import QtCore, QtWidgets

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
def _settle_qt():
    """
    Once a test finishes, run any Qt events it left pending, then free its
    widgets.

    A test's windows usually sit in reference cycles (e.g. a Controller and
    its window's signals), so Python would otherwise free them at some
    unpredictable later point -- often during a later test.  Any redraw a
    window had scheduled (matplotlib's ``draw_idle`` is a zero-delay Qt
    timer) would then fire on an already-deleted canvas, raising
    "Internal C++ object already deleted" or crashing.  Running the pending
    events first, while the widgets still exist, and then collecting them
    immediately keeps each test's widgets from outliving it.
    """
    yield
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    app.processEvents()
    gc.collect()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture(autouse=True)
def _remove_qt_log_handlers():
    """
    Remove any `QtLogHandler` a test's `Controller` added to the
    `nickel_focus.log` singleton once that test finishes, so a handler
    pointing at a destroyed window never lingers into a later test module.
    """
    yield
    log.remove_handlers_of_type(QtLogHandler)

