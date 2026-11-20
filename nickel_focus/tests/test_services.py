"""
Tests for :mod:`nickel_focus.services`: the boundary between
:mod:`nickel_focus` and :mod:`ktl`, run over the fake ``ktl`` (see
``fake_ktl.py``).
"""
import gc
import logging
import importlib.abc
import importlib.util
import sys

import pytest

from nickel_focus import focus
from nickel_focus import log
from nickel_focus.services import base
from nickel_focus.services import (
    DEFAULT_READ_TIMEOUT, KTLError, KTLReadOnlyError, KTLService, KTLTimeoutError,
    KTLUnavailableError, NickelPoco, NSciCam, ktl_available, require,
)


def test_services_need_ktl():
    # The autouse `no_ktl` fixture sets `services.base.ktl` to None.
    assert not ktl_available(), 'ktl should be unavailable under the no_ktl fixture'
    for service_class in (NickelPoco, NSciCam):
        with pytest.raises(KTLUnavailableError, match='ktl package is not available'):
            service_class()


def test_unavailable_is_a_runtime_error():
    # Callers that catch RuntimeError (as they did before the service layer)
    # keep working.
    assert issubclass(KTLUnavailableError, RuntimeError), \
        'KTLUnavailableError should be a RuntimeError'
    assert issubclass(KTLUnavailableError, KTLError), 'KTLUnavailableError should be a KTLError'


def test_reads_use_the_default_timeout(fake_telescope):
    poco = NickelPoco()
    fake_telescope.keyword('nickelpoco', 'POCSTOP').reads.clear()  # forget the priming read

    assert poco.read('POCSTOP') == 'allowed', 'the read should return the ascii value'
    assert fake_telescope.keyword('nickelpoco', 'POCSTOP').reads == [DEFAULT_READ_TIMEOUT], \
        'the read should use the default timeout'
    assert DEFAULT_READ_TIMEOUT == 1.0, 'the default read timeout should be 1 s'


def test_binary_read(fake_telescope):
    assert NickelPoco().read('POCOT', binary=True) == 0, \
        'a binary read should return the binary value'


def test_read_timeout_raises_our_exception(fake_telescope):
    fake_telescope.keyword('nickelpoco', 'POCSECPA').responsive = False

    with pytest.raises(KTLTimeoutError, match='POCSECPA did not answer') as info:
        NickelPoco().secondary_position()
    assert isinstance(info.value.__cause__, fake_telescope.ktl.TimeoutException), \
        "the ktl exception should be chained, not lost"


def test_ktl_errors_are_translated(fake_telescope, monkeypatch):
    def fail(*args, **kwargs):
        raise fake_telescope.ktl.ktlError('KTL::-1::boom')

    poco = NickelPoco()
    keyword = fake_telescope.keyword('nickelpoco', 'POCSTOP')
    monkeypatch.setattr(keyword, 'read', fail)
    with pytest.raises(KTLError, match='Cannot read nickelpoco.POCSTOP: KTL::-1::boom'):
        poco.read('POCSTOP')


def test_unknown_keyword_is_a_ktl_error(fake_telescope):
    with pytest.raises(KTLError, match='Cannot access nickelpoco.NOSUCH'):
        NickelPoco().read('NOSUCH')


@pytest.mark.parametrize('service, names', [
    ('nickelpoco', ('POCSTOP', 'POCTRCK', 'POCOT', 'POCSECLK', 'POCSECPA', 'POCORAA',
                    'POCODECA', 'POCHEART')),
    ('nscicam', ('EXPSTATE', 'FITSFILE', 'EXPOSURE', 'AMPCONF', 'BINNING', 'DISP0CLK')),
])
def test_services_monitor_and_prime_with_a_timeout(fake_hardware, service, names):
    sim = fake_hardware['sim']
    for name in names:
        keyword = sim.keyword(service, name)
        assert keyword.monitored, f'{service}.{name} should be monitored from construction'
        assert keyword.reads[0] == DEFAULT_READ_TIMEOUT, \
            f"{service}.{name}'s priming read should have a timeout"


def test_expose_is_not_monitored(fake_hardware):
    assert not fake_hardware['sim'].keyword('nscicam', 'EXPOSE').monitored, \
        'EXPOSE is only written, so it should not be monitored'


@pytest.mark.parametrize('service, keyword', [
    ('nickelpoco', 'POCHEART'), ('nscicam', 'DISP0CLK'),
])
def test_heartbeats_are_registered(fake_hardware, service, keyword):
    heartbeats = fake_hardware['sim'].ktl.cache(service).heartbeats
    assert heartbeats == {keyword: 30}, \
        f'{keyword} should be registered as the {service} heartbeat, with its period'


def test_unresponsive_heartbeat_fails_construction(fake_telescope):
    fake_telescope.keyword('nickelpoco', 'POCHEART').responsive = False
    with pytest.raises(KTLTimeoutError, match='POCHEART'):
        NickelPoco()


