"""Tests for :mod:`pkg.logger`."""
import logging

import pytest

from nickel_focus import log
from nickel_focus.pkg.logger import StreamFormatter, clear_text_color
from nickel_focus.scripts.slew_to_nearest import NickelSlewToNearest


def _make_record(level, message):
    return logging.LogRecord(
        name='nickel_focus', level=level, pathname=__file__, lineno=1, msg=message, args=(),
        exc_info=None)


def test_stream_formatter_colors_only_the_level_tag():
    record = _make_record(logging.WARNING, 'careful now')
    text = StreamFormatter().format(record)
    assert '\x1b' in text, 'the console formatter should color the level tag'
    assert clear_text_color(text) == ' [WARNING] - careful now', \
        'without its color codes, the line should read "[LEVEL] - message"'


@pytest.mark.parametrize('verbosity, level, ktl_level', [
    (0, logging.WARNING, 'warn'),
    (1, logging.INFO, 'warn'),
    (2, logging.DEBUG, 'notice'),
    (3, logging.DEBUG, 'debug'),
])
def test_verbosity_levels(verbosity, level, ktl_level):
    assert log.convert_verbosity_to_logging_level(verbosity) == level, \
        f'verbosity {verbosity} should set the log level to {logging.getLevelName(level)}'
    assert log.convert_verbosity_to_ktl_level(verbosity) == ktl_level, \
        f'verbosity {verbosity} should pass on KTL messages at {ktl_level!r}'


def test_verbosity_beyond_3_is_rejected():
    with pytest.raises(ValueError, match='0, 1, 2, or 3'):
        log.convert_verbosity_to_ktl_level(4)


def test_init_log_sets_the_ktl_level():
    NickelSlewToNearest.init_log(NickelSlewToNearest.parse_args(['-v', '3']))
    assert log.ktl_level == 'debug', '-v 3 should pass on all of KTL\'s log'


def test_ktl_level_follows_a_more_verbose_log_file(tmp_path):
    args = NickelSlewToNearest.parse_args(
        ['-v', '1', '--log_file', str(tmp_path / 'run.log'), '--log_level', '2']
    )
    NickelSlewToNearest.init_log(args)
    log.close_file()

    assert log.ktl_level == 'notice', \
        'KTL should pass on what the more verbose log file needs, not just the console'


def test_log_level_without_a_log_file_does_not_raise_the_ktl_level():
    NickelSlewToNearest.init_log(NickelSlewToNearest.parse_args(['-v', '1', '--log_level', '3']))
    assert log.ktl_level == 'warn', '--log_level has no effect without a log file'
