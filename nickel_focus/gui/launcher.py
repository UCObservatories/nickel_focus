"""
GUI entry point.

Run via the installed ``nickel_focus_gui`` console script (see
:class:`~nickel_focus.scripts.focus_gui.NickelFocusGUI`), from an environment
with PySide6 installed (see :mod:`~nickel_focus.gui.qt` and the ``gui`` extra in
``pyproject.toml``).
"""
from nickel_focus.gui.qt import QtWidgets
from nickel_focus.gui.views.main_window import MainWindow


def build_app():
    """
    Return the (singleton) :class:`PySide6.QtWidgets.QApplication`, creating it
    first if one doesn't already exist.

    Constructed with no command-line arguments: by the time this runs,
    :class:`~nickel_focus.scripts.focus_gui.NickelFocusGUI` has already parsed
    ``sys.argv`` itself via :mod:`argparse` (which accepts no arguments of its
    own), so there is never a leftover Qt-specific flag (e.g.  ``-style``) for
    :class:`PySide6.QtWidgets.QApplication` to consume.

    The widget style is always set to Qt's cross-platform "Fusion" style, not
    left to the platform default.  The GUI is deployed on Linux, where Fusion is
    already the default; forcing it everywhere means it looks the same on a
    developer's Mac (whose native style pads buttons and combo boxes noticeably
    differently) and in the test suite.
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    app.setStyle('Fusion')
    return app


def build_window():
    """
    Construct the main window (panels only; unwired -- see
    :class:`~nickel_focus.scripts.focus_gui.NickelFocusGUI`).
    """
    return MainWindow()