def test_heartbeat_alive(fake_telescope):
    poco = NickelPoco()
    heart = fake_telescope.keyword('nickelpoco', 'POCHEART')

    assert not poco.heartbeat_alive(timeout=1), 'a heartbeat that never changes is not alive'
    heart.pending = [heart.value + 1]
    assert poco.heartbeat_alive(timeout=1), 'a heartbeat that changes is alive'


def test_heartbeat_alive_needs_a_heartbeat(fake_telescope):
    class NoHeartbeat(KTLService):
        service = 'nickelpoco'

    with pytest.raises(KTLError, match='no heartbeat keyword'):
        NoHeartbeat().heartbeat_alive(timeout=1)


def test_unresponsive_camera_fails_construction_instead_of_hanging(fake_hardware):
    fake_hardware['sim'].keyword('nscicam', 'EXPSTATE').responsive = False
    with pytest.raises(KTLTimeoutError):
        NSciCam()


def test_check_tells_false_from_no_reply(fake_telescope):
    poco = NickelPoco()
    stop = fake_telescope.keyword('nickelpoco', 'POCSTOP')

    assert poco.check('POCSTOP', '== allowed', 0.5) is True, 'a true condition should be True'
    stop.value = 'disabled'
    assert poco.check('POCSTOP', '== allowed', 0.5) is False, \
        'a false condition, with the service answering, should be False'
    stop.responsive = False
    assert poco.check('POCSTOP', '== allowed', 0.5) is None, \
        'a service that does not reply should give None'


def test_require():
    require(True, 'false', 'no reply')  # does not raise
    with pytest.raises(ValueError, match='^false$'):
        require(False, 'false', 'no reply')
    with pytest.raises(KTLTimeoutError, match='^no reply$'):
        require(None, 'false', 'no reply')


def test_wait_for(fake_telescope):
    poco = NickelPoco()
    assert poco.movement_allowed(timeout=0.5), 'movement should be allowed'
    fake_telescope.movement_allowed = False
    assert not poco.movement_allowed(timeout=0.5), 'movement should not be allowed once disabled'


def test_pointing(fake_telescope):
    fake_telescope.ra, fake_telescope.dec = 5.5, -30.25

    coo = NickelPoco().pointing()

    assert coo.ra.hour == pytest.approx(5.5), 'the RA should be read in hours'
    assert coo.dec.deg == pytest.approx(-30.25), 'the Dec should be read in degrees, with its sign'


# -- read-only services --------------------------------------------------------

def test_read_only_write_never_reaches_ktl(fake_telescope):
    poco = NickelPoco(read_only=True)

    with pytest.raises(KTLReadOnlyError, match='read-only'):
        poco.write('POCSECPD', 350.)
    assert fake_telescope.keyword('nickelpoco', 'POCSECPD').writes == [], \
        'a read-only write should never reach the keyword'
    assert poco.read('POCSECPD') == '340.0', 'reads still work'


def test_read_only_device_method_fails_harmlessly(fake_telescope):
    focus_hw = focus.Focus(NickelPoco(read_only=True))

    with pytest.raises(KTLReadOnlyError):
        focus_hw.set_to(350.)
    assert fake_telescope.ktl.writes == [], 'nothing should have been written'


def test_read_only_construction_writes_nothing(fake_hardware):
    NickelPoco(read_only=True)
    NSciCam(read_only=True)
    assert fake_hardware['sim'].ktl.writes == [], \
        'constructing read-only services should write nothing'


# -- importing ktl -------------------------------------------------------------

class _BrokenKtlFinder(importlib.abc.MetaPathFinder):
    """Makes ``import ktl`` fail the way a broken install can (not ModuleNotFoundError)."""

    def find_spec(self, name, path, target=None):
        if name == 'ktl':
            raise ImportError('libktl.so: cannot open shared object file')
        return None


def test_broken_ktl_install_degrades_instead_of_failing(monkeypatch):
    # Load a private copy of services/base.py, so the module the rest of the
    # suite uses (and its exception classes) is left alone.
    monkeypatch.delitem(sys.modules, 'ktl', raising=False)
    monkeypatch.setattr(sys, 'meta_path', [_BrokenKtlFinder(), *sys.meta_path])
    spec = importlib.util.spec_from_file_location('_services_base_copy', base.__file__)
    module = importlib.util.module_from_spec(spec)

    with pytest.warns(UserWarning, match='Unable to import ktl.*libktl.so'):
        spec.loader.exec_module(module)

    assert module.ktl is None, 'a broken ktl import should leave ktl as None'
    assert not module.ktl_available(), 'ktl should be reported as unavailable'


# -- subscriptions -------------------------------------------------------------

