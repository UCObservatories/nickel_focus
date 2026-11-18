"""
The :class:`PySide6.QtCore.QThread` engine that drives a
:meth:`~nickel_focus.slew.NickelTelescopePointing.apply_offset` call off the
GUI thread.

See :mod:`~nickel_focus.gui.model.slew_worker`: applying an offset waits for
the guider move to finish and for the telescope to report being on target,
which would freeze the interface if run on the GUI's event-loop thread.
"""
from nickel_focus.gui.qt import QtCore


class PointingOffsetWorker(QtCore.QThread):
    """
    Drive one :meth:`~nickel_focus.slew.NickelTelescopePointing.apply_offset`
    call on a background thread.  Like
    :class:`~nickel_focus.gui.model.slew_worker.SlewWorker`, a new one is
    created for each offset.

    Parameters
    ----------
    telescope : :class:`~nickel_focus.slew.NickelTelescopePointing`
        The telescope to offset.
    east, north : :obj:`float`
        The offset, in arcseconds on the sky, passed through unchanged to
        :meth:`~nickel_focus.slew.NickelTelescopePointing.apply_offset`.

    Attributes
    ----------
    offsetFinished : :class:`PySide6.QtCore.Signal`
        Emitted once the offset has been applied.
    offsetFailed : :class:`PySide6.QtCore.Signal`
        Emitted once, with ``(message, exc)``, if
        :meth:`~nickel_focus.slew.NickelTelescopePointing.apply_offset` raises;
        ``message`` is human-readable and ``exc`` is the raised
        :obj:`Exception`, so its traceback can be logged.
    """
    offsetFinished = QtCore.Signal()
    offsetFailed = QtCore.Signal(str, object)

    def __init__(self, telescope, east, north, parent=None):
        super().__init__(parent)
        self.telescope = telescope
        self.east = east
        self.north = north

    def run(self):
        """
        :class:`PySide6.QtCore.QThread`'s entry point: runs
        ``telescope.apply_offset(east, north)`` on the background thread,
        reporting every failure through ``offsetFailed``.
        """
        try:
            self.telescope.apply_offset(self.east, self.north)
        except Exception as e:
            self.offsetFailed.emit(f'Could not apply the pointing offset: {e}', e)
            return
        self.offsetFinished.emit()
