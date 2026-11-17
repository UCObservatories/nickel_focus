"""
Shared pytest configuration and fixtures for the Nickel automation test
suite.

Forces a non-interactive matplotlib backend before anything imports
:mod:`nickel_focus.focus` (which imports :mod:`matplotlib.pyplot`), so
tests never require a display.

Per the project's testing strategy, this suite must never touch real
telescope/camera hardware via ``ktl``, regardless of whether the ``ktl``
package happens to be installed in the environment running the tests --
see the autouse :func:`no_ktl` fixture below, which sets
:mod:`nickel_focus.services.base`'s ``ktl`` (the only reference to
:mod:`ktl` in the package) to ``None`` for every test, unless a test
installs the fake one instead (:func:`fake_hardware`/:func:`fake_telescope`).

The one exception is the ``ktl_live`` tests, which use the real services and
run only when explicitly requested with ``--ktl-live``; see
:func:`pytest_addoption`.
"""
import matplotlib
matplotlib.use('Agg')

import numpy as np
import pytest
from astropy.io import fits

from nickel_focus.services import base as services_base
from nickel_focus.services import NickelPoco, NSciCam
from nickel_focus.tests.fake_ktl import Simulator


def pytest_addoption(parser):
    """
    Add ``--ktl-live``, which runs the ``ktl_live`` tests against the real
    services.  They never change a keyword value, but they need a live
    ``ktl`` connection, and fail (rather than skip) without one.
    """
    parser.addoption(
        '--ktl-live', action='store_true', default=False,
        help='Also run the ktl_live tests, which read (never write) the real KTL services.'
    )


def pytest_configure(config):
    config.addinivalue_line(
        'markers',
        'ktl_live: reads the real KTL services; run only with --ktl-live, and never writes.'
    )


def pytest_collection_modifyitems(config, items):
    """Skip every ``ktl_live`` test unless ``--ktl-live`` was given."""
    if config.getoption('--ktl-live'):
        return
    skip = pytest.mark.skip(reason='needs --ktl-live (reads the real KTL services)')
    for item in items:
        if 'ktl_live' in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_ktl(request, monkeypatch):
    """
    Set ``services.base.ktl`` to ``None`` for every test, regardless of
    whether the real ``ktl`` package happens to be importable in the
    environment running the suite.

    Every KTL service (and so everything that talks to hardware) is built
    on :class:`~nickel_focus.services.base.KTLService`, whose constructor
    raises :class:`~nickel_focus.services.base.KTLUnavailableError` when
    ``ktl`` is ``None``, so this guarantees no test can reach real
    hardware.  :func:`fake_hardware`/:func:`fake_telescope` replace it
    again afterward with a fake.

    Tests marked ``ktl_live`` (which only run under ``--ktl-live``) keep the
    real ``ktl``, and fail here if it isn't available: the live tests were
    explicitly requested, so a missing ``ktl`` is an error, not a reason to
    report success.
    """
    if request.node.get_closest_marker('ktl_live') is not None:
        if not services_base.ktl_available():
            pytest.fail('--ktl-live was given, but the ktl package is not available.')
        return
    monkeypatch.setattr(services_base, 'ktl', None)


def gaussian_frame(fwhm, amplitude=8000., background=200., noise_sigma=5.,
                    shape=(101, 101), center=None, seed=0):
    """
    Generate a small synthetic image with a single 2D Gaussian source.

    A Gaussian (rather than, e.g., a Moffat profile) is used because
    :func:`photometry.evaluate_shape` converts its moment-based sigma to a
    FWHM using the Gaussian relationship
    (``FWHM = 2*sqrt(2*ln(2))*sigma``), so a Gaussian source is what lets
    the requested ``fwhm`` be recovered (up to segmentation-mask
    truncation effects) rather than introducing an unrelated,
    profile-dependent bias.

    Parameters
    ----------
    fwhm : :obj:`float`
        Full width at half maximum of the source, in pixels.
    amplitude : :obj:`float`, optional
        Peak source flux, in counts.
    background : :obj:`float`, optional
        Constant background level, in counts.
    noise_sigma : :obj:`float`, optional
        Standard deviation of the Gaussian noise added to every pixel.
    shape : :obj:`tuple`, optional
        ``(ny, nx)`` shape of the returned array.
    center : :obj:`tuple`, optional
        ``(x, y)`` center of the source.  Defaults to the center of the
        frame.
    seed : :obj:`int`, optional
        Seed for the noise random number generator, so tests are
        deterministic.

    Returns
    -------
    :class:`numpy.ndarray`
        The synthetic image, as ``float32``.
    """
    ny, nx = shape
    if center is None:
        center = (nx / 2, ny / 2)
    y, x = np.mgrid[0:ny, 0:nx]

    sigma = fwhm / (2 * np.sqrt(2 * np.log(2)))
    source = amplitude * np.exp(-((x - center[0])**2 + (y - center[1])**2) / (2 * sigma**2))

    rng = np.random.default_rng(seed)
    data = background + source + rng.normal(scale=noise_sigma, size=shape)
    return data.astype(np.float32)


