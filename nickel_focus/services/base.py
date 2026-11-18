"""
The base class for KTL services, and the only module in :mod:`nickel_focus`
that imports :mod:`ktl`.

Everything else talks to the telescope and camera through
:class:`KTLService` subclasses
(:class:`~nickel_focus.services.nickelpoco.NickelPoco`,
:class:`~nickel_focus.services.nscicam.NSciCam`), which take and return
plain Python values and raise only the exceptions defined here.
:mod:`ktl`'s own keyword objects and exceptions never leave this module.

:mod:`ktl` being unavailable is not an error at import: the archive/replay
paths of the CLI scripts and the GUI must keep working with no :mod:`ktl` at
all.  If it can't be imported (not installed, or installed but broken),
a warning is issued once, :func:`ktl_available` returns False, and
constructing a service raises :class:`KTLUnavailableError`.

KTL's own log -- which by default goes to ``stderr`` and includes only errors
-- is passed on to the :mod:`nickel_focus` log once the first service object
is constructed (see :func:`route_ktl_logging`).

Only the named :mod:`ktl` methods are used: ``ktl.cache``, the keyword
methods ``read``, ``write``, ``monitor``, ``waitFor``, and ``callback``, the
service method ``heartbeat``, and ``ktl.logger``/``ktl.loglevel``.
"""
import logging
import re
import warnings

from nickel_focus import log

try:
    import ktl
except ImportError as e:
    warnings.warn(f'Unable to import ktl package ({e}).  Functionality will be limited.')
    ktl = None


DEFAULT_READ_TIMEOUT = 1.0
"""Seconds to wait for a service to answer a read before giving up."""


class KTLError(Exception):
    """A KTL call failed."""


class KTLUnavailableError(KTLError, RuntimeError):
    """The :mod:`ktl` package is not available, so no service can be used."""


class KTLTimeoutError(KTLError):
    """A service did not answer a read within its timeout."""


class KTLReadOnlyError(KTLError):
    """A write was attempted on a service constructed with ``read_only=True``."""


def ktl_available():
    """Return True if the :mod:`ktl` package was imported successfully."""
    return ktl is not None


class KTLLogWriter:
    """
    A destination for :func:`ktl.logger` that passes KTL's log messages on to
    the :mod:`nickel_focus` log.

    KTL's logging thread writes one line per message,
    ``'<time> <flag>: <text>'``, where the flag gives its severity.  Each
    line's text is logged as ``'ktl: <text>'`` at the matching level (see
    :attr:`LEVELS`); KTL's own timestamp is dropped.  A line in any other
    form is logged as a warning, as written.
    """

    LEVELS = {
        'E': logging.ERROR,
        'W': logging.WARNING,
        'N': logging.INFO,
        # KTL's "info" messages are internal detail, so they're debug output here.
        'I': logging.DEBUG,
        'D': logging.DEBUG,
    }
    """The log level for each KTL severity flag."""

    _LINE = re.compile(r'^\d+(\.\d*)? (?P<flag>\S): (?P<text>.*)$', re.DOTALL)

    def __init__(self):
        self._partial = ''

    def write(self, text):
        """Log each complete line of ``text``; keep any unfinished line for later."""
        lines = (self._partial + text).split('\n')
        self._partial = lines.pop()
        for line in lines:
            if line.strip():
                self._log_line(line)
        return len(text)

    def flush(self):
        """Nothing to flush: each line is logged as soon as it's complete."""

    def _log_line(self, line):
        match = self._LINE.match(line)
        if match is None or match['flag'] not in self.LEVELS:
            log.warning(f'ktl: {line}')
            return
        log.log(self.LEVELS[match['flag']], f"ktl: {match['text']}")


_KTL_LEVELS = {'warn': 'LOG_WARN', 'notice': 'LOG_NOTICE', 'debug': 'LOG_DEBUG'}

# The ktl module whose log has been routed (see route_ktl_logging).
_routed_ktl = None


def route_ktl_logging(ktl_module):
    """
    Pass KTL's own log on to the :mod:`nickel_focus` log (once per ``ktl``
    module), and set how much of it KTL emits from
    :attr:`log.ktl_level <nickel_focus.pkg.logger.NickelFocusLogger.ktl_level>`
    (``'warn'`` unless set otherwise, e.g. by a script's ``--verbosity``).

    Called whenever a :class:`KTLService` is constructed, so the level is
    re-applied each time; ``ktl`` itself is never imported just for this.

    Parameters
    ----------
    ktl_module : module
        The :mod:`ktl` module in use.
    """
    global _routed_ktl
    if _routed_ktl is not ktl_module:
        ktl_module.logger(KTLLogWriter())
        _routed_ktl = ktl_module
    ktl_module.loglevel(getattr(ktl_module, _KTL_LEVELS[log.ktl_level]))


