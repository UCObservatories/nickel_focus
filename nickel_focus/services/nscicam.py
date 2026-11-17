"""
The Nickel science camera service, ``nscicam``: exposure state and control,
exposure settings (:class:`ExposureConfig`), and where exposures are written
(:class:`ExposurePath`).
"""
from pathlib import Path

from nickel_focus.services.base import KTLService


class NSciCam(KTLService):
    """
    The ``nscicam`` KTL service.

    ``EXPSTATE``, ``FITSFILE`` (the latest complete exposure), and the
    exposure settings (``EXPOSURE``, ``AMPCONF``, ``BINNING``) are monitored
    from construction on, and ``DISP0CLK`` (the dispatcher's runtime clock) is
    registered as the heartbeat.  The camera's
    settings and file paths are available through :attr:`cfg` and
    :attr:`path`.

    See :class:`~nickel_focus.services.base.KTLService` for the parameters
    and exceptions.

    Attributes
    ----------
    cfg : :class:`ExposureConfig`
        The exposure settings.
    path : :class:`ExposurePath`
        Where exposures are written.
    """

    service = 'nscicam'
    monitored = ('EXPSTATE', 'FITSFILE', 'EXPOSURE', 'AMPCONF', 'BINNING')
    heartbeat = 'DISP0CLK'
    # Provisional: generous until DISP0CLK's broadcast interval is measured
    # at the telescope.
    heartbeat_period = 30

    def __init__(self, read_only=False):
        super().__init__(read_only=read_only)
        self.path = ExposurePath(self)
        self.cfg = ExposureConfig(self)

    def exposure_state(self):
        """
        The camera's exposure state, ``EXPSTATE`` (e.g. ``'Ready'``,
        ``'Exposing'``).
        """
        return self.read('EXPSTATE')

    def exposure_state_is(self, state, timeout):
        """
        Wait up to ``timeout`` seconds for ``EXPSTATE`` to equal ``state``
        (case-insensitive), and return True if it did, False if not, or None if
        the service didn't reply (see
        :meth:`~nickel_focus.services.base.KTLService.check`).
        """
        return self.check('EXPSTATE', f'== {state}', timeout)

    def exposure_ended(self, timeout):
        """
        Wait up to ``timeout`` seconds for an exposure to end: ``EXPSTATE``
        becomes ``Ready`` (done) or ``Error``.

        Returns
        -------
        :obj:`bool` or None
            True if it ended (see :meth:`exposure_state` for how), False if not,
            or None if the service didn't reply (see
            :meth:`~nickel_focus.services.base.KTLService.check`).
        """
        return self.check('EXPSTATE', '== Ready or $nscicam.EXPSTATE == Error', timeout)

    def exposure_result(self):
        """
        The camera's report on the latest exposure, ``EXPRESULT``: its file
        name, followed by an error message if it failed.
        """
        return self.read('EXPRESULT')

    def fits_file(self):
        """
        The full path of the latest complete exposure, ``FITSFILE`` (set once
        the file, including its header, is finished).
        """
        return self.read('FITSFILE')

    def fits_file_changed(self, previous, timeout):
        """
        Wait up to ``timeout`` seconds for ``FITSFILE`` to differ from
        ``previous`` -- i.e. for a new exposure to be complete -- and return
        True if it did, False if not, or None if the service didn't reply (see
        :meth:`~nickel_focus.services.base.KTLService.check`).
        """
        # Quoted, so ktl's expression parser reads a path as one string.
        return self.check('FITSFILE', f'!= "{previous}"', timeout)

    def start_exposure(self):
        """
        Start an exposure (``EXPOSE = Go``, which starts it the way the camera's
        current trigger mode requires).
        """
        self.write('EXPOSE', 'Go')


class ExposurePath:
    """
    Where the camera writes its exposures.

    Parameters
    ----------
    cam : :class:`NSciCam`
        The camera service.
    """
    def __init__(self, cam):
        self.cam = cam

    @property
    def previous(self):
        """The path of the latest complete exposure, from ``FITSFILE``."""
        return Path(self.cam.fits_file())

    @property
    def next(self):
        """The path the next exposure will be written to (``OBSNUM``)."""
        return self.for_obsnum(self.cam.read('OBSNUM'))

    def for_obsnum(self, obsnum, assume_recorded=False):
        """
        The path of the exposure with observation number ``obsnum``: in
        ``RECORDDIR`` if the camera is recording (``RECORD`` is ``Yes``) or
        ``assume_recorded`` is True, otherwise in ``SCRATCHDIR``, named
        ``FITSPREFIX`` + ``obsnum`` + ``FITSSUFFIX``.
        """
        record = self.cam.read('RECORD') == 'Yes'
        if record or assume_recorded:
            path = Path(self.cam.read('RECORDDIR')).absolute()
        else:
            path = Path(self.cam.read('SCRATCHDIR')).absolute()
        return path / f"{self.cam.read('FITSPREFIX')}{obsnum}{self.cam.read('FITSSUFFIX')}"


