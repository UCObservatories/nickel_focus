"""
The Nickel telescope's pointing and control service, ``nickelpoco``: pointing,
tracking, and the secondary mirror (focus).
"""
from astropy.coordinates import Angle, SkyCoord
import numpy as np

from nickel_focus.services.base import KTLService, require

# How guider offsets behave (see NickelPoco.apply_guider_offset).  None of
# these has been verified at the telescope yet.

GUIDER_OFFSET_IN_SERIES = False
"""
Whether ``POCGDIX`` and ``POCGDIY`` must each be armed, written, and waited
for separately (X first), instead of both written and then waited for once.
Unverified.
"""

GUIDER_OFFSET_SIGN = (1, 1)
"""
The sign of ``POCGDIX`` and ``POCGDIY`` for an offset that points the
telescope further East and North, respectively.  Unverified.
"""

GUIDER_OFFSET_COS_DEC = False
"""
Whether ``POCGDIX`` is a change in true RA (so an East offset on the sky is
divided by cos(Dec) before it's written) rather than in plate units.
Unverified.
"""

GUIDER_MOVE_TIMEOUT = 60.
"""Seconds to wait for a guider move to finish (``POCGDFIN``).  Unverified."""

ON_TARGET_TIMEOUT = 60.
"""
Seconds to wait for the telescope to report being on target (``POCOT``)
after ``POCGDOT`` is written.  Unverified.
"""


