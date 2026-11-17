"""Tests for :mod:`gui.model.slew_worker`."""
import types

from astropy.coordinates import Angle
import pytest

from nickel_focus import slew
from nickel_focus.gui.model.slew_worker import SlewWorker
from nickel_focus.gui.qt import QtCore
from nickel_focus.services import NickelPoco


def _run_and_collect(worker, timeout_ms=5000):
    """
    Start ``worker``, pump the event loop until it finishes, and return
    the lists of ``(slewFinished, slewFailed)`` payloads it emitted.

    See `test_focus_worker._run_and_collect` for why a
    ``DirectConnection`` is used here.
    """
    finished, failed = [], []
    direct = QtCore.Qt.ConnectionType.DirectConnection
    worker.slewFinished.connect(lambda: finished.append(True), direct)
    worker.slewFailed.connect(lambda msg, exc: failed.append(msg), direct)

    loop = QtCore.QEventLoop()
    worker.finished.connect(loop.quit)
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    worker.start()
    loop.exec()

    return finished, failed


def test_slew_finished_is_emitted_on_success(qapp, fake_telescope):
    telescope = slew.NickelTelescopePointing(NickelPoco())
    worker = SlewWorker(telescope, Angle(30., unit='deg'), Angle(10., unit='deg'))

    finished, failed = _run_and_collect(worker)

    assert finished == [True], 'a successful slew should emit slewFinished exactly once'
    assert failed == [], 'a successful slew should never emit slewFailed'
    assert fake_telescope.slew_calls == [(pytest.approx(2.0), pytest.approx(10.0))], \
        'the worker should have slewed to the given ra/dec (30 deg = 2 h)'


def test_slew_failed_is_emitted_when_movement_is_disabled(qapp, fake_telescope):
    fake_telescope.movement_allowed = False
    telescope = slew.NickelTelescopePointing(NickelPoco())
    worker = SlewWorker(telescope, Angle(30., unit='deg'), Angle(10., unit='deg'))

    finished, failed = _run_and_collect(worker)

    assert finished == [], 'a failed slew should never emit slewFinished'
    assert len(failed) == 1, 'a failed slew should emit slewFailed exactly once'
    assert 'disabled' in failed[0], 'the failure message should explain why the slew failed'


def test_slew_failed_is_emitted_for_an_unexpected_error(qapp):
    def slew_to(ra, dec):
        raise RuntimeError('keyword write failed')

    broken = types.SimpleNamespace(slew_to=slew_to)
    worker = SlewWorker(broken, Angle(30., unit='deg'), Angle(10., unit='deg'))

    finished, failed = _run_and_collect(worker)

    assert finished == [], 'a failed slew should never emit slewFinished'
    assert len(failed) == 1, 'any error, not just a ValueError, should emit slewFailed'
    assert 'keyword write failed' in failed[0], 'the failure message should include the error'
