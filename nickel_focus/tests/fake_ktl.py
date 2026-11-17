"""
A keyword-level stand-in for the :mod:`ktl` package, with a simulated
telescope and camera behind it.

Tests install a :class:`FakeKTL` in place of :mod:`ktl` in
:mod:`nickel_focus.services.base` (see the ``fake_hardware``/``fake_telescope``
fixtures in ``conftest.py``), so the real service classes
(:class:`~nickel_focus.services.nickelpoco.NickelPoco`,
:class:`~nickel_focus.services.nscicam.NSciCam`) and the device logic built on
them (:class:`~nickel_focus.focus.Focus`,
:class:`~nickel_focus.slew.NickelTelescopePointing`,
:class:`~nickel_focus.exposure.Exposure`) run unchanged.

:class:`FakeKeyword` implements just the named methods :mod:`nickel_focus` uses
(``read``, ``write``, ``monitor``, ``waitFor``), as does :class:`FakeService`
(``heartbeat``), and they record how they were called so tests can check,
e.g., that every read had a timeout or that a read-only service never
wrote.  :class:`Simulator` defines the keywords of the
``nickelpoco`` and ``nscicam`` services and makes writes behave like the
hardware: commanding the focus moves the secondary and re-locks it, commanding
a slew moves the telescope, and starting an exposure steps ``EXPSTATE`` through
an exposure and writes a synthesized frame.

This validates the *software logic* built on the keyword protocol -- it cannot
validate the real services' behavior, which can only be checked at the
telescope (see the ``ktl_live`` tests).
"""
import operator
import threading

from astropy.coordinates import Angle
from astropy.io import fits


class TimeoutException(Exception):
    """Stand-in for ``ktl.TimeoutException``."""


class ktlError(Exception):  # noqa: N801 (named like ktl's own class)
    """Stand-in for ``ktl.ktlError``."""


_OPERATORS = {'==': operator.eq, '!=': operator.ne}


def _evaluate(value, expression):
    """
    Evaluate a ``waitFor`` expression such as ``'== allowed'`` against
    ``value``: numerically if both sides are numbers, otherwise as
    case-insensitive strings, as :mod:`ktl` does.
    """
    op, _, operand = expression.strip().partition(' ')
    compare = _OPERATORS[op]
    operand = operand.strip()
    try:
        return compare(float(value), float(operand))
    except ValueError:
        return compare(str(value).strip().lower(), operand.lower())


class FakeKeyword:
    """
    One fake keyword.

    Attributes
    ----------
    value
        The current value.  ``read()`` returns it as a string (the ascii
        value) unless ``binary=True``.
    pending : :obj:`list`
        Values the keyword will take later, in order: each ``waitFor`` steps
        through them (as if time were passing) until its expression holds or
        none are left.  This is how a simulated move or exposure "takes time".
    responsive : :obj:`bool`
        If False, the service doesn't answer: ``read`` raises
        :class:`TimeoutException` and ``waitFor`` returns False.
    reads, writes : :obj:`list`
        The ``timeout`` of each read, and the value of each write.
    monitored : :obj:`bool`
        Whether ``monitor()`` has been called.
    """

    def __init__(self, fake_ktl, service, name, value, on_write=None):
        self._ktl = fake_ktl
        self.service = service
        self.name = name
        self.value = value
        self.pending = []
        self.responsive = True
        self.on_write = on_write
        self.reads = []
        self.writes = []
        self.monitored = False

    def read(self, binary=False, both=False, wait=True, timeout=None):
        with self._ktl.lock:
            self.reads.append(timeout)
            if not self.responsive:
                if timeout is None:
                    raise AssertionError(
                        f'{self.service}.{self.name}: a read without a timeout would block '
                        'forever on an unresponsive service'
                    )
                raise TimeoutException(f'{self.service}.{self.name}: no reply within {timeout} s')
            return self.value if binary else str(self.value)

    def write(self, value, wait=True, binary=False, timeout=None):
        with self._ktl.lock:
            self.writes.append(value)
            self._ktl.writes.append((self.service, self.name, value))
            self.value = value
            self.pending = []
            if self.on_write is not None:
                self.on_write(value)

    def monitor(self, start=True, prime=True, wait=True):
        with self._ktl.lock:
            self.monitored = start

    def waitFor(self, expression, timeout=None, case=False):  # noqa: N802 (ktl's name)
        with self._ktl.lock:
            self.monitored = True
            if not self.responsive:
                return False
            while not _evaluate(self.value, expression):
                if not self.pending:
                    return False
                self.value = self.pending.pop(0)
            return True


