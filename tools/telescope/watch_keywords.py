#!/usr/bin/env python
"""
Log every broadcast of a set of keywords, then summarize how often each
arrived.  Read only.  Stop with Ctrl-C, or after --seconds.

Run it in a second terminal while running another check, or while doing
something in the data-acquisition GUI or POCO.

Checks (ktl_interface_refactor.md §5.1 and §5.2):

- Heartbeat periods (POCHEART, DISP0CLK): set heartbeat_period from them.
- The position broadcast rate (POCORAA/POCODECA), against the position
  display's 200 ms redraw limit.
- That callbacks deliver values on the real ktl.
- Whether EXPSTATE passes through Start during an exposure (§5.1 #2), and
  when FITSFILE changes relative to EXPSTATE returning to Ready.
- What POCGDFIN and POCOT do during a guider offset.
- What an exposure that fails puts in EXPSTATE and EXPRESULT, if one does.

Keywords are given as SERVICE.KEYWORD (nickelpoco or nscicam).
"""
import time

import _common
from _common import log

DEFAULT = [
    'nickelpoco.POCHEART', 'nickelpoco.POCORAA', 'nickelpoco.POCODECA', 'nickelpoco.POCOT',
    'nickelpoco.POCGDFIN', 'nscicam.DISP0CLK', 'nscicam.EXPSTATE', 'nscicam.FITSFILE',
    'nscicam.FILENAME', 'nscicam.OBSNUM', 'nscicam.RECORD', 'nscicam.EXPRESULT',
]


def main():
    p = _common.parser(__doc__)
    p.add_argument('keywords', nargs='*', default=DEFAULT, help='SERVICE.KEYWORD names.')
    p.add_argument('--seconds', type=float, default=None, help='Stop after this long.')
    p.add_argument('--quiet', action='store_true', help='Only print the summary.')
    args = p.parse_args()
    _common.start_log(__file__)

    by_service = {}
    for item in args.keywords:
        service, _, keyword = item.partition('.')
        by_service.setdefault(service, []).append(keyword)
    unknown = set(by_service) - {'nickelpoco', 'nscicam'}
    if unknown:
        p.error(f'unknown service(s): {", ".join(sorted(unknown))}')
    poco, cam = _common.connect(poco='nickelpoco' in by_service, cam='nscicam' in by_service)
    services = {'nickelpoco': poco, 'nscicam': cam}

    recorder = _common.Recorder(
        [(services[s], names) for s, names in by_service.items()], echo=not args.quiet
    )
    log.info('Recording; Ctrl-C to stop.')
    try:
        while args.seconds is None or recorder.now() < args.seconds:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    recorder.summary()


if __name__ == '__main__':
    main()
