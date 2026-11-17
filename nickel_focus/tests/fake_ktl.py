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
import inspect
import operator
import shlex
import threading
import weakref

from astropy.coordinates import Angle
from astropy.io import fits


class TimeoutException(Exception):
    """Stand-in for ``ktl.TimeoutException``."""


class ktlError(Exception):  # noqa: N801 (named like ktl's own class)
    """Stand-in for ``ktl.ktlError``."""


_OPERATORS = {'==': operator.eq, '!=': operator.ne}


def _evaluate(value, expression):
    """
    Evaluate a ``waitFor`` expression against ``value``, for the forms
    :mod:`nickel_focus` uses: ``'== allowed'``, a quoted operand
    (``'!= "/data/n1.fits"'``), and alternatives on the same keyword joined
    by ``or`` (``'== Ready or $nscicam.EXPSTATE == Error'``).  Like :mod:`ktl`,
    the expression is split with :mod:`shlex`, and values are compared
    numerically if both sides are numbers, otherwise as case-insensitive
    strings.
    """
    tokens = shlex.split(expression)
    alternatives, current = [], []
    for token in tokens:
        if token == 'or':
            alternatives.append(current)
            current = []
        elif not token.startswith('$'):  # the keyword itself, in later alternatives
            current.append(token)
    alternatives.append(current)
    return any(_compare(value, op, operand) for op, operand in alternatives)


def _compare(value, op, operand):
    compare = _OPERATORS[op]
    try:
        return compare(float(value), float(operand))
    except ValueError:
        return compare(str(value).strip().lower(), operand.strip().lower())


class _Update:
    """What a fake callback receives: a frozen copy whose read() returns its value."""

    def __init__(self, value):
        self._value = value

    def read(self, binary=False, both=False, wait=True, timeout=None):
        return self._value if binary else str(self._value)


