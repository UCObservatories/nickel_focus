"""
Access to the KTL services used by :mod:`nickel_focus`: one class per service
(:class:`~nickel_focus.services.nickelpoco.NickelPoco` for the telescope,
:class:`~nickel_focus.services.nscicam.NSciCam` for the science camera), built
on :class:`~nickel_focus.services.base.KTLService`.

Importing this package imports :mod:`ktl` (if available); see
:mod:`nickel_focus.services.base`.
"""
from nickel_focus.services.base import (
    DEFAULT_READ_TIMEOUT, KTLError, KTLReadOnlyError, KTLService, KTLTimeoutError,
    KTLUnavailableError, ktl_available, require,
)
from nickel_focus.services.nickelpoco import NickelPoco
from nickel_focus.services.nscicam import NSciCam
