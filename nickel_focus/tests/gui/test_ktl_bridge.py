"""Tests for :mod:`gui.ktl_bridge`."""
import threading

from nickel_focus.gui.ktl_bridge import KTLBridge
from nickel_focus.gui.qt import QtCore


def test_relay_from_another_thread_reaches_the_gui_thread(qapp):
    # ktl calls subscription callbacks on its own thread; the bridge's
    # queued signal must run the slot on the GUI thread instead.
    bridge = KTLBridge()
    received = []

    def slot(service, keyword, value):
        received.append((service, keyword, value, threading.current_thread()))

    bridge.keywordChanged.connect(slot, QtCore.Qt.ConnectionType.QueuedConnection)
    sender = threading.Thread(target=bridge.relay, args=('nickelpoco', 'POCSTOP', 'allowed'))
    sender.start()
    sender.join()
    assert received == [], 'a queued slot should not run on the sending thread'

    QtCore.QCoreApplication.processEvents()

    assert len(received) == 1, 'the value should be delivered once'
    assert received[0][:3] == ('nickelpoco', 'POCSTOP', 'allowed'), \
        'the slot should receive (service, keyword, value)'
    assert received[0][3] is threading.main_thread(), 'the slot should run on the GUI thread'