class FakeService:
    """
    Stand-in for a ``ktl.Service``: just ``heartbeat``.

    Attributes
    ----------
    heartbeats : :obj:`dict`
        The period registered for each heartbeat keyword.
    """

    def __init__(self, fake_ktl, name):
        self._ktl = fake_ktl
        self.name = name
        self.heartbeats = {}

    def heartbeat(self, keyword, period=5):
        self._ktl.cache(self.name, keyword)  # KeyError if there's no such keyword
        self.heartbeats[keyword] = period


class FakeKTL:
    """
    Stand-in for the :mod:`ktl` module: :meth:`cache` and the two exception
    classes, over a set of :class:`FakeKeyword` objects.

    Attributes
    ----------
    writes : :obj:`list`
        Every write, as ``(service, keyword, value)``, in order.
    """
    TimeoutException = TimeoutException
    ktlError = ktlError

    def __init__(self):
        self.lock = threading.RLock()
        self.keywords = {}
        self.services = {}
        self.writes = []

    def define(self, service, name, value, on_write=None):
        """Add a keyword with an initial value and an optional write hook."""
        self.keywords[service, name] = FakeKeyword(self, service, name, value, on_write)

    def cache(self, service, name=None):
        """
        Like ``ktl.cache``: the (single, shared) :class:`FakeService` if no
        keyword is named, otherwise the keyword object, or KeyError.
        """
        if name is None:
            return self.services.setdefault(service, FakeService(self, service))
        try:
            return self.keywords[service, name]
        except KeyError:
            raise KeyError(f'{service} has no keyword {name}') from None

    def keyword(self, service, name):
        """The keyword object, for a test to inspect or change."""
        return self.keywords[service, name]


def _sexagesimal(angle, unit, precision):
    return Angle(angle, unit=unit).to_string(unit=unit, sep=':', pad=True, precision=precision)