class NickelPoco(KTLService):
    """
    The ``nickelpoco`` KTL service.

    Keywords used:

    - ``POCSTOP``: whether mechanisms may move (``allowed``/``disabled``).
      **Only ever read.**  Writing any non-zero value stops all motion, and
      a negative value turns off motor power, so nothing in
      :mod:`nickel_focus` writes it.
    - ``POCTRCK``: tracking (``on``/``off``).
    - ``POCOT``: on target (``0``), not there yet (``1``), or failed to get
      there (``-1``).
    - ``POCORAA``/``POCODECA``: the actual RA (hours) and Dec (degrees),
      sexagesimal in ascii.
    - ``POCORAD``/``POCODECD``: the desired RA and Dec.  Always written as
      binary values, in radians -- the keywords' native unit -- so that how
      the service would parse an ascii value never matters.
    - ``POCSECPA``/``POCSECPD``: the actual and desired secondary-mirror
      (focus) position.
    - ``POCSECLK``: the secondary-mirror lock (``on``/``off``).
    - ``POCGDFIN``: whether a guider move has finished.  Writing any value
      sets it to ``1``; it becomes ``0`` when the move completes, or ``-1``
      if it fails.
    - ``POCGDIX``/``POCGDIY``: increase the guider offset in X (RA) and Y
      (Dec) by the value written.  Always written as binary radians.
    - ``POCGDOT``: tells poco that the current position matches the
      desired position, setting the guide offsets accordingly (marking the
      telescope on target).
    - ``POCHEART``: the service's heartbeat.

    The status keywords and the actual position and focus are monitored from
    construction on, and ``POCHEART`` is registered as the heartbeat.

    See :class:`~nickel_focus.services.base.KTLService` for the parameters
    and exceptions.
    """

    service = 'nickelpoco'
    monitored = (
        'POCSTOP', 'POCTRCK', 'POCOT', 'POCSECLK', 'POCSECPA', 'POCORAA', 'POCODECA',
    )
    heartbeat = 'POCHEART'
    # Provisional: generous until POCHEART's broadcast interval is measured
    # at the telescope.
    heartbeat_period = 30

    # -- conditions ------------------------------------------------------------
    #
    # Each waits up to `timeout` seconds for the condition and returns True,
    # False, or None if the service didn't reply; see KTLService.check.

    def movement_allowed(self, timeout):
        """Whether telescope movement is enabled (``POCSTOP == allowed``)."""
        return self.check('POCSTOP', '== allowed', timeout)

    def tracking(self, timeout):
        """Whether the telescope is tracking (``POCTRCK == on``)."""
        return self.check('POCTRCK', '== on', timeout)

    def on_target(self, timeout):
        """Whether the telescope is on target and stable (``POCOT == 0``)."""
        return self.check('POCOT', '== 0', timeout)

    def secondary_locked(self, timeout):
        """Whether the secondary mirror is locked (``POCSECLK == on``)."""
        return self.check('POCSECLK', '== on', timeout)

    # -- values ----------------------------------------------------------------

    def pointing(self):
        """
        The telescope's current pointing, from ``POCORAA``/``POCODECA``.

        Returns
        -------
        :class:`~astropy.coordinates.SkyCoord`
            The current RA/Dec.
        """
        return SkyCoord(
            ra=self.read('POCORAA'), dec=self.read('POCODECA'), unit=('hourangle', 'deg')
        )

    def secondary_position(self):
        """The actual secondary-mirror (focus) position, ``POCSECPA``, as a float."""
        return float(self.read('POCSECPA'))

    def secondary_demand(self):
        """The desired secondary-mirror (focus) position, ``POCSECPD``, as a float."""
        return float(self.read('POCSECPD'))

    # -- commands --------------------------------------------------------------

    def set_secondary_lock(self, locked):
        """Lock (``POCSECLK = on``) or unlock (``off``) the secondary mirror."""
        self.write('POCSECLK', 'on' if locked else 'off')

    def set_secondary_demand(self, position):
        """Set the desired secondary-mirror (focus) position, ``POCSECPD``."""
        self.write('POCSECPD', position)

    def set_pointing_demand(self, ra, dec):
        """
        Set the desired RA (``POCORAD``) and then Dec (``POCODECD``), written as
        binary values in radians.

        Parameters
        ----------
        ra, dec : :class:`~astropy.coordinates.Angle` or :class:`~astropy.units.Quantity`
            The target's right ascension and declination, in any angular unit
            (e.g. ``SkyCoord.ra``/``.dec``).
        """
        self.write('POCORAD', Angle(ra).radian, binary=True)
        self.write('POCODECD', Angle(dec).radian, binary=True)

    def guider_offset_radians(self, east, north):
        """
        The ``POCGDIX``/``POCGDIY`` values, in radians, for an on-sky offset
        (see :data:`GUIDER_OFFSET_SIGN` and :data:`GUIDER_OFFSET_COS_DEC`; the
        latter needs the current Dec, from :meth:`pointing`).

        Parameters
        ----------
        east, north : :obj:`float`
            The offset, in arcseconds on the sky: positive points the
            telescope further East and North.

        Returns
        -------
        :obj:`tuple`
            ``(x, y)``, in radians.
        """
        x_sign, y_sign = GUIDER_OFFSET_SIGN
        x = x_sign * Angle(east, unit='arcsec').radian
        y = y_sign * Angle(north, unit='arcsec').radian
        if GUIDER_OFFSET_COS_DEC and x != 0:
            x /= np.cos(self.pointing().dec.radian)
        return x, y

    def apply_guider_offset(self, east, north, timeout=None):
        """
        Offset the telescope through the guider offsets, and wait for the
        move to finish: arm ``POCGDFIN`` (write it, then wait for it to read
        ``1``), write ``POCGDIX`` and ``POCGDIY``, then wait for ``POCGDFIN``
        to read ``0`` (done) or ``-1`` (failed).  With
        :data:`GUIDER_OFFSET_IN_SERIES`, each axis is armed, written, and
        waited for in turn.  An axis with no offset isn't written; with no
        offset at all, nothing is.

        This doesn't check that the telescope may move, or mark it on target
        afterward (see :meth:`mark_guider_on_target`).

        Parameters
        ----------
        east, north : :obj:`float`
            The offset, in arcseconds on the sky (see
            :meth:`guider_offset_radians`).
        timeout : :obj:`float`, optional
            Seconds to wait for each step; :data:`GUIDER_MOVE_TIMEOUT` if
            None.

        Raises
        ------
        ValueError
            Raised if ``POCGDFIN`` doesn't arm, the move doesn't finish within
            ``timeout``, or the move fails.
        ~nickel_focus.services.base.KTLTimeoutError
            Raised if the service doesn't reply.
        """
        timeout = GUIDER_MOVE_TIMEOUT if timeout is None else timeout
        x, y = self.guider_offset_radians(east, north)
        axes = [(name, value) for name, value in (('POCGDIX', x), ('POCGDIY', y)) if value != 0]
        if not axes:
            return
        groups = [[axis] for axis in axes] if GUIDER_OFFSET_IN_SERIES else [axes]
        for group in groups:
            self._arm_guider_move(timeout)
            for name, value in group:
                self.write(name, value, binary=True)
            self._wait_for_guider_move(timeout)

    def _arm_guider_move(self, timeout):
        """Write ``POCGDFIN`` and wait for it to read ``1``."""
        self.write('POCGDFIN', 1)
        require(
            self.check('POCGDFIN', '== 1', timeout),
            f'nickelpoco.POCGDFIN did not change to 1 within {timeout:.0f} s of being written.',
            'No reply from nickelpoco.POCGDFIN; cannot start the guider offset.'
        )

    def _wait_for_guider_move(self, timeout):
        """Wait for ``POCGDFIN`` to read ``0`` or ``-1``; raise for ``-1``."""
        require(
            self.check('POCGDFIN', '!= 1', timeout),
            f'The guider offset did not finish within {timeout:.0f} s.',
            'No reply from nickelpoco.POCGDFIN; cannot tell whether the guider offset finished.'
        )
        result = self.read('POCGDFIN')
        if float(result) != 0:
            raise ValueError(f'The guider offset failed (nickelpoco.POCGDFIN = {result}).')

    def mark_guider_on_target(self, timeout=None):
        """
        Write ``POCGDOT``, marking the telescope's current position as on
        target, then wait for it to report being on target (``POCOT == 0``).

        Parameters
        ----------
        timeout : :obj:`float`, optional
            Seconds to wait for ``POCOT``; :data:`ON_TARGET_TIMEOUT` if None.

        Returns
        -------
        :obj:`bool` or None
            Whether the telescope reported being on target within
            ``timeout``, or None if the service didn't reply (see
            :meth:`~nickel_focus.services.base.KTLService.check`).
        """
        timeout = ON_TARGET_TIMEOUT if timeout is None else timeout
        self.write('POCGDOT', 1)
        return self.on_target(timeout)
