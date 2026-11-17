"""
Pointing the Nickel telescope (:class:`NickelTelescopePointing`), plus a helper
to locate the nearest known target in a starlist.
"""

from pathlib import Path

from astropy.coordinates import Angle, SkyCoord
import numpy as np

from nickel_focus import log
from nickel_focus import starlist


class NickelTelescopePointing:
    """
    Read and command the telescope's pointing.

    Parameters
    ----------
    poco : :class:`~nickel_focus.services.nickelpoco.NickelPoco`
        The telescope service.
    """
    def __init__(self, poco):
        self.poco = poco

    @property
    def current(self):
        """
        The telescope's current pointing (see
        :meth:`~nickel_focus.services.nickelpoco.NickelPoco.pointing`).

        Returns
        -------
        astropy.coordinates.SkyCoord
            The current RA/Dec.
        """
        return self.poco.pointing()

    def slew_to(self, ra, dec):
        """
        Command the telescope to slew to a new position, after checking that it
        is in a state that allows a new target to be commanded.

        Parameters
        ----------
        ra
            Right ascension of the target.  May be a bare number (assumed to be
            hours) or a sexagesimal string, or any
            :class:`astropy.units.Quantity`/:class:`astropy.coordinates.Angle`
            (in which case its own unit is used instead).
        dec
            Declination of the target, interpreted the same way as ``ra`` except
            a bare number is assumed to be degrees.

        Raises
        ------
        ValueError
            Raised if telescope movement is disabled, tracking is disabled, the
            telescope is not ready to move to a new target, or the telescope
            fails to reach the target within 5 minutes.
        """
        if not self.poco.movement_allowed(timeout=0.5):
            raise ValueError('Telescope movement is disabled!')

        if not self.poco.tracking(timeout=0.5):
            raise ValueError('Tracking is disabled!')

        if not self.poco.on_target(timeout=0.5):
            raise ValueError('Telescope is not ready to move to a new target!')

        # TODO: Need to add checks that the request is within the pointing
        # limits.
        log.info(f'Moving to RA={ra}, Dec={dec}')
        target_coo = SkyCoord(ra=ra, dec=dec, unit=('hourangle', 'deg'))
        # NOTE: Type is double, should be radians but we don't know what happens
        # in the ktl python package
        #   - try using modify with sexigesimal coordinates

        self.poco.set_pointing_demand(
            target_coo.ra.to('hourangle').value, target_coo.dec.to('deg').value
        )

        # TODO: Need a better way to determine if the move fails!  Timeout
        # currently set at 5 min.
        if not self.poco.on_target(timeout=300):
            # TODO: Is there a way to appropriately handle this?
            raise ValueError('Telescope failed to make it to target within 5 min.')


def find_nearest_target(telescope_coo, obj_search_str=None, file=None):
    """
    Find the starlist target nearest the telescope's current pointing.

    Parameters
    ----------
    telescope_coo
        The position to measure separations from, typically
        :attr:`~nickel_focus.slew.NickelTelescopePointing.current`.
    obj_search_str
        If given, only targets whose name contains this substring are
        considered; if ``None``, every target in the file is considered.
    file
        Path to a starlist file to search (see
        :func:`~nickel_focus.starlist.parse_starlist` for the supported format).
        If ``None``, the packaged default catalog of pointing/focus stars
        (``nickel_focus/data/point_focus.txt``) is used.

    Returns
    -------
    name : str
        The nearest target's name.
    ra : astropy.coordinates.Angle
        The nearest target's right ascension.
    dec : astropy.coordinates.Angle
        The nearest target's declination.

    Raises
    ------
    ValueError
        Raised if ``obj_search_str`` is given but no target name in ``file``
        contains it.
    """
    _file = (
        Path(__file__).resolve().parent / 'data' / 'point_focus.txt'
        if file is None else Path(file).absolute()
    )
    tbl = starlist.parse_starlist(_file)

    if obj_search_str is not None:
        selected = [obj_search_str in obj_name for obj_name in tbl['name']]
        if np.sum(selected) == 0:
            raise ValueError(f'{_file} has no object names containing {obj_search_str}')
        tbl = tbl[selected]

    obj_coo = SkyCoord(ra=tbl['ra'], dec=tbl['dec'], unit=('hourangle', 'deg'))
    offsets = telescope_coo.separation(obj_coo)
    nearest_obj = tbl[np.argmin(offsets)]
    return (
        nearest_obj['name'],
        Angle(nearest_obj['ra'], unit='deg'),
        Angle(nearest_obj['dec'], unit='deg'),
    )