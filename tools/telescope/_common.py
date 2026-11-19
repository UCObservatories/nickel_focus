"""
Shared pieces of the telescope check scripts: logging, the ``--go`` guard,
connecting to the services, timing calls, and recording keyword broadcasts.

Every script logs to the terminal and to ``logs/<script>-<time>.log`` beside
the scripts, so a session's results can be read afterward.
"""
import argparse
from datetime import datetime
import logging
from pathlib import Path
import statistics
import sys
import threading
import time

from nickel_focus.services import NickelPoco, NSciCam

LOG_DIRECTORY = Path(__file__).resolve().parent / 'logs'

log = logging.getLogger('telescope_check')


def parser(description, actions=False):
    """
    An argument parser for a check script.  With ``actions``, it has a
    ``--go`` flag; without it, the script only says what it would do.
    """
    p = argparse.ArgumentParser(
        description=description, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    if actions:
        p.add_argument(
            '--go', action='store_true',
            help='Actually do it.  Without this, only describe what would be done.'
        )
    return p


def start_log(script):
    """
    Log to the terminal and to a new file in :data:`LOG_DIRECTORY`, each
    line timestamped to the millisecond.  Returns the file's path.
    """
    LOG_DIRECTORY.mkdir(exist_ok=True)
    name = Path(script).stem
    path = LOG_DIRECTORY / f'{name}-{datetime.now():%Y%m%d-%H%M%S}.log'
    formatter = logging.Formatter('%(asctime)s.%(msecs)03d  %(message)s', '%H:%M:%S')
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(path)):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info(f'{name}: {" ".join(sys.argv[1:])}')
    log.info(f'Logging to {path}')
    return path


def plan(args, steps):
    """
    Log what the script will do.  Without ``--go``, stop there.
    """
    log.info('Plan:')
    for i, step in enumerate(steps, start=1):
        log.info(f'  {i}. {step}')
    if not args.go:
        log.info('Nothing done.  Rerun with --go to do it.')
        sys.exit(0)


def connect(poco=True, cam=True, read_only=True):
    """The telescope and/or camera services (None for those not asked for), timed."""
    services = []
    for wanted, service_class in ((poco, NickelPoco), (cam, NSciCam)):
        if not wanted:
            services.append(None)
            continue
        service = timed(f'Connecting to {service_class.service}', service_class, read_only=read_only)
        services.append(service)
    return services


def timed(label, function, *args, **kwargs):
    """Call ``function``, log how long it took, and return its result."""
    start = time.monotonic()
    try:
        return function(*args, **kwargs)
    finally:
        log.info(f'{label}: {time.monotonic() - start:.3f} s')


def pause(message):
    """Wait for Enter (e.g. while an image is taken elsewhere)."""
    log.info(message)
    input('  ... press Enter to continue ')


class Recorder:
    """
    Record every broadcast of some keywords, timestamped.

    Subscribing delivers each keyword's current value at once; after that,
    each broadcast (and each completed read of that keyword) is recorded.

    Parameters
    ----------
    subscriptions : :obj:`list`
        ``(service, names)`` pairs: a service object and the keyword names to
        record from it.
    echo : :obj:`bool`, optional
        Log each value as it arrives.
    """

    def __init__(self, subscriptions, echo=True):
        self.echo = echo
        self.start = time.monotonic()
        self.events = []  # (seconds since start, 'service.KEYWORD', value)
        self._lock = threading.Lock()
        for service, names in subscriptions:
            service.subscribe(names, self._record)

    def _record(self, service, keyword, value):
        t = time.monotonic() - self.start
        with self._lock:
            self.events.append((t, f'{service}.{keyword}', value))
        if self.echo:
            log.info(f'  [{t:8.3f} s] {service}.{keyword} = {value!r}')

    def now(self):
        """Seconds since recording started, on the events' clock."""
        return time.monotonic() - self.start

    def of(self, keyword, since=0.):
        """The ``(time, value)`` events of ``keyword`` (``'service.KEYWORD'``) since ``since``."""
        with self._lock:
            return [(t, v) for t, k, v in self.events if k == keyword and t >= since]

    def summary(self):
        """Log, for each keyword, how many values arrived and how often."""
        log.info('Broadcast summary (the first value of each is from subscribing):')
        with self._lock:
            keywords = sorted({k for _, k, _ in self.events})
            events = list(self.events)
        for keyword in keywords:
            times = [t for t, k, _ in events if k == keyword]
            values = {v for _, k, v in events if k == keyword}
            text = f'  {keyword}: {len(times)} values, {len(values)} distinct'
            if len(times) > 2:
                gaps = [b - a for a, b in zip(times[1:], times[2:])] or [times[-1] - times[1]]
                text += (
                    f'; interval after the first: median {statistics.median(gaps):.3f} s, '
                    f'min {min(gaps):.3f} s, max {max(gaps):.3f} s'
                )
            log.info(text)
