"""Tests for :mod:`gui.log_handler`."""
import logging

from nickel_focus.gui.log_handler import QtLogHandler
from nickel_focus.gui.qt import QtCore
from nickel_focus.pkg.logger import StreamFormatter


def test_emit_forwards_formatted_text_via_signal(qapp):
    logger = logging.getLogger('test_qt_log_handler')
    logger.setLevel(logging.INFO)
    handler = QtLogHandler()
    handler.setFormatter(StreamFormatter())
    logger.addHandler(handler)

    received = []
    handler.record_logged.connect(received.append)
    logger.info('hello')
    QtCore.QCoreApplication.processEvents()
    logger.removeHandler(handler)

    record = logging.LogRecord('test', logging.INFO, __file__, 1, 'hello', (), None)
    assert received == [StreamFormatter().format(record)], \
        'handler should emit the formatted text via record_logged'
