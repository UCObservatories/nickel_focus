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

    ``EXPSTATE`` and ``EXPOSE`` are monitored from construction on.  The
    camera's settings and file paths are available through :attr:`cfg` and
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
    monitored = ('EXPSTATE', 'EXPOSE')

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
        (case-insensitive), and return whether it did.
        """
        return self.wait_for('EXPSTATE', f'== {state}', timeout)

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
        """The path of the most recent exposure, from ``EXPRESULT``."""
        return Path(self.cam.read('EXPRESULT'))

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
    # values accepted by the camera.  Names not in this map are passed through
    # unchanged and validated against AMPCONFLIST.
    speed_aliases = {
        'Fast': '1.0MHz',
        'Slow': '0.05MHz',
        }

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

        The read speed is reported by its user-facing name (a key of
        :attr:`speed_aliases`, e.g. ``'Fast'``) when the camera's ``AMPCONF``
        value has one, so the result uses the same names :meth:`configure`
        accepts; otherwise the raw ``AMPCONF`` value is returned.

        Returns
        -------
        :obj:`dict`
            Keys ``exptime`` (:obj:`float`, seconds), ``speed``, and
            ``binning`` (:obj:`str`, e.g. ``'2,2'``).
        """
        speed = self.cam.read('AMPCONF').strip()
        speed_names = {value: name for name, value in self.speed_aliases.items()}
        return {
            'exptime': float(self.cam.read('EXPOSURE')),
            'speed': speed_names.get(speed, speed),
            'binning': self.cam.read('BINNING').strip(),
        }

    def __repr__(self):
        return (
            'Exposure settings:\n'
            f"    Record: {self.cam.read('RECORD')}\n"
            f'    Time: {self.exptime}\n'
            f"    Speed: {self.cam.read('AMPCONF')}\n"
            f"    Binning: {self.cam.read('BINNING')}\n"
        )
