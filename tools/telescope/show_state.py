#!/usr/bin/env python
"""
Print every keyword the focus software uses, as ascii and binary.  Read only.

Checks:

- The Dec sign (ktl_interface_refactor.md §5.1 #1): with the telescope
  pointed between 0 and -1 deg Dec, does POCODECA's ascii value keep its
  minus sign?  The binary value (radians) is compared, and a disagreement is
  flagged.
- Scratch file naming (§5.2): OBSNUM, OBSNUMMAX, FILENAME, FITSFILE, and the
  directories and prefixes, with RECORD.
"""
from astropy.coordinates import Angle

import _common
from _common import log
from nickel_focus.services import KTLError

POCO = [
    'POCSTOP', 'POCTRCK', 'POCOT', 'POCORAA', 'POCODECA', 'POCORAD', 'POCODECD',
    'POCSECPA', 'POCSECPD', 'POCSECLK', 'POCHEART', 'POCGDFIN', 'POCGDX', 'POCGDY',
    'POCGDRO', 'POCLAT', 'POCLON',
]
CAM = [
    'EXPSTATE', 'EXPRESULT', 'FITSFILE', 'FILENAME', 'RECORD', 'OBSNUM', 'OBSNUMMAX',
    'RECORDDIR', 'SCRATCHDIR', 'FITSPREFIX', 'FITSSUFFIX', 'EXPOSURE', 'AMPCONF',
    'AMPCONFLIST', 'BINNING', 'OBJECT', 'DISP0CLK',
]


def show(service, names):
    """Log each keyword's ascii and binary value."""
    values = {}
    for name in names:
        try:
            ascii_value = service.read(name)
            binary_value = service.read(name, binary=True)
        except KTLError as e:
            log.info(f'  {service.service}.{name:<12} could not be read: {e}')
            continue
        values[name] = (ascii_value, binary_value)
        log.info(f'  {service.service}.{name:<12} ascii={ascii_value!r:<30} binary={binary_value!r}')
    return values


def main():
    args = _common.parser(__doc__).parse_args()
    _common.start_log(__file__)
    poco, cam = _common.connect()

    poco_values = show(poco, POCO)
    show(cam, CAM)

    if 'POCODECA' in poco_values:
        ascii_value, binary_value = poco_values['POCODECA']
        try:
            ascii_dec = Angle(ascii_value, unit='deg')
            binary_dec = Angle(float(binary_value), unit='rad')
        except (TypeError, ValueError) as e:
            log.info(f'POCODECA: cannot compare the ascii and binary Dec ({e}).')
            return
        log.info(f'POCODECA: ascii {ascii_dec.deg:+.5f} deg, binary {binary_dec.deg:+.5f} deg')
        if abs(ascii_dec.deg - binary_dec.deg) > 1 / 60:
            log.info('  *** The ascii and binary Dec disagree (e.g. a lost minus sign). ***')
        else:
            log.info('  The ascii and binary Dec agree.')


if __name__ == '__main__':
    main()
