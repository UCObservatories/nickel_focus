#!/usr/bin/env python
"""
Time keyword reads.  Read only.

Checks (ktl_interface_refactor.md §5.2):

- Startup time: how long connecting to each service takes (it primes every
  monitored keyword).
- The 1 s default read timeout: how long reads of every keyword we use take
  (min/median/max over --repeat reads).  Run it idle, then again during a
  readout and during a slew.
- Notify flags: a read with a 1 ms timeout should raise a timeout, not
  block; a read that blocks past its timeout means notify isn't set.
"""
import statistics
import time

import _common
from _common import log
from nickel_focus.services import KTLError, KTLTimeoutError

POCO = [
    'POCSTOP', 'POCTRCK', 'POCOT', 'POCORAA', 'POCODECA', 'POCSECPA', 'POCSECPD',
    'POCSECLK', 'POCGDFIN',
]
CAM = [
    'EXPSTATE', 'EXPRESULT', 'FITSFILE', 'RECORD', 'OBSNUM', 'RECORDDIR', 'SCRATCHDIR',
    'FITSPREFIX', 'FITSSUFFIX', 'EXPOSURE', 'AMPCONF', 'AMPCONFLIST', 'BINNING',
]


def time_reads(service, names, repeat):
    """Log min/median/max read times for each keyword."""
    for name in names:
        times = []
        for _ in range(repeat):
            start = time.monotonic()
            try:
                service.read(name)
            except KTLError as e:
                log.info(f'  {service.service}.{name}: read failed: {e}')
                break
            times.append(time.monotonic() - start)
        if times:
            log.info(
                f'  {service.service}.{name:<12} min {min(times)*1e3:7.1f} ms, '
                f'median {statistics.median(times)*1e3:7.1f} ms, max {max(times)*1e3:7.1f} ms'
            )


def check_short_timeout(service, name):
    """Read with a 1 ms timeout: it should time out promptly."""
    start = time.monotonic()
    try:
        service.read(name, timeout=0.001)
        outcome = 'returned a value'
    except KTLTimeoutError:
        outcome = 'timed out'
    except KTLError as e:
        outcome = f'failed: {e}'
    elapsed = time.monotonic() - start
    verdict = 'blocked past its timeout' if elapsed > 0.5 else 'returned promptly'
    log.info(f'  {service.service}.{name}: {outcome} after {elapsed*1e3:.1f} ms ({verdict})')


def main():
    p = _common.parser(__doc__)
    p.add_argument('--repeat', type=int, default=20, help='Reads of each keyword.')
    args = p.parse_args()
    _common.start_log(__file__)

    poco, cam = _common.connect()
    log.info(f'Read times ({args.repeat} reads each):')
    time_reads(poco, POCO, args.repeat)
    time_reads(cam, CAM, args.repeat)
    log.info('Reads with a 1 ms timeout (notify flags):')
    check_short_timeout(poco, 'POCSECPA')
    check_short_timeout(cam, 'EXPOSURE')


if __name__ == '__main__':
    main()
