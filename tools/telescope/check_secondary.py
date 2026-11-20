#!/usr/bin/env python
"""
Move the secondary (focus) by a small step and back, timing each part.

Checks (ktl_interface_refactor.md §5.2): what write "completion" means for
POCSECPD -- whether the write returns at once or only once the secondary
has moved -- against the secondary relocking (POCSECLK = on), to set a
write timeout.  The camera must not be exposing.
"""
import _common
from _common import log
from nickel_focus.focus import Focus
from nickel_focus.services import require


def move(poco, recorder, target):
    """Unlock, write POCSECPD, and wait for the relock, timing each step."""
    start = recorder.now()
    _common.timed('  POCSECLK = off (write returned)', poco.set_secondary_lock, False)
    _common.timed(f'  POCSECPD = {target} (write returned)', poco.set_secondary_demand, target)
    locked = _common.timed('  Waiting for POCSECLK = on', poco.secondary_locked, timeout=30)
    log.info(f'  relocked: {locked}; POCSECPA = {poco.secondary_position()}')
    positions = recorder.of('nickelpoco.POCSECPA', since=start)
    if positions:
        log.info(f'  POCSECPA broadcasts: {[(round(t - start, 3), v) for t, v in positions]}')


def main():
    p = _common.parser(__doc__, actions=True)
    p.add_argument('--step', type=float, default=2., help='Focus step (focus units).')
    args = p.parse_args()
    _common.start_log(__file__)

    poco, cam = _common.connect(read_only=not args.go)
    original = poco.secondary_demand()
    log.info(f'POCSECPA = {poco.secondary_position()}, POCSECPD = {original}')
    _common.plan(args, [
        f'Move the focus to {original + args.step} and time it.',
        f'Move it back to {original} and time it.',
    ])
    require(
        cam.exposure_state_is('Ready', 0.5), 'The camera is exposing; try again when it is Ready.',
        'No reply from nscicam.EXPSTATE.'
    )
    require(
        poco.movement_allowed(0.5), 'Telescope movement is disabled.',
        'No reply from nickelpoco.POCSTOP.'
    )
    recorder = _common.Recorder([(poco, ['POCSECPA', 'POCSECPD', 'POCSECLK'])])
    try:
        log.info(f'Out to {original + args.step}:')
        move(poco, recorder, original + args.step)
    finally:
        log.info(f'Back to {original}:')
        Focus(poco).set_to(original)
        log.info(f'  POCSECPA = {poco.secondary_position()}')
        recorder.summary()


if __name__ == '__main__':
    main()
