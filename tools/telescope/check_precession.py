#!/usr/bin/env python
"""
Compare a catalog target's J2000 and apparent coordinates, and (with --go)
where the telescope ends up when slewed to it as the focus GUI does.

Checks (ktl_interface_refactor.md §5.1 #4): POCORAD/POCODECD are described
as apparent coordinates, but the GUI writes catalog (J2000) coordinates to
them.  If so, the telescope stops short of the target by the precession
since J2000 (about 20 arcmin).  After the slew, the telescope's position
(POCORAA/POCODECA) is compared with both; check an image too, to see where
the star actually is.

The target is the one nearest the telescope in the packaged pointing/focus
catalog (or --file), as the GUI's "Find nearest" chooses it, optionally
among names containing --name; or --ra/--dec (J2000).

astropy's automatic download of Earth-orientation tables is turned off (it
can hang without a network), and its bundled tables are used however old:
their error is far below what this check looks for (tens of arcminutes).
"""
from astropy.coordinates import Angle, EarthLocation, SkyCoord, TETE
from astropy.time import Time
from astropy.utils import iers
import astropy.units as u

import _common
from _common import log
from nickel_focus import slew

iers.conf.auto_download = False
iers.conf.auto_max_age = None

# Lick Observatory, if the telescope's own POCLAT/POCLON can't be read.
LICK = EarthLocation(lat=37.3414 * u.deg, lon=-121.6429 * u.deg, height=1283 * u.m)


def target(args, poco):
    """The target's name and J2000 SkyCoord."""
    if args.ra is not None:
        return 'given', SkyCoord(args.ra, args.dec, unit=('hourangle', 'deg'), frame='icrs')
    current = slew.NickelTelescopePointing(poco).current
    name, ra, dec = slew.find_nearest_target(current, obj_search_str=args.name, file=args.file)
    return str(name), SkyCoord(ra, dec, frame='icrs')


def location(poco):
    """The telescope's location, from POCLAT/POCLON if they can be read."""
    try:
        lat = Angle(float(poco.read('POCLAT', binary=True)), unit='rad')
        lon = Angle(float(poco.read('POCLON', binary=True)), unit='rad')
    except Exception as e:
        log.info(f'Using the catalog location of Lick Observatory ({e}).')
        return LICK
    log.info(f'Telescope location: lat {lat.deg:.4f} deg, lon {lon.deg:.4f} deg')
    return EarthLocation(lat=lat, lon=lon, height=1283 * u.m)


def show(label, coord):
    log.info(
        f'  {label:<26} RA {coord.ra.to_string(unit="hourangle", sep=":", precision=2, pad=True)}'
        f'  Dec {coord.dec.to_string(unit="deg", sep=":", precision=1, alwayssign=True, pad=True)}'
    )


def main():
    p = _common.parser(__doc__, actions=True)
    p.add_argument('--name', default=None, help='Only targets whose names contain this.')
    p.add_argument('--file', help='A starlist file instead of the packaged catalog.')
    p.add_argument('--ra', help='Target RA (J2000), e.g. 05:30:00; with --dec.')
    p.add_argument('--dec', help='Target Dec (J2000), e.g. +20:15:00; with --ra.')
    args = p.parse_args()
    if (args.ra is None) != (args.dec is None):
        p.error('give both --ra and --dec, or neither')
    _common.start_log(__file__)

    poco, _ = _common.connect(cam=False, read_only=not args.go)
    name, j2000 = target(args, poco)
    now = Time.now()
    apparent = j2000.transform_to(TETE(obstime=now, location=location(poco)))
    # The apparent RA/Dec as numbers, to compare with J2000 ones directly
    # (separation() would transform it back to J2000 first).
    apparent_values = SkyCoord(apparent.ra, apparent.dec, frame='icrs')
    log.info(f'Target {name!r} at {now.isot}:')
    show('J2000', j2000)
    show('apparent (true equinox)', apparent)
    log.info(f'  J2000 to apparent: {j2000.separation(apparent_values).arcmin:.1f} arcmin')

    _common.plan(args, [
        f'Slew to {name!r} as the focus GUI does: its J2000 coordinates written to '
        'POCORAD/POCODECD.',
        'Compare the telescope position (POCORAA/POCODECA) with the J2000 and apparent '
        'coordinates.',
    ])
    telescope = slew.NickelTelescopePointing(poco)
    _common.timed('Slew', telescope.slew_to, j2000.ra, j2000.dec)
    position = telescope.current
    show('telescope (POCORAA/DECA)', position)
    log.info(f'  from J2000:    {position.separation(j2000).arcmin:.2f} arcmin')
    log.info(f'  from apparent: {position.separation(apparent_values).arcmin:.2f} arcmin')
    log.info('Now take an image to see where the star actually is.')


if __name__ == '__main__':
    main()
