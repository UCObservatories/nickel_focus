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

Only the named :mod:`ktl` methods are used: ``ktl.cache`` and the keyword
methods ``read``, ``write``, ``monitor``, and ``waitFor``.
"""
import warnings

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
        Raised if a keyword in :attr:`monitored` can't be monitored or its
        first value isn't received within :data:`DEFAULT_READ_TIMEOUT`.
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

    def __init__(self, read_only=False):
        if ktl is None:
            raise KTLUnavailableError(
                f'The ktl package is not available.  Connecting to the {self.service} service '
                'requires a kpython environment with ktl installed.'
            )
        # The module this service talks to, kept for its lifetime.
        self._ktl = ktl
        self.read_only = read_only
        for name in self.monitored:
            self._monitor(name)

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
