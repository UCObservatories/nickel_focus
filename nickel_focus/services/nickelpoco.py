"""
The Nickel telescope's pointing and control service, ``nickelpoco``: pointing,
tracking, and the secondary mirror (focus).
"""
from astropy.coordinates import SkyCoord

from nickel_focus.services.base import KTLService


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
    - ``POCORAD``/``POCODECD``: the desired RA and Dec.
    - ``POCSECPA``/``POCSECPD``: the actual and desired secondary-mirror
      (focus) position.
    - ``POCSECLK``: the secondary-mirror lock (``on``/``off``).
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
        Set the desired RA (``POCORAD``) and then Dec (``POCODECD``).

        Parameters
        ----------
        ra, dec
            The values to write, as given (e.g. decimal hours and degrees,
            written as ascii).
        """
        self.write('POCORAD', ra)
        self.write('POCODECD', dec)