class ExposureConfig:
    """
    The camera's exposure settings: recording, read speed, binning, and
    exposure time.

    The valid read speeds are read from ``AMPCONFLIST`` at construction.

    Parameters
    ----------
    cam : :class:`NSciCam`
        The camera service.
    """
    # Map user-facing speed names (e.g., from the GUI or CLI) to the AMPCONF
    # values accepted by the camera -- the same Fast/Slow mapping the
    # data-acquisition GUI offers observers.  Names not in this map are passed
    # through unchanged and validated against AMPCONFLIST (at the telescope:
    # 5.0MHz, 3.0MHz, 1.0MHz, 0.05MHz).
    speed_aliases = {
        'Fast': '1.0MHz',
        'Slow': '0.05MHz',
        }

    keywords = {'exptime': 'EXPOSURE', 'speed': 'AMPCONF', 'binning': 'BINNING'}
    """The keyword behind each setting :meth:`current_settings` reports."""

    @classmethod
    def setting_value(cls, key, value):
        """
        Convert a setting's keyword value (ascii, as read or broadcast) to the
        form :meth:`configure` and :meth:`current_settings` use.

        Parameters
        ----------
        key : :obj:`str`
            ``'exptime'``, ``'speed'``, or ``'binning'`` (see :attr:`keywords`).
        value : :obj:`str`
            The keyword's ascii value.

        Returns
        -------
        :obj:`float` or :obj:`str`
            The exposure time in seconds; the read speed by its user-facing
            name (a key of :attr:`speed_aliases`, e.g. ``'Fast'``) if it has
            one, otherwise the raw ``AMPCONF`` value; or the binning (e.g.
            ``'2,2'``).

        Raises
        ------
        ValueError
            Raised if an exposure time isn't a number.
        """
        if key == 'exptime':
            return float(value)
        value = str(value).strip()
        if key == 'speed':
            speed_names = {alias: name for name, alias in cls.speed_aliases.items()}
            return speed_names.get(value, value)
        return value

    def __init__(self, cam):
        self.cam = cam
        self.expspeed_options = cam.read('AMPCONFLIST').split(',')
        # TODO: Is there a way to programmatically get the binning options
        self.expbin_options = ['1,1', '2,2', '4,4']

    def configure(self, record=None, speed=None, binning=None, exptime=None):
        """
        Write each setting that isn't None: ``RECORD``, ``AMPCONF`` (``speed``,
        a :attr:`speed_aliases` name or an ``AMPCONFLIST`` value), ``BINNING``,
        and ``EXPOSURE`` (``exptime``, seconds).

        Raises
        ------
        ValueError
            Raised if ``speed`` or ``binning`` isn't a valid option.
        """
        if record is not None:
            self.cam.write('RECORD', record)
        if speed is not None:
            speed = self.speed_aliases.get(speed, speed)
            if speed not in self.expspeed_options:
                raise ValueError(
                    f'{speed} is not a valid read speed.  Must be one of {self.expspeed_options}.'
                )
            self.cam.write('AMPCONF', speed)
        if binning is not None:
            if binning not in self.expbin_options:
                raise ValueError(
                    f'{binning} is not a valid binning.  Must be one of {self.expbin_options}.'
                )
            self.cam.write('BINNING', binning)
        if exptime is not None:
            self.cam.write('EXPOSURE', exptime)

    @property
    def exptime(self):
        """The exposure time, ``EXPOSURE``, as the camera reports it (a string)."""
        return self.cam.read('EXPOSURE')

    def current_settings(self):
        """
        Read the camera's current exposure time, read speed, and binning.

        Each is converted by :meth:`setting_value`, so the result uses the same
        names :meth:`configure` accepts.

        Returns
        -------
        :obj:`dict`
            Keys ``exptime`` (:obj:`float`, seconds), ``speed``, and
            ``binning`` (:obj:`str`, e.g. ``'2,2'``).
        """
        return {
            key: self.setting_value(key, self.cam.read(keyword))
            for key, keyword in self.keywords.items()
        }

    def __repr__(self):
        return (
            'Exposure settings:\n'
            f"    Record: {self.cam.read('RECORD')}\n"
            f'    Time: {self.exptime}\n'
            f"    Speed: {self.cam.read('AMPCONF')}\n"
            f"    Binning: {self.cam.read('BINNING')}\n"
        )