def require(result, false_message, no_reply_message):
    """
    Turn the result of :meth:`KTLService.check` into an exception unless the
    condition held.

    Parameters
    ----------
    result : :obj:`bool` or None
        The result of :meth:`KTLService.check`.
    false_message : :obj:`str`
        The error message if the condition was false.
    no_reply_message : :obj:`str`
        The error message if the service didn't reply.

    Raises
    ------
    ValueError
        Raised if ``result`` is False: the service answered, and the
        condition doesn't hold.
    KTLTimeoutError
        Raised if ``result`` is None: the service didn't reply, so the
        condition is unknown.
    """
    if result is None:
        raise KTLTimeoutError(no_reply_message)
    if not result:
        raise ValueError(false_message)


class KTLService:
    """
    One KTL service: access to its keywords, with timeouts on every read.

    Subclasses set :attr:`service` and add named methods for what their
    keywords mean; callers should use those rather than keyword names where
    one exists.

    Parameters
    ----------
    read_only : :obj:`bool`, optional
        If True, :meth:`write` raises :class:`KTLReadOnlyError` without
        contacting the service, so no keyword value can be changed through
        this object.  Construction itself never writes.

    Raises
    ------
    KTLUnavailableError
        Raised if the :mod:`ktl` package is not available.
    KTLError, KTLTimeoutError
        Raised if a keyword in :attr:`monitored` (or the :attr:`heartbeat`)
        can't be monitored or its first value isn't received within
        :data:`DEFAULT_READ_TIMEOUT`, or if the heartbeat can't be
        registered.
    """

    service = None
    """The KTL service name, e.g. ``'nickelpoco'``."""

    monitored = ()
    """
    Keywords monitored from construction on.  Each is subscribed to without
    :mod:`ktl`'s own priming read (which has no timeout) and then read once
    with a timeout, so an unresponsive service fails construction instead of
    hanging it.
    """

    heartbeat = None
    """
    A keyword the service broadcasts regularly, or None.  It is monitored like
    :attr:`monitored` and registered with :mod:`ktl` as the service's
    heartbeat, so that if its broadcasts stop while the service still
    answers, :mod:`ktl` reconnects to the service and restores monitoring.
    """

    heartbeat_period = None
    """
    The longest expected interval, in seconds, between :attr:`heartbeat`
    broadcasts.  Too short a period makes :mod:`ktl` reconnect needlessly.
    """

    def __init__(self, read_only=False):
        if ktl is None:
            raise KTLUnavailableError(
                f'The ktl package is not available.  Connecting to the {self.service} service '
                'requires a kpython environment with ktl installed.'
            )
        # The module this service talks to, kept for its lifetime.
        self._ktl = ktl
        route_ktl_logging(ktl)
        self.read_only = read_only
        # Strong references to the callbacks registered by subscribe(): ktl
        # holds callbacks only weakly, and drops any that nothing else holds.
        self._subscriptions = []
        for name in self.monitored:
            self._monitor(name)
        if self.heartbeat is not None:
            self._register_heartbeat()

    def __repr__(self):
        mode = ', read_only=True' if self.read_only else ''
        return f'{type(self).__name__}({self.service!r}{mode})'

    def _keyword(self, name):
        """Return :mod:`ktl`'s keyword object for ``name`` in this service."""
        try:
            return self._ktl.cache(self.service, name)
        except (self._ktl.ktlError, KeyError) as e:
            raise KTLError(f'Cannot access {self.service}.{name}: {e}') from e

    def _monitor(self, name, timeout=DEFAULT_READ_TIMEOUT):
        """Start monitoring ``name``, then prime it with a read (see :attr:`monitored`)."""
        try:
            self._keyword(name).monitor(prime=False)
        except self._ktl.ktlError as e:
            raise KTLError(f'Cannot monitor {self.service}.{name}: {e}') from e
        self.read(name, timeout=timeout)

    def _register_heartbeat(self):
        """Monitor :attr:`heartbeat` and register it with :mod:`ktl` (see there)."""
        self._monitor(self.heartbeat)
        try:
            self._ktl.cache(self.service).heartbeat(self.heartbeat, self.heartbeat_period)
        except (self._ktl.ktlError, KeyError) as e:
            raise KTLError(
                f'Cannot register {self.service}.{self.heartbeat} as a heartbeat: {e}'
            ) from e

    def read(self, name, binary=False, timeout=DEFAULT_READ_TIMEOUT):
        """
        Read a keyword's current value from the service.

        Parameters
        ----------
        name : :obj:`str`
            Keyword name.
        binary : :obj:`bool`, optional
            Return the binary value instead of the ascii (string) value.
        timeout : :obj:`float`, optional
            Seconds to wait for the service's reply.

        Returns
        -------
        :obj:`str` or the keyword's binary type
            The value.

        Raises
        ------
        KTLTimeoutError
            Raised if the service doesn't reply within ``timeout``.
        KTLError
            Raised if the read fails.
        """
        keyword = self._keyword(name)
        try:
            return keyword.read(binary=binary, timeout=timeout)
        except self._ktl.TimeoutException as e:
            raise KTLTimeoutError(
                f'{self.service}.{name} did not answer a read within {timeout} s.'
            ) from e
        except self._ktl.ktlError as e:
            raise KTLError(f'Cannot read {self.service}.{name}: {e}') from e

    def write(self, name, value, binary=False):
        """
        Write a keyword, waiting for the service to report that the write is
        complete.

        Writes have no timeout: what "complete" means differs by keyword (for
        some it may include a mechanism finishing its move), so any limit
        would have to be set per keyword.

        Parameters
        ----------
        name : :obj:`str`
            Keyword name.
        value
            The value to write.
        binary : :obj:`bool`, optional
            Write ``value`` as the binary value instead of as ascii.

        Raises
        ------
        KTLReadOnlyError
            Raised, before contacting the service, if this object is
            read-only.
        KTLError
            Raised if the write fails.
        """
        if self.read_only:
            raise KTLReadOnlyError(
                f'Not writing {self.service}.{name} = {value!r}: this {type(self).__name__} '
                'is read-only.'
            )
        keyword = self._keyword(name)
        try:
            keyword.write(value, binary=binary)
        except self._ktl.ktlError as e:
            raise KTLError(f'Cannot write {self.service}.{name} = {value!r}: {e}') from e

    def wait_for(self, name, expression, timeout):
        """
        Wait until a keyword's value satisfies ``expression``.

        String comparisons are case-insensitive.  If the keyword isn't
        monitored yet, this starts monitoring it, and its first value must
        then arrive within ``timeout``.

        Parameters
        ----------
        name : :obj:`str`
            Keyword name.
        expression : :obj:`str`
            A comparison with the keyword's value as its left-hand side,
            e.g. ``'== allowed'``.
        timeout : :obj:`float`
            Seconds to wait.

        Returns
        -------
        :obj:`bool`
            True if the expression was satisfied within ``timeout``; False if
            not, which includes the case where no value was received at all.
        """
        keyword = self._keyword(name)
        try:
            return bool(keyword.waitFor(expression, timeout=timeout))
        except self._ktl.ktlError as e:
            raise KTLError(f'Cannot wait for {self.service}.{name} {expression}: {e}') from e

    def check(self, name, expression, timeout):
        """
        Wait until a keyword's value satisfies ``expression``, and tell a false
        condition apart from a service that doesn't reply.

        If :meth:`wait_for` returns False, the keyword is read once (with
        :data:`DEFAULT_READ_TIMEOUT`): a reply means the condition really was
        false; a timeout means the service isn't answering, so the condition
        is unknown.

        Parameters
        ----------
        name : :obj:`str`
            Keyword name.
        expression : :obj:`str`
            A comparison with the keyword's value as its left-hand side,
            e.g. ``'== allowed'``.
        timeout : :obj:`float`
            Seconds to wait for the condition.

        Returns
        -------
        :obj:`bool` or None
            True if the condition held within ``timeout``, False if it
            didn't, or None if the service didn't reply.  See :func:`require`
            to turn the result into an exception.
        """
        if self.wait_for(name, expression, timeout):
            return True
        try:
            self.read(name)
        except KTLTimeoutError:
            return None
        return False

    def heartbeat_alive(self, timeout):
        """
        Whether the :attr:`heartbeat` keyword is broadcasting: read it, then
        wait up to ``timeout`` seconds for its value to change.

        Returns
        -------
        :obj:`bool`
            True if the value changed within ``timeout``.

        Raises
        ------
        KTLError
            Raised if this service has no heartbeat keyword, or the read fails.
        """
        if self.heartbeat is None:
            raise KTLError(f'The {self.service} service has no heartbeat keyword.')
        value = self.read(self.heartbeat)
        return self.wait_for(self.heartbeat, f'!= {value}', timeout)

    def subscribe(self, names, function):
        """
        Call ``function(service, keyword, value)`` with each new value of the
        named keywords: every broadcast, and every completed read.

        Each keyword is monitored (if it isn't already) and then read once, so
        ``function`` also receives each keyword's current value right away.
        ``value`` is the ascii value, as :meth:`read` returns by default.

        ``function`` is called on :mod:`ktl`'s own callback thread, not the
        caller's; it must not block, and a GUI must hand the value over to its
        own thread (e.g. with a queued Qt signal).  Exceptions it raises are
        logged by :mod:`ktl`, not raised.  The subscription lasts as long as
        this service object.

        Parameters
        ----------
        names : iterable of :obj:`str`
            Keyword names.
        function : callable
            Called as ``function(service, keyword, value)``, where
            ``service`` and ``keyword`` are names (:obj:`str`).

        Raises
        ------
        KTLError, KTLTimeoutError
            Raised if a keyword can't be subscribed to or its current value
            isn't received within :data:`DEFAULT_READ_TIMEOUT`.
        """
        for name in names:
            keyword = self._keyword(name)

            # ktl calls this with a frozen copy of the keyword, whose read()
            # returns the value of this update without contacting the service.
            def relay(update, name=name):
                function(self.service, name, update.read())

            self._subscriptions.append(relay)
            try:
                keyword.callback(relay)
            except self._ktl.ktlError as e:
                raise KTLError(f'Cannot subscribe to {self.service}.{name}: {e}') from e
            self._monitor(name)
