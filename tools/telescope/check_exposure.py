#!/usr/bin/env python
"""
Take a short exposure, timing each step, and optionally check unrecorded
exposures and whether the data-acquisition GUI follows setting changes.
The camera's settings (exposure time, speed, binning, RECORD, OBJECT) are
restored afterward.

Checks (ktl_interface_refactor.md §5.1 #2 and §5.2):

- Exposure completion: whether EXPSTATE is seen passing through Start, and
  how long after EXPSTATE returns to Ready FITSFILE names the new file
  (against Exposure.FILE_TIMEOUT, 30 s).
- What write "completion" means: how long each settings write, and the
  EXPOSE write, take to return.
- --unrecorded N: N exposures with RECORD = No.  Does FITSFILE change each
  time, and do the scratch names repeat (OBSNUMMAX)?
- --settings: change the exposure time, speed, and binning one at a time,
  pausing so you can check whether the data-acquisition GUI shows each.
"""
import _common
from _common import log

# A second value for each setting, for --settings.
OTHER = {'exptime': 2.0, 'speed': {'Fast': 'Slow'}, 'binning': {'1,1': '2,2', '2,2': '4,4'}}


def expose(cam, recorder, exptime, timeout):
    """Take one exposure, step by step, and report what was seen."""
    start = recorder.now()
    previous = cam.fits_file()
    _common.timed('EXPOSE = Go (write returned)', cam.start_exposure)
    ended = _common.timed(
        'Waiting for EXPSTATE Ready/Error', cam.exposure_ended, timeout=exptime + 90.
    )
    changed = _common.timed(
        'Waiting for FITSFILE to change', cam.fits_file_changed, previous, timeout=timeout
    )
    states = recorder.of('nscicam.EXPSTATE', since=start)
    files = recorder.of('nscicam.FITSFILE', since=start)
    log.info(f'  EXPSTATE sequence: {[v for _, v in states]}')
    log.info(f'  Start seen: {any(v.strip().lower() == "start" for _, v in states)}')
    ready = [t for t, v in states if v.strip().lower() in ('ready', 'error')]
    new_files = [(t, v) for t, v in files if v != previous]
    if ready and new_files:
        log.info(f'  FITSFILE changed {new_files[0][0] - ready[-1]:+.3f} s after EXPSTATE ended')
    log.info(
        f'  ended: {ended}; FITSFILE changed: {changed}; FITSFILE = {cam.fits_file()!r}; '
        f'EXPRESULT = {cam.exposure_result()!r}'
    )
    return cam.fits_file()


def main():
    p = _common.parser(__doc__, actions=True)
    p.add_argument('--exptime', type=float, default=1.0, help='Exposure time (s).')
    p.add_argument('--speed', default='Fast', help='Read speed (Fast or Slow).')
    p.add_argument('--binning', default='4,4', help='Binning (1,1, 2,2, or 4,4).')
    p.add_argument('--unrecorded', type=int, default=0, metavar='N',
                   help='Also take N exposures with RECORD = No.')
    p.add_argument('--settings', action='store_true',
                   help='Also change each setting in turn, for the data-acquisition GUI check.')
    p.add_argument('--file-timeout', type=float, default=30.,
                   help='Seconds to wait for FITSFILE to change.')
    args = p.parse_args()
    _common.start_log(__file__)

    _, cam = _common.connect(poco=False, read_only=not args.go)
    original = cam.cfg.current_settings()
    original_record = cam.cfg.recording()
    original_object = cam.read('OBJECT')
    log.info(f'Current settings: {original}, recording {original_record}, '
             f'OBJECT {original_object!r}')

    steps = [
        f'Set exposure time {args.exptime} s, speed {args.speed}, binning {args.binning}, '
        'RECORD Yes, OBJECT "nickel_focus check", timing each write.',
        'Take one exposure, timing each step.',
    ]
    if args.settings:
        steps.append('Change the exposure time, speed, and binning in turn, pausing after each.')
    if args.unrecorded:
        steps.append(f'Take {args.unrecorded} exposures with RECORD = No.')
    steps.append('Restore the original settings.')
    _common.plan(args, steps)

    recorder = _common.Recorder([(
        cam, ['EXPSTATE', 'FITSFILE', 'FILENAME', 'OBSNUM', 'EXPRESULT', 'RECORD']
    )])
    cfg = cam.cfg
    try:
        log.info('Settings writes:')
        _common.timed('  EXPOSURE', cfg.configure, exptime=args.exptime)
        _common.timed('  AMPCONF', cfg.configure, speed=args.speed)
        _common.timed('  BINNING', cfg.configure, binning=args.binning)
        _common.timed('  RECORD', cfg.configure, record=True)
        _common.timed('  OBJECT', cfg.configure, object_name='nickel_focus check')

        if args.settings:
            changes = [
                ('exptime', args.exptime + OTHER['exptime']),
                ('speed', OTHER['speed'].get(args.speed, 'Fast')),
                ('binning', OTHER['binning'].get(args.binning, '1,1')),
            ]
            for key, value in changes:
                _common.timed(f'  {key} = {value}', cfg.configure, **{key: value})
                _common.pause(f'Does the data-acquisition GUI now show {key} = {value}?')
                cfg.configure(**{key: getattr(args, key)})

        log.info('Recorded exposure:')
        expose(cam, recorder, args.exptime, args.file_timeout)

        if args.unrecorded:
            log.info(f'OBSNUMMAX = {cam.read("OBSNUMMAX")!r}')
            _common.timed('  RECORD = No', cfg.set_recording, False)
            names = []
            for i in range(args.unrecorded):
                log.info(f'Unrecorded exposure {i + 1}/{args.unrecorded}:')
                names.append(expose(cam, recorder, args.exptime, args.file_timeout))
            repeats = len(names) - len(set(names))
            log.info(f'Unrecorded file names: {names}')
            log.info(f'  {repeats} repeated name(s) among {len(names)} exposures')
    finally:
        log.info('Restoring the original settings.')
        cfg.configure(record=original_record, object_name=original_object, **original)
        recorder.summary()


if __name__ == '__main__':
    main()