def test_subscribe_delivers_current_values_then_broadcasts(fake_telescope):
    received = []
    poco = NickelPoco()

    poco.subscribe(['POCSTOP', 'POCTRCK'], lambda *args: received.append(args))

    assert received == [('nickelpoco', 'POCSTOP', 'allowed'), ('nickelpoco', 'POCTRCK', 'on')], \
        'each keyword\'s current value should be delivered right away'
    received.clear()
    fake_telescope.movement_allowed = False
    assert received == [('nickelpoco', 'POCSTOP', 'disabled')], \
        'a broadcast should be delivered as (service, keyword, ascii value)'


def test_subscription_survives_garbage_collection(fake_telescope):
    # ktl holds callbacks only weakly; the service must keep its own
    # callback wrappers alive, or broadcasts would silently stop arriving.
    received = []
    poco = NickelPoco()
    poco.subscribe(['POCSECPA'], lambda *args: received.append(args[2]))
    received.clear()

    gc.collect()
    fake_telescope.keyword('nickelpoco', 'POCSECPA').value = 351.5

    assert received == ['351.5'], 'the subscription should outlive garbage collection'


def test_subscribe_monitors_and_needs_a_reply(fake_telescope):
    poco = NickelPoco()
    secpd = fake_telescope.keyword('nickelpoco', 'POCSECPD')
    secpd.responsive = False

    with pytest.raises(KTLTimeoutError):
        poco.subscribe(['POCSECPD'], lambda *args: None)
    assert secpd.monitored, 'a subscribed keyword should be monitored'


def test_read_only_service_can_subscribe(fake_telescope):
    received = []
    NickelPoco(read_only=True).subscribe(['POCOT'], lambda *args: received.append(args))
    assert received == [('nickelpoco', 'POCOT', '0')], 'subscribing only reads'
    assert fake_telescope.ktl.writes == [], 'subscribing should write nothing'


# -- KTL's own log -------------------------------------------------------------

@pytest.mark.parametrize('flag, level', [
    ('E', logging.ERROR), ('W', logging.WARNING), ('N', logging.INFO),
    ('I', logging.DEBUG), ('D', logging.DEBUG),
])
def test_ktl_log_lines_are_logged_at_their_level(caplog, flag, level):
    writer = base.KTLLogWriter()

    with caplog.at_level(logging.DEBUG, logger='nickel_focus'):
        writer.write(f'1763412345.123456 {flag}: something happened\n')

    records = [r for r in caplog.records if r.getMessage() == 'ktl: something happened']
    assert [r.levelno for r in records] == [level], \
        f'a KTL {flag!r} message should be logged once, at {logging.getLevelName(level)}'


def test_ktl_log_writer_waits_for_complete_lines(caplog):
    writer = base.KTLLogWriter()

    with caplog.at_level(logging.DEBUG, logger='nickel_focus'):
        writer.write('1763412345.1 W: first ')
        assert not caplog.records, 'an unfinished line should not be logged yet'
        writer.write('half\n1763412345.2 E: second\n')
        writer.flush()

    assert [r.getMessage() for r in caplog.records] == ['ktl: first half', 'ktl: second'], \
        'lines split across writes should be logged whole, in order'


def test_unrecognized_ktl_log_line_is_logged_as_written(caplog):
    with caplog.at_level(logging.DEBUG, logger='nickel_focus'):
        base.KTLLogWriter().write('not a KTL log line\n')

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == \
        [(logging.WARNING, 'ktl: not a KTL log line')], \
        'a line in an unexpected form should be logged as a warning, as written'


def test_ktl_log_is_routed_once_with_the_default_level(fake_telescope):
    NickelPoco()
    NickelPoco(read_only=True)
    fake = fake_telescope.ktl

    assert len(fake.log_destinations) == 1, 'KTL\'s log should be routed once per ktl module'
    assert isinstance(fake.log_destinations[0], base.KTLLogWriter), \
        'KTL\'s log should go to the nickel_focus log'
    assert fake.log_levels and set(fake.log_levels) == {fake.LOG_WARN}, \
        'by default, KTL should pass on its warnings and errors'


def test_ktl_log_level_follows_the_verbosity(fake_telescope):
    log.ktl_level = 'debug'  # as set by --verbosity 3; restored after the test

    NickelPoco()

    assert fake_telescope.ktl.log_levels[-1] == fake_telescope.ktl.LOG_DEBUG, \
        'the KTL level set from the verbosity should be applied'


def test_recording_is_read_and_written_as_yes_and_no(fake_hardware):
    cam = fake_hardware['cam']
    record = fake_hardware['sim'].keyword('nscicam', 'RECORD')

    cam.cfg.set_recording(False)
    assert record.writes[-1] == 'No' and not cam.cfg.recording(), \
        'turning recording off should write No'
    cam.cfg.set_recording(True)
    assert record.writes[-1] == 'Yes' and cam.cfg.recording(), \
        'turning recording on should write Yes'
    cam.cfg.configure(record=False)
    assert record.writes[-1] == 'No', 'configure should write a bool as Yes/No'
    record.value = 'yes'
    assert cam.cfg.recording(), 'RECORD should be compared case-insensitively'