class Simulator:
    """
    The ``nickelpoco`` and ``nscicam`` keywords on a :class:`FakeKTL`, with
    writes that act like the hardware.

    Parameters
    ----------
    make_frame : callable, optional
        ``make_frame(focus_value) -> numpy.ndarray``: the image an exposure
        produces at the current focus.  Required only to take exposures.
    directory : :class:`pathlib.Path`, optional
        Where exposures are written.  Required only to take exposures.
    focus : :obj:`float`, optional
        The starting focus (secondary position).

    Attributes
    ----------
    ktl : :class:`FakeKTL`
        The fake module, to install in place of :mod:`ktl`.
    slew_calls : :obj:`list`
        The ``(POCORAD, POCODECD)`` values of each commanded slew.
    reaches_target : :obj:`bool`
        Whether a commanded slew arrives (``POCOT`` goes to ``0``) or fails
        (``-1``).
    """

    def __init__(self, make_frame=None, directory=None, focus=340.):
        self.make_frame = make_frame
        self.directory = directory
        self.exposure_count = 0
        self.slew_calls = []
        self.reaches_target = True
        self.ktl = FakeKTL()

        poco = 'nickelpoco'
        define = self.ktl.define
        define(poco, 'POCSTOP', 'allowed')
        define(poco, 'POCTRCK', 'on')
        define(poco, 'POCOT', 0)
        define(poco, 'POCORAA', '00:00:00.00')
        define(poco, 'POCODECA', '+00:00:00.0')
        define(poco, 'POCORAD', '00:00:00.00')
        define(poco, 'POCODECD', '+00:00:00.0', on_write=self._on_slew)
        define(poco, 'POCSECPA', focus)
        define(poco, 'POCSECPD', focus, on_write=self._on_focus)
        define(poco, 'POCSECLK', 'on')
        define(poco, 'POCHEART', 0)

        cam = 'nscicam'
        define(cam, 'EXPSTATE', 'Ready')
        define(cam, 'EXPOSE', 'None', on_write=self._on_expose)
        define(cam, 'EXPRESULT', '')
        define(cam, 'EXPOSURE', 5.0)
        define(cam, 'AMPCONF', '0.05MHz')
        define(cam, 'AMPCONFLIST', '1.0MHz,0.05MHz')
        define(cam, 'BINNING', '2,2')
        define(cam, 'RECORD', 'Yes')
        define(cam, 'OBSNUM', 1)
        define(cam, 'RECORDDIR', str(directory or '.'))
        define(cam, 'SCRATCHDIR', str(directory or '.'))
        define(cam, 'FITSPREFIX', 'fake')
        define(cam, 'FITSSUFFIX', '.fits')
        define(cam, 'DISP0CLK', 1000)

    def keyword(self, service, name):
        """The :class:`FakeKeyword` for ``service.name``."""
        return self.ktl.keyword(service, name)

    def _set(self, service, name, value, pending=()):
        keyword = self.keyword(service, name)
        keyword.value = value
        keyword.pending = list(pending)

    # -- telescope state, as plain values -----------------------------------------

    @property
    def focus(self):
        """The actual secondary position (``POCSECPA``)."""
        return float(self.keyword('nickelpoco', 'POCSECPA').value)

    @property
    def ra(self):
        """The telescope's actual RA in hours (``POCORAA``)."""
        return Angle(self.keyword('nickelpoco', 'POCORAA').value, unit='hourangle').hour

    @ra.setter
    def ra(self, hours):
        self._set('nickelpoco', 'POCORAA', _sexagesimal(hours, 'hourangle', 2))

    @property
    def dec(self):
        """The telescope's actual Dec in degrees (``POCODECA``)."""
        return Angle(self.keyword('nickelpoco', 'POCODECA').value, unit='deg').deg

    @dec.setter
    def dec(self, degrees):
        self._set('nickelpoco', 'POCODECA', _sexagesimal(degrees, 'deg', 1))

    @property
    def movement_allowed(self):
        return self.keyword('nickelpoco', 'POCSTOP').value == 'allowed'

    @movement_allowed.setter
    def movement_allowed(self, allowed):
        self._set('nickelpoco', 'POCSTOP', 'allowed' if allowed else 'disabled')

    @property
    def tracking_on(self):
        return self.keyword('nickelpoco', 'POCTRCK').value == 'on'

    @tracking_on.setter
    def tracking_on(self, on):
        self._set('nickelpoco', 'POCTRCK', 'on' if on else 'off')

    @property
    def ready(self):
        """Whether the telescope is on target, ready for a new one (``POCOT == 0``)."""
        return self.keyword('nickelpoco', 'POCOT').value == 0

    @ready.setter
    def ready(self, ready):
        self._set('nickelpoco', 'POCOT', 0 if ready else 1)

    # -- write hooks ------------------------------------------------------------

    def _on_focus(self, value):
        """``POCSECPD`` written: the secondary moves there, then locks."""
        self._set('nickelpoco', 'POCSECPA', float(value))
        self._set('nickelpoco', 'POCSECLK', 'off', pending=['on'])

    def _on_slew(self, dec):
        """``POCODECD`` written (after ``POCORAD``): the telescope slews."""
        ra = float(self.keyword('nickelpoco', 'POCORAD').value)
        dec = float(dec)
        self.slew_calls.append((ra, dec))
        if self.reaches_target:
            self.ra, self.dec = ra, dec
            self._set('nickelpoco', 'POCOT', 1, pending=[0])
        else:
            self._set('nickelpoco', 'POCOT', 1, pending=[-1])

    def _on_expose(self, value):
        """``EXPOSE = Go``: write a frame for the current focus and step ``EXPSTATE``."""
        if value != 'Go':
            return
        if self.make_frame is None or self.directory is None:
            raise AssertionError('This Simulator was not set up to take exposures.')
        self.exposure_count += 1
        path = self.directory / f'fake{self.exposure_count:04d}.fits'
        fits.writeto(path, self.make_frame(self.focus), overwrite=True)
        self._set('nscicam', 'EXPRESULT', str(path))
        self._set('nscicam', 'EXPSTATE', 'Start', pending=['Exposing', 'Readout', 'Ready'])
        obsnum = self.keyword('nscicam', 'OBSNUM')
        obsnum.value = int(obsnum.value) + 1