class FakeKeyword:
    """
    One fake keyword.

    Attributes
    ----------
    value
        The current value.  ``read()`` returns it as a string (the ascii
        value) unless ``binary=True``.  Setting it, while the keyword is
        monitored, "broadcasts" it: every callback is called.
    pending : :obj:`list`
        Values the keyword will take later, in order: each ``waitFor`` steps
        through them (as if time were passing) until its expression holds or
        none are left.  This is how a simulated move or exposure "takes time".
    responsive : :obj:`bool`
        If False, the service doesn't answer: ``read`` raises
        :class:`TimeoutException` and ``waitFor`` returns False.
    reads, writes : :obj:`list`
        The ``timeout`` of each read, and the value of each write.
    binary_writes : :obj:`list`
        For each write, whether it was binary (``binary=True``).
    monitored : :obj:`bool`
        Whether ``monitor()`` has been called.

    Like :mod:`ktl`'s, callbacks are held only weakly, are called (with a
    frozen copy of the keyword) after every broadcast and every completed
    read, and any exception they raise is swallowed.  Unlike :mod:`ktl`'s,
    they're called on the thread that changed or read the value, not a
    dedicated callback thread.
    """

    def __init__(self, fake_ktl, service, name, value, on_write=None):
        self._ktl = fake_ktl
        self.service = service
        self.name = name
        self._value = value
        self.pending = []
        self.responsive = True
        self.on_write = on_write
        self.reads = []
        self.writes = []
        self.binary_writes = []
        self.monitored = False
        self._callbacks = []
        self.callback_errors = []

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        with self._ktl.lock:
            self._value = value
            if self.monitored:
                self._propagate()

    def _propagate(self):
        update = _Update(self._value)
        for reference in list(self._callbacks):
            function = reference()
            if function is None:
                self._callbacks.remove(reference)
                continue
            try:
                function(update)
            except Exception as e:  # ktl logs these; it never raises them
                self.callback_errors.append(e)

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
            self._propagate()
            return self._value if binary else str(self._value)

    def write(self, value, wait=True, binary=False, timeout=None):
        with self._ktl.lock:
            self.writes.append(value)
            self.binary_writes.append(binary)
            self._ktl.writes.append((self.service, self.name, value))
            self.pending = []
            self.value = value
            if self.on_write is not None:
                self.on_write(value)

    def monitor(self, start=True, prime=True, wait=True):
        with self._ktl.lock:
            self.monitored = start

    def callback(self, function, remove=False, preferred=False):
        with self._ktl.lock:
            if remove:
                self._callbacks = [r for r in self._callbacks if r() is not function]
                return
            make_ref = weakref.WeakMethod if inspect.ismethod(function) else weakref.ref
            self._callbacks.append(make_ref(function))

    def waitFor(self, expression, timeout=None, case=False):  # noqa: N802 (ktl's name)
        with self._ktl.lock:
            self.monitored = True
            if not self.responsive:
                return False
            while not _evaluate(self._value, expression):
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
        Each commanded slew, as ``(ra, dec)`` in hours and degrees, converted
        from the binary radians written to ``POCORAD``/``POCODECD``.  A
        target written any other way (e.g. as ascii) is an error.
    reaches_target : :obj:`bool`
        Whether a commanded slew arrives (``POCOT`` goes to ``0``) or fails
        (``-1``).
    exposure_error : :obj:`str` or None
        If set, exposures fail: ``EXPSTATE`` ends at ``Error``, ``EXPRESULT``
        holds the file name followed by this message, and ``FITSFILE`` doesn't
        change.
    reports_file : :obj:`bool`
        Whether a successful exposure updates ``FITSFILE``.
    """

    def __init__(self, make_frame=None, directory=None, focus=340.):
        self.make_frame = make_frame
        self.directory = directory
        self.exposure_count = 0
        self.slew_calls = []
        self.reaches_target = True
        self.exposure_error = None
        self.reports_file = True
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
        define(cam, 'FITSFILE', '')
        define(cam, 'EXPOSURE', 5.0)
        define(cam, 'AMPCONF', '0.05MHz')
        define(cam, 'AMPCONFLIST', '5.0MHz,3.0MHz,1.0MHz,0.05MHz')  # as at the telescope
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
        rad = self.keyword('nickelpoco', 'POCORAD')
        decd = self.keyword('nickelpoco', 'POCODECD')
        if not (rad.binary_writes[-1:] == [True] and decd.binary_writes[-1:] == [True]):
            raise AssertionError('The slew target must be written as binary radians.')
        ra = Angle(float(rad.value), unit='rad').hour
        dec = Angle(float(dec), unit='rad').deg
        self.slew_calls.append((ra, dec))
        if self.reaches_target:
            self.ra, self.dec = ra, dec
            self._set('nickelpoco', 'POCOT', 1, pending=[0])
        else:
            self._set('nickelpoco', 'POCOT', 1, pending=[-1])

    def _on_expose(self, value):
        """
        ``EXPOSE = Go``: write a frame for the current focus, step ``EXPSTATE``
        through the exposure, and report the finished file in ``FITSFILE``
        (as a pending value, so it appears only once waited for).
        """
        if value != 'Go':
            return
        if self.make_frame is None or self.directory is None:
            raise AssertionError('This Simulator was not set up to take exposures.')
        self.exposure_count += 1
        path = self.directory / f'fake{self.exposure_count:04d}.fits'
        if self.exposure_error is not None:
            self._set('nscicam', 'EXPRESULT', f'{path} {self.exposure_error}')
            self._set('nscicam', 'EXPSTATE', 'Start', pending=['Exposing', 'Error'])
            return
        fits.writeto(path, self.make_frame(self.focus), overwrite=True)
        self._set('nscicam', 'EXPRESULT', str(path))
        self._set('nscicam', 'EXPSTATE', 'Start', pending=['Exposing', 'Readout', 'Ready'])
        if self.reports_file:
            fitsfile = self.keyword('nscicam', 'FITSFILE')
            fitsfile.pending = [str(path)]
        obsnum = self.keyword('nscicam', 'OBSNUM')
        obsnum.value = int(obsnum.value) + 1