@pytest.fixture
def focus_sweep(tmp_path):
    """
    Write a small synthetic focus sequence to disk.

    The sequence follows a known quadratic,
    ``fwhm(focus) = fwhm_min + curvature*(focus - best_focus)**2``, so
    tests can check that fitting the sequence recovers ``best_focus`` and
    ``fwhm_min``. Filenames follow the ``{prefix}{obsnum}{suffix}``
    convention :func:`focus.main` expects for ``--obsnum``/``--datadir``/
    ``--prefix``/``--suffix`` archive-mode replay.

    Returns
    -------
    :obj:`dict`
        Keys: ``focus_values`` (:obj:`list` of :obj:`float`), ``files``
        (:obj:`list` of :class:`pathlib.Path`, one per focus value),
        ``best_focus``, ``fwhm_min`` (:obj:`float`, the true values used to
        generate the sweep), ``datadir`` (:class:`pathlib.Path`),
        ``prefix``, ``suffix`` (:obj:`str`), and ``obsnum`` (:obj:`int`,
        the observation number of the first file).
    """
    best_focus = 350.
    fwhm_min = 3.0
    curvature = 0.01
    focus_values = [340., 345., 350., 355., 360.]
    obsnum = 2000
    prefix, suffix = 'n', '.fits'

    files = []
    for i, focus_value in enumerate(focus_values):
        fwhm = fwhm_min + curvature * (focus_value - best_focus)**2
        data = gaussian_frame(fwhm, seed=i)
        path = tmp_path / f'{prefix}{obsnum + i}{suffix}'
        fits.writeto(path, data, overwrite=True)
        files.append(path)

    return {
        'focus_values': focus_values,
        'files': files,
        'best_focus': best_focus,
        'fwhm_min': fwhm_min,
        'datadir': tmp_path,
        'prefix': prefix,
        'suffix': suffix,
        'obsnum': obsnum,
    }


@pytest.fixture
def fake_hardware(tmp_path, monkeypatch):
    """
    Install a fake ``ktl`` (see ``tests/fake_ktl.py``) whose simulated
    telescope and camera take exposures, so live
    (:class:`focus.GridFocusSequence`/:class:`focus.AutomatedFocusSequence`)
    sequences, and the GUI, can run on the real service and device classes
    with no hardware.  Each exposure is a small Gaussian-source FITS frame
    for whatever focus value is current at the time, following the same
    ``fwhm_min + curvature*(focus - best_focus)**2`` relationship as the
    :func:`focus_sweep` fixture, so sequences exercise a known, checkable
    focus curve.

    Returns
    -------
    :obj:`dict`
        Keys: ``best_focus``, ``fwhm_min`` (:obj:`float`, the true values
        used to generate frames); ``sim`` (the
        :class:`~nickel_focus.tests.fake_ktl.Simulator`, e.g. to check the
        commanded focus or change a keyword); ``poco`` and ``cam`` (a
        :class:`~nickel_focus.services.nickelpoco.NickelPoco` and
        :class:`~nickel_focus.services.nscicam.NSciCam` on the fake, to pass
        to a sequence); and ``directory`` (:class:`pathlib.Path`, where
        exposures are written).
    """
    best_focus = 350.
    fwhm_min = 3.0
    curvature = 0.01

    def fwhm_for(focus_value):
        return fwhm_min + curvature * (focus_value - best_focus) ** 2

    def make_frame(focus_value):
        return gaussian_frame(fwhm_for(focus_value), seed=round(focus_value * 10))

    sim = Simulator(make_frame=make_frame, directory=tmp_path)
    monkeypatch.setattr(services_base, 'ktl', sim.ktl)

    return {
        'best_focus': best_focus,
        'fwhm_min': fwhm_min,
        'sim': sim,
        'poco': NickelPoco(),
        'cam': NSciCam(),
        'directory': tmp_path,
    }


@pytest.fixture
def fake_telescope(monkeypatch):
    """
    Install a fake ``ktl`` (see ``tests/fake_ktl.py``) with a simulated
    telescope, so code that constructs a
    :class:`~nickel_focus.services.nickelpoco.NickelPoco` (e.g.
    :class:`nickel_focus.scripts.slew_to_nearest.NickelSlewToNearest`, or the
    GUI) runs with no hardware.

    Returns
    -------
    :class:`~nickel_focus.tests.fake_ktl.Simulator`
        The simulator, so a test can set ``ra``/``dec`` or a failure mode
        (``movement_allowed``, ``tracking_on``, ``ready``,
        ``reaches_target``) before running the code under test, and inspect
        ``slew_calls`` afterward.  It can't take exposures.
    """
    sim = Simulator()
    monkeypatch.setattr(services_base, 'ktl', sim.ktl)
    return sim
