#!/usr/bin/env python
"""
Apply a small, known pointing offset through the guider offsets, using the
same sequence as the focus GUI (NickelPoco.apply_guider_offset), with its
unverified settings given on the command line; optionally mark the telescope
on target, and undo the offset.

Checks (pointing_offset_plan.md §4 and §10; ktl_interface_refactor.md §5.2):

- GUIDER_OFFSET_SIGN, GUIDER_OFFSET_COS_DEC: take an image before and after
  (in the data-acquisition GUI) and see which way, and how far, the stars
  moved.  A positive East offset points the telescope further East, so the
  stars should move West (to higher column numbers).  Repeat at high Dec for
  cos(Dec).
- GUIDER_OFFSET_IN_SERIES: whether --together works, or --series is needed.
- Whether writing POCGDIX/POCGDIY already waits for the move: each write's
  time is logged, against when POCGDFIN reads 0.
- GUIDER_MOVE_TIMEOUT, ON_TARGET_TIMEOUT: how long the move and POCOT take.
- --mark: whether POCGDOT = 1 marks the telescope on target (POCOT = 0).
"""
import time

import _common
from _common import log
from nickel_focus.services import nickelpoco, require


def timed_writes(poco):
    """Log how long each keyword write takes to return."""
    write = poco.write

    def timed_write(name, value, binary=False):
        start = time.monotonic()
        try:
            return write(name, value, binary=binary)
        finally:
            log.info(f'  write {name} = {value}: returned after {time.monotonic() - start:.3f} s')

    poco.write = timed_write


def offset(poco, recorder, east, north, mark):
    """Apply one offset (and mark on target), reporting what was seen."""
    start = recorder.now()
    x, y = poco.guider_offset_radians(east, north)
    log.info(f'Offset {east:+.1f}" E, {north:+.1f}" N: POCGDIX {x:+.3e} rad, POCGDIY {y:+.3e} rad')
    _common.timed('  apply_guider_offset', poco.apply_guider_offset, east, north)
    log.info(f'  POCGDFIN broadcasts: {_since(recorder, "nickelpoco.POCGDFIN", start)}')
    if mark:
        on_target = _common.timed('  mark_guider_on_target', poco.mark_guider_on_target)
        log.info(f'  on target: {on_target}')
        log.info(f'  POCOT broadcasts: {_since(recorder, "nickelpoco.POCOT", start)}')
    log.info(f'  POCGDX = {poco.read("POCGDX")}, POCGDY = {poco.read("POCGDY")}')
    log.info(f'  position: RA {poco.read("POCORAA")}, Dec {poco.read("POCODECA")}')


def _since(recorder, keyword, start):
    return [(round(t - start, 3), v) for t, v in recorder.of(keyword, since=start)]


def main():
    p = _common.parser(__doc__, actions=True)
    p.add_argument('--east', type=float, default=10., help='East offset (arcsec).')
    p.add_argument('--north', type=float, default=0., help='North offset (arcsec).')
    order = p.add_mutually_exclusive_group()
    order.add_argument('--series', action='store_true', help='Arm, write, and wait per axis.')
    order.add_argument('--together', action='store_true', help='Write both, then wait once.')
    p.add_argument('--sign', default=None, help='GUIDER_OFFSET_SIGN, e.g. "1,-1".')
    p.add_argument('--cos-dec', choices=['yes', 'no'], default=None,
                   help='GUIDER_OFFSET_COS_DEC: divide the East offset by cos(Dec).')
    p.add_argument('--timeout', type=float, default=None, help='Both timeouts (s).')
    p.add_argument('--mark', action='store_true', help='Then write POCGDOT = 1 (mark on target).')
    p.add_argument('--undo', action='store_true',
                   help='Then (after a pause for an image) apply the opposite offset.')
    args = p.parse_args()
    _common.start_log(__file__)

    # This run's settings, overriding the module's unverified defaults.
    if args.series or args.together:
        nickelpoco.GUIDER_OFFSET_IN_SERIES = args.series
    if args.sign:
        nickelpoco.GUIDER_OFFSET_SIGN = tuple(int(s) for s in args.sign.split(','))
    if args.cos_dec:
        nickelpoco.GUIDER_OFFSET_COS_DEC = args.cos_dec == 'yes'
    if args.timeout:
        nickelpoco.GUIDER_MOVE_TIMEOUT = nickelpoco.ON_TARGET_TIMEOUT = args.timeout
    for name in ('GUIDER_OFFSET_IN_SERIES', 'GUIDER_OFFSET_SIGN', 'GUIDER_OFFSET_COS_DEC',
                 'GUIDER_MOVE_TIMEOUT', 'ON_TARGET_TIMEOUT'):
        log.info(f'{name} = {getattr(nickelpoco, name)!r}')

    poco, _ = _common.connect(cam=False, read_only=not args.go)
    log.info(f'POCGDX = {poco.read("POCGDX")}, POCGDY = {poco.read("POCGDY")}, '
             f'POCGDRO = {poco.read("POCGDRO")}')
    steps = [f'Offset {args.east:+.1f}" E, {args.north:+.1f}" N.']
    if args.mark:
        steps.append('Write POCGDOT = 1 and wait for POCOT = 0.')
    if args.undo:
        steps.append('Pause for an image, then offset back (and mark, if --mark).')
    _common.plan(args, ['Pause while you take an image, for comparison.'] + steps)

    require(poco.movement_allowed(0.5), 'Telescope movement is disabled.',
            'No reply from nickelpoco.POCSTOP.')
    require(poco.tracking(0.5), 'Tracking is disabled.', 'No reply from nickelpoco.POCTRCK.')
    timed_writes(poco)
    recorder = _common.Recorder(
        [(poco, ['POCGDFIN', 'POCOT', 'POCGDX', 'POCGDY', 'POCORAA', 'POCODECA'])], echo=False
    )
    _common.pause('Take an image now, before the offset.')
    try:
        offset(poco, recorder, args.east, args.north, args.mark)
        if args.undo:
            _common.pause('Take an image now to see where the stars moved.')
            offset(poco, recorder, -args.east, -args.north, args.mark)
    finally:
        recorder.summary()


if __name__ == '__main__':
    main()
