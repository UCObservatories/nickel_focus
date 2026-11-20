"""
Hands KTL keyword values from :mod:`ktl`'s callback thread to the GUI thread.
"""
from nickel_focus.gui.qt import QtCore


class KTLBridge(QtCore.QObject):
    """
    Re-emit keyword values as a Qt signal.

    Pass :meth:`relay` to
    :meth:`~nickel_focus.services.base.KTLService.subscribe`, and connect
    :attr:`keywordChanged` to a slot with a queued connection: :mod:`ktl` calls
    :meth:`relay` on its own callback thread, so the slot must not run there.
    The same pattern as :class:`~nickel_focus.gui.log_handler.QtLogHandler`.

    Parameters
    ----------
    parent : :class:`PySide6.QtCore.QObject`, optional
        Passed through to the base :class:`PySide6.QtCore.QObject` constructor.

    Attributes
    ----------
    keywordChanged : :class:`PySide6.QtCore.Signal`
        Emitted with ``(service, keyword, value)`` for each value received.
    """
    keywordChanged = QtCore.Signal(str, str, object)

    def relay(self, service, keyword, value):
        """Emit :attr:`keywordChanged` (safe to call from any thread)."""
        self.keywordChanged.emit(service, keyword, value)
