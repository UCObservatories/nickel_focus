"""
Wires the View (:class:`~nickel_focus.gui.views.main_window.MainWindow` and its
panels) to the Model (:class:`~nickel_focus.focus.FocusSequence` subclasses, via
:class:`~nickel_focus.gui.model.focus_worker.FocusWorker`; and
:class:`~nickel_focus.slew.NickelTelescopePointing`, via
:class:`~nickel_focus.gui.model.slew_worker.SlewWorker`).

Every hardware operation (an exposure, a focus move, a slew) blocks, so each
runs on its own worker thread rather than the GUI thread, and only one may run
at a time.
"""
import logging

from astropy.coordinates import Angle, SkyCoord

from nickel_focus import focus
from nickel_focus import log
from nickel_focus import slew
from nickel_focus.services import NickelPoco, NSciCam, ktl_available
from nickel_focus.services.nscicam import ExposureConfig
from nickel_focus.gui.ktl_bridge import KTLBridge
from nickel_focus.gui.log_handler import QtLogHandler
from nickel_focus.gui.model.focus_worker import FocusWorker
from nickel_focus.gui.model.slew_worker import SlewWorker
from nickel_focus.gui.qt import QtCore


class Controller(QtCore.QObject):
    """
    Owns the currently active :class:`~nickel_focus.focus.FocusSequence` (if
    any) and the :class:`~nickel_focus.gui.model.focus_worker.FocusWorker`
    driving it, and mediates between it and a
    :class:`~nickel_focus.gui.views.main_window.MainWindow`.

    Implements "hardware exclusivity": only one operation can be active at a
    time, and interactive source selection
    (:attr:`~nickel_focus.gui.views.image_panel.ImagePanel.sourceSelected`) is
    only enabled while nothing is running.  All three sequence types (Grid,
    Automated, Archive/Replay), reanalysis, and the Single tab (which also
    serves as "move to best focus" -- see
    :class:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel`) are
    wired up.

    Also owns the two KTL service objects shared by everything that talks to
    the hardware -- :attr:`~nickel_focus.gui.controller.Controller.poco`
    (:class:`~nickel_focus.services.nickelpoco.NickelPoco`, the telescope) and
    :attr:`~nickel_focus.gui.controller.Controller.cam`
    (:class:`~nickel_focus.services.nscicam.NSciCam`, the camera) -- which it
    passes to each sequence it builds, and the
    :class:`~nickel_focus.slew.NickelTelescopePointing` handle used by the
    Slew tab:
    :meth:`~nickel_focus.gui.controller.Controller.move_to_target` (a slew, via
    :class:`~nickel_focus.gui.model.slew_worker.SlewWorker`) participates in the
    same hardware-exclusivity state as a focus sequence -- the telescope
    shouldn't move while it's mid-exposure, or vice versa -- while
    :meth:`~nickel_focus.gui.controller.Controller.find_nearest_object`/
    :meth:`~nickel_focus.gui.controller.Controller.find_nearest_pointing`/
    :meth:`~nickel_focus.gui.controller.Controller.find_nearest_focus` (a single
    fast ktl read plus a starlist search, not a move) do not.

    The :class:`~nickel_focus.gui.views.status_bar.StatusBar` indicators and
    the Slew tab's live position display follow the telescope's and camera's
    keyword broadcasts, whatever else is running and whoever changed them
    (e.g. another GUI): the Controller subscribes to those keywords (see
    :meth:`~nickel_focus.services.base.KTLService.subscribe`), and a
    :class:`~nickel_focus.gui.ktl_bridge.KTLBridge` hands each new value to the
    GUI thread (see
    :meth:`~nickel_focus.gui.controller.Controller._on_keyword_changed`).
    Position updates are redrawn at most every
    :attr:`~nickel_focus.gui.controller.Controller.POSITION_REDRAW_MS`
    milliseconds.  The status bar's Quit button closes the window; like the
    action buttons, it's disabled while anything is running.

    The Single/Grid/Auto tabs' exposure fields follow the camera the same way
    (``EXPOSURE``, ``AMPCONF``, ``BINNING``; see
    :meth:`~nickel_focus.gui.controller.Controller._on_exposure_keyword`), and
    a setting the user commits there is written to the camera at once (see
    :meth:`~nickel_focus.gui.controller.Controller._on_exposure_setting_edited`).

    Without a live ``ktl`` connection, the Slew/Single/Grid/Auto tabs can't do
    anything but fail -- see
    :attr:`~nickel_focus.gui.controller.Controller.ktl_available` and
    :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.set_hardware_tabs_enabled`.

    Parameters
    ----------
    window : :class:`~nickel_focus.gui.views.main_window.MainWindow`
        The window to wire up.
    parent : :class:`PySide6.QtCore.QObject`, optional
        Passed through to the base :class:`PySide6.QtCore.QObject` constructor.
    force_enable_hardware_tabs : :obj:`bool`, optional
        Leave the Slew/Single/Grid/Auto tabs enabled even with no live ``ktl``
        connection, so a developer can still see and click through them while
        working on the GUI. Every action on those tabs still fails exactly as it
        would otherwise (each checks
        :attr:`~nickel_focus.gui.controller.Controller.poco`/
        :attr:`~nickel_focus.gui.controller.Controller.cam` itself) -- this only
        skips graying them out.  Wired to the GUI script's suppressed ``--test`` flag; never set
        outside of that.
    """

    MAX_TARGET_OFFSET_ARCMIN = 2.
    """
    How far, in arcminutes, the telescope may be from the Slew tab's target for
    its name to be recorded as the exposures' object (see
    :meth:`~nickel_focus.gui.controller.Controller._name_object`).
    """

    POSITION_REDRAW_MS = 200
    """
    The shortest interval, in milliseconds, between redraws of the Slew tab's
    current position, however often ``POCORAA``/``POCODECA`` broadcast.
    """

    def __init__(self, window, parent=None, force_enable_hardware_tabs=False):
        super().__init__(parent)
        self.window = window

        # `log` is a module-level singleton, not per-Controller, so a new
        # Controller must remove any handler a previous one added before
        # adding its own -- otherwise a stale handler pointing at a
        # since-destroyed log_widget accumulates each time a Controller is
        # (re)constructed.
        log.remove_handlers_of_type(QtLogHandler)
        self._log_handler = QtLogHandler(self)
        # The Log tab repeats the console: the same level (e.g. per the GUI
        # script's --verbosity) and the same formatter, whose ANSI colors the
        # Log tab renders.
        self._log_handler.setFormatter(type(log.sh.formatter)())
        self._log_handler.setLevel(log.sh.level)
        self._log_handler.record_logged.connect(window.control_panel.append_log_line)
        log.addHandler(self._log_handler)

        self.focus_sequence = None  # the current focus.FocusSequence, or None
        self.focus_worker = None    # the FocusWorker currently running, or None
        self.method = 'brightest'   # the photometry method currently in effect
        self.slew_worker = None     # the SlewWorker currently running, or None

        # The throwaway `focus.FocusSequence` used only to take/reanalyze
        # a standalone Single-tab exposure (its hardware handles, not its
        # bookkeeping, matter -- see `take_single_exposure`). Seeded with
        # that one exposure's data so `reanalyze()` (via interactive
        # source selection) can run against it even though no real
        # sequence is loaded.
        self._standalone_focus_sequence = None

        # The telescope and camera services, shared by the status
        # indicators, the Slew tab, and every sequence or single exposure.
        # Each is `None` if it can't be used (no ktl, or the service didn't
        # answer) -- every method below that needs one checks for `None`
        # first and reports a clear failure instead.
        self.poco = self._connect_service(NickelPoco)
        self.cam = self._connect_service(NSciCam)

        # The telescope-pointing handle backing the Slew tab; `None`
        # without the telescope service.
        self.telescope = (
            None if self.poco is None else slew.NickelTelescopePointing(self.poco)
        )

        # Whether ktl itself is available; `force_enable_hardware_tabs`
        # overrides only the View-level graying-out below, not this flag.
        self.ktl_available = ktl_available()
        window.control_panel.set_hardware_tabs_enabled(
            self.ktl_available or force_enable_hardware_tabs)

        # This is Qt's signal/slot mechanism, the backbone of how every
        # View talks to this Controller: each `connect()` call below
        # says "whenever this Signal is emitted (e.g.
        # `window.control_panel.startRequested.emit()`, from a button's
        # `clicked` handler), call this plain Python method (the
        # 'slot')." The View never calls `self.start_focus_sequence()`
        # directly -- it only knows how to `.emit()` its own signals, so
        # it stays usable even if what eventually happens in response
        # (or whether anything does) changes.
        window.control_panel.startRequested.connect(self.start_focus_sequence)
        window.control_panel.stopRequested.connect(self.stop)
        window.control_panel.takeSingleExposureRequested.connect(self.take_single_exposure)
        window.control_panel.moveToTargetRequested.connect(self.move_to_target)
        window.control_panel.findNearestObjectRequested.connect(self.find_nearest_object)
        window.control_panel.findNearestPointingRequested.connect(self.find_nearest_pointing)
        window.control_panel.findNearestFocusRequested.connect(self.find_nearest_focus)
        window.image_panel.sourceSelected.connect(self._on_source_selected)
        window.status_bar.quitRequested.connect(self.quit)
        window.control_panel.exposureSettingEdited.connect(self._on_exposure_setting_edited)

        # The status indicators and current position follow the keywords'
        # broadcasts. ktl delivers each new value on its own callback
        # thread; the bridge re-emits it as a Qt signal, and the queued
        # connection runs `_on_keyword_changed` on the GUI thread.
        self._latest = {}  # (service, keyword) -> the latest value received
        # The exposure settings as the camera last reported them, and as the
        # exposure fields show them (key -> value, in ExposureConfig's form).
        self._camera_exposure = {}
        self._shown_exposure = {}
        self._bad_position_logged = False
        # Position broadcasts may be frequent: redraw at most once per
        # POSITION_REDRAW_MS (a QTimer interval is in ms, not seconds).
        self._position_timer = QtCore.QTimer(self)
        self._position_timer.setSingleShot(True)
        self._position_timer.setInterval(self.POSITION_REDRAW_MS)
        self._position_timer.timeout.connect(self._show_current_position)
        self._ktl_bridge = KTLBridge(self)
        self._ktl_bridge.keywordChanged.connect(
            self._on_keyword_changed, QtCore.Qt.ConnectionType.QueuedConnection
        )
        for key in _INDICATORS:
            self._show_indicator(key)
        self._show_current_position()
        self._subscribe(self.poco, _POCO_DISPLAY_KEYWORDS)
        self._subscribe(self.cam, _CAM_DISPLAY_KEYWORDS)

        self._set_running(False)

    # -- actions the View can request ---------------------------------------

    def start_focus_sequence(self):
        """
        Build a sequence from the control panel's configuration and run it.
        """
        if self.focus_worker is not None or self.slew_worker is not None:
            return  # hardware exclusivity: something is already running

        focus_sequence_type = self.window.control_panel.get_focus_sequence_type()
        config = self.window.control_panel.get_focus_sequence_config()

        try:
            if focus_sequence_type == 'archive':
                focus_sequence = self._build_archive_focus_sequence(config)
            elif focus_sequence_type == 'grid':
                focus_sequence = focus.GridFocusSequence(
                    config['start'], config['step'], nstep=config['nstep'], poco=self.poco,
                    cam=self.cam
                )
            else:
                focus_sequence = focus.AutomatedFocusSequence(
                    config['start'], config['step'], maxsteps=config['maxsteps'],
                    poco=self.poco, cam=self.cam
                )
        except Exception as e:
            self._fail(f'Could not start sequence: {e}', e)
            return

        if (
            focus_sequence_type != 'archive'
            and (focus_sequence._focus is None or focus_sequence._exposure is None)
        ):
            self._fail(
                'Could not start sequence: no ktl connection is available for a live sequence.'
            )
            return

        self._name_object(focus_sequence)
        self.focus_sequence = focus_sequence
        self._standalone_focus_sequence = None
        self.window.image_panel.reset()
        self.window.curve_panel.reset()

        exp_kwargs = (
            None if focus_sequence_type == 'archive'
            else self.window.control_panel.get_exposure_config()
        )
        self._start_focus_worker(self.focus_sequence, mode='step', exp_kwargs=exp_kwargs)

    def _build_archive_focus_sequence(self, config):
        """
        Build a :class:`~nickel_focus.focus.ArchiveFocusSequence` from the
        Replay tab's ``config``, or raise.
        """
        # GridFocusSequence is only used here for its focus-value
        # arithmetic (matches focus.py's own CLI archive-mode path), so
        # it's built without the services, keeping it from touching
        # hardware.
        grid = focus.GridFocusSequence(config['start'], config['step'], nstep=config['nstep'])
        expected_files = [
            config['datadir'] / f"{config['prefix']}{config['obsnum'] + i}{config['suffix']}"
            for i in range(grid.nstep)
        ]
        missing = [f for f in expected_files if not f.is_file()]
        if len(missing) > 0:
            raise FileNotFoundError(
                'Expected to find the following files, but they are not available: '
                + ', '.join(str(f) for f in missing)
            )
        return focus.ArchiveFocusSequence(list(grid.target_focus), expected_files)

    def reanalyze(self):
        """
        Re-run photometry on the already-collected exposures of whatever is
        currently loaded: the active
        :attr:`~nickel_focus.gui.controller.Controller.focus_sequence`, or -- if
        none is loaded -- a standalone Single-tab exposure, so a source can be
        marked on it before any sequence exists.
        """
        target = (
            self.focus_sequence if self.focus_sequence is not None
            else self._standalone_focus_sequence
        )
        if (
            self.focus_worker is not None or self.slew_worker is not None or target is None
            or not target.exposures
        ):
            return
        if target is self.focus_sequence:
            # Clear the curve immediately rather than letting old points
            # be replaced one at a time as each re-measured result
            # streams in -- otherwise the plot would show a confusing
            # mix of old and new measurements while reanalysis runs.
            self.window.curve_panel.reset()
        self._start_focus_worker(target, mode='reanalyze')

    def take_single_exposure(self, focus_value):
        """
        Take one exposure at ``focus_value`` with no sequence bookkeeping -- the
        Single tab's action, which doubles as "move to best focus" when
        ``focus_value`` is left at its default (the most recent fitted best
        focus; see
        :meth:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.show_best_focus`).
        Uses a throwaway :class:`~nickel_focus.focus.FocusSequence` (built on
        the shared services) for its hardware handles, independent of any
        loaded :attr:`~nickel_focus.gui.controller.Controller.focus_sequence`.
        """
        if self.focus_worker is not None or self.slew_worker is not None:
            return  # hardware exclusivity: something is already running
        standalone = focus.FocusSequence(poco=self.poco, cam=self.cam)
        if standalone._focus is None or standalone._exposure is None:
            self._fail('Could not take single exposure: no ktl connection is available.')
            return
        self._name_object(standalone)
        self._standalone_focus_sequence = standalone
        exp_kwargs = self.window.control_panel.get_exposure_config()
        self._start_focus_worker(
            standalone, mode='single', exp_kwargs=exp_kwargs, focus_value=focus_value
        )

    def stop(self):
        """
        Request that the running sequence stop between steps, never
        mid-exposure; see
        :meth:`~nickel_focus.gui.model.focus_worker.FocusWorker.request_stop`.
        """
        if self.focus_worker is None:
            return
        self.focus_worker.request_stop()
        log.info('Stopping -- waiting for the current exposure to finish...')
        self.window.control_panel.set_stopping()

    def set_method(self, method):
        """
        Update the photometry method used for future steps/reanalysis.
        ``method`` is ``'brightest'`` (the default) or an ``(x, y)`` coordinate
        tuple (from
        :attr:`~nickel_focus.gui.views.image_panel.ImagePanel.sourceSelected`).
        The coordinates actually measured are logged for each exposure.
        """
        self.method = method

    def move_to_target(self, ra_text, dec_text):
        """
        Slew the telescope to the Slew tab's target RA/Dec -- on a background
        thread (:class:`~nickel_focus.gui.model.slew_worker.SlewWorker`), since
        :meth:`~nickel_focus.slew.NickelTelescopePointing.slew_to` can block for
        up to five minutes waiting for the telescope to arrive.

        Parameters
        ----------
        ra_text : :obj:`str`
            Target right ascension, as entered in the Slew tab's target RA field
            (a bare sexagesimal string, e.g.  ``'05:30:00'``).
        dec_text : :obj:`str`
            Target declination, entered the same way (e.g.  ``'+20:15:00'``).
        """
        if self.focus_worker is not None or self.slew_worker is not None:
            return  # hardware exclusivity: something is already running
        if self.telescope is None:
            self._fail('Could not move to target: no ktl connection is available.')
            return
        try:
            target = SkyCoord(ra=ra_text, dec=dec_text, unit=('hourangle', 'deg'))
        except ValueError as e:
            self._fail(f'Could not parse target coordinates: {e}', e)
            return

        self.slew_worker = SlewWorker(self.telescope, target.ra, target.dec)
        self.slew_worker.slewFinished.connect(self._on_slew_finished)
        self.slew_worker.slewFailed.connect(self._fail)
        self.slew_worker.finished.connect(self._on_slew_worker_finished)
        self._set_running(True)
        self.slew_worker.start()

    def find_nearest_object(self, file_text, search_text):
        """
        Handle the Slew tab's "Find nearest object" button: search ``file_text``
        for the target nearest the telescope's current position, restricted to
        names containing ``search_text`` (unrestricted if empty).

        Parameters
        ----------
        file_text : :obj:`str`
            Path to a starlist file entered in the Slew tab's file field.
            Unlike
            :meth:`~nickel_focus.gui.controller.Controller.find_nearest_pointing`/
            :meth:`~nickel_focus.gui.controller.Controller.find_nearest_focus`,
            there is no default here -- use one of those two for the packaged
            catalog.
        search_text : :obj:`str`
            Object-name search string entered in the Slew tab's search field, or
            an empty string to consider every target in the file.
        """
        if file_text == '':
            self._fail('A starlist file must be provided to find the nearest object.')
            return
        search = None if search_text == '' else search_text
        self._find_nearest(search, file=file_text)

    def find_nearest_pointing(self):
        """
        Handle the Slew tab's "Find nearest pointing star" button: search the
        packaged default catalog for the nearest target whose name contains
        ``'Pointing'``, ignoring whatever is currently in the Slew tab's
        file/search fields (matching ``slew_to_nearest.py -s Pointing`` with no
        ``-f``).
        """
        self._find_nearest('Pointing')

    def find_nearest_focus(self):
        """
        Handle the Slew tab's "Find nearest focus star" button: like
        :meth:`~nickel_focus.gui.controller.Controller.find_nearest_pointing`,
        but for names containing ``'Focusing'`` (matching ``slew_to_nearest.py
        -s Focusing`` with no ``-f``).
        """
        self._find_nearest('Focusing')

    def quit(self):
        """
        Handle the status bar's Quit button: close the window, exactly as its
        own close button would (the application exits once its last window
        closes).
        """
        self.window.close()

    # -- internals ------------------------------------------------------------

    def _fail(self, message, exc=None):
        """
        Log ``message`` as an error (which the Log tab shows, like the
        console).  If ``exc`` (the :obj:`Exception` that caused the failure) is
        provided, its full traceback is logged along with ``message``.
        """
        log.error(message, exc_info=exc)

    def _name_object(self, focus_sequence):
        """
        Set ``focus_sequence``'s
        :attr:`~nickel_focus.focus.FocusSequence.object_name` -- recorded with
        each exposure, with the focus, as the camera's ``OBJECT`` -- to the
        Slew tab's target name, if the telescope is pointed at that target.

        The name is used only if the Slew tab's target coordinates are within
        :attr:`MAX_TARGET_OFFSET_ARCMIN` of the telescope's current position
        (its latest ``POCORAA``/``POCODECA`` broadcast).  Otherwise -- or if
        the two can't be compared -- the object stays ``'unknown'`` and a
        warning is logged; the Slew tab's name is left as it is.  With no
        target name, the object is ``'unknown'``.
        """
        name = self.window.control_panel.get_target_name()
        if name is None:
            return
        offset = self._target_offset()
        if offset is None:
            log.warning(
                f"Could not compare the Slew tab's target ({name}) with the telescope's "
                "current position; recording the object as 'unknown'."
            )
            return
        if offset > self.MAX_TARGET_OFFSET_ARCMIN:
            log.warning(
                f"The telescope is {offset:.1f} arcmin from the Slew tab's target ({name}), more "
                f"than {self.MAX_TARGET_OFFSET_ARCMIN:g} arcmin; recording the object as "
                "'unknown'."
            )
            return
        focus_sequence.object_name = name

    def _target_offset(self):
        """
        The separation, in arcminutes, between the Slew tab's target
        coordinates and the telescope's current position, or None if either
        is missing or can't be parsed.
        """
        ra_text, dec_text = self.window.control_panel.get_target_coordinates()
        current = self._current_pointing()
        if current is None:
            return None
        try:
            target = SkyCoord(ra=ra_text, dec=dec_text, unit=('hourangle', 'deg'))
        except ValueError:
            return None
        return target.separation(current).arcmin

    def _current_pointing(self):
        """
        The telescope's current position from the latest ``POCORAA``/
        ``POCODECA`` broadcasts, or None if there are none or they can't be
        parsed.
        """
        ra = self._latest.get(('nickelpoco', 'POCORAA'))
        dec = self._latest.get(('nickelpoco', 'POCODECA'))
        if ra is None or dec is None:
            return None
        try:
            return SkyCoord(ra=ra, dec=dec, unit=('hourangle', 'deg'))
        except ValueError:
            return None

    def _find_nearest(self, obj_search_str, file=None):
        """
        Shared implementation for
        :meth:`~nickel_focus.gui.controller.Controller.find_nearest_object`/
        :meth:`~nickel_focus.gui.controller.Controller.find_nearest_pointing`/
        :meth:`~nickel_focus.gui.controller.Controller.find_nearest_focus`:
        locate the nearest target and populate/report it on the Slew tab, or
        report a clear failure.

        Parameters
        ----------
        obj_search_str
            Forwarded to :func:`~nickel_focus.slew.find_nearest_target`.
        file
            Forwarded to :func:`~nickel_focus.slew.find_nearest_target`;
            ``None`` (the default) searches the packaged default catalog.
        """
        if self.telescope is None:
            self._fail('Could not find nearest target: no ktl connection is available.')
            return
        try:
            name, ra, dec = slew.find_nearest_target(
                self.telescope.current, obj_search_str=obj_search_str, file=file
            )
        except Exception as e:
            # Not just bad starlists (ValueError/FileNotFoundError): reading
            # the telescope's current position can also fail.
            self._fail(f'Could not find nearest target: {e}', e)
            return
        ra_text = ra.to_string(unit='hourangle', sep=':', pad=True, precision=2)
        dec_text = dec.to_string(unit='deg', sep=':', pad=True, alwayssign=True, precision=2)
        text = f'Nearest target: {name} (RA={ra_text}, Dec={dec_text})'
        log.info(text)
        self.window.control_panel.show_nearest_target(ra_text, dec_text, name)

    def _start_focus_worker(self, focus_sequence, mode, exp_kwargs=None, focus_value=None):
        """
        Create a :class:`~nickel_focus.gui.model.focus_worker.FocusWorker` for
        ``focus_sequence``, connect its signals to this Controller's ``_on_*``
        handlers, and start it running on its own background thread (see
        :meth:`~nickel_focus.gui.model.focus_worker.FocusWorker.run`).
        """
        self.focus_worker = FocusWorker(
            focus_sequence, method=self.method, mode=mode, exp_kwargs=exp_kwargs,
            focus_value=focus_value
        )
        self.focus_worker.stepComplete.connect(self._on_step_complete)
        self.focus_worker.focusSequenceFinished.connect(self._on_focus_sequence_finished)
        self.focus_worker.focusSequenceFailed.connect(self._fail)
        self.focus_worker.singleExposureFinished.connect(self._on_single_exposure_finished)
        self.focus_worker.finished.connect(self._on_focus_worker_finished)
        self._set_running(True)
        self.focus_worker.start()

    def _on_step_complete(self, result):
        """
        Handle one :class:`~nickel_focus.focus.StepResult` from the worker's
        :attr:`~nickel_focus.gui.model.focus_worker.FocusWorker.stepComplete`
        signal: update the image/curve panels and log the step.
        """
        is_reanalyze = self.focus_worker is not None and self.focus_worker.mode == 'reanalyze'
        reanalyzing_standalone = (
            is_reanalyze and self._standalone_focus_sequence is not None
            and self.focus_worker.focus_sequence is self._standalone_focus_sequence
        )
        if is_reanalyze:
            self.window.image_panel.update_result(result)
            if not reanalyzing_standalone:
                # The curve was already cleared in reanalyze(), so each
                # re-measured result is simply added back in as it
                # arrives, rather than replacing an old point in place --
                # that would otherwise show a confusing mix of old and
                # new measurements while reanalysis is still in progress.
                # A standalone exposure never appears on the curve panel
                # at all, so there's nothing to add for it.
                self.window.curve_panel.add_result(result)
        else:
            self.window.image_panel.add_result(result)
            self.window.curve_panel.add_result(result)

        total = self.focus_sequence.expected_steps if self.focus_sequence is not None else None
        step_text = f'Step {result.index + 1}'
        if total:
            step_text += f'/{total}'
        step_text += (
            f' — Focus {result.focus_value:.0f}, FWHM {result.fwhm:.2f}, '
            f'Source ({result.centroid[0]:.1f}, {result.centroid[1]:.1f})'
        )
        if result.is_outlier:
            step_text += '  [outlier]'
        log.info(step_text)

    def _on_focus_sequence_finished(self, best_focus, best_fwhm):
        """
        Handle
        :attr:`~nickel_focus.gui.model.focus_worker.FocusWorker.focusSequenceFinished`:
        report the fitted result.
        """
        text = f'Sequence finished: best focus {best_focus:.1f}, expected FWHM {best_fwhm:.2f}'
        log.info(text)
        self.window.control_panel.show_best_focus(best_focus)

    def _on_single_exposure_finished(self, result):
        """
        Handle
        :attr:`~nickel_focus.gui.model.focus_worker.FocusWorker.singleExposureFinished`:
        display the exposure, seed
        :attr:`~nickel_focus.gui.controller.Controller._standalone_focus_sequence`'s
        bookkeeping with it (so
        :meth:`~nickel_focus.gui.controller.Controller.reanalyze` has something
        to work with), and report it.
        """
        self.window.image_panel.add_result(result)
        seq = self._standalone_focus_sequence
        seq.observed_focus.append(result.focus_value)
        seq.exposures.append(result.exposure)
        seq.img_quality.append(result.fwhm)
        seq.source_stamps.append(result.stamp)
        seq.centroids.append(result.centroid)
        seq.step_iter = 1
        text = (
            f'Took exposure at focus {result.focus_value:.0f}: measured FWHM {result.fwhm:.2f}, '
            f'source ({result.centroid[0]:.1f}, {result.centroid[1]:.1f})'
        )
        log.info(text)

    def _on_focus_worker_finished(self):
        """
        Handle :attr:`~nickel_focus.gui.model.focus_worker.FocusWorker.finished`
        (a signal every :class:`PySide6.QtCore.QThread` provides automatically,
        emitted once
        :meth:`~nickel_focus.gui.model.focus_worker.FocusWorker.run` returns):
        release the worker and restore hardware exclusivity.
        """
        if self.focus_worker is not None:
            # QThread.wait() blocks (briefly) until the OS thread has
            # actually terminated. `finished` already means `run()` has
            # returned, so this is just tidying up bookkeeping, not
            # waiting for real work -- but it's the documented, correct
            # way to confirm a QThread is truly done before discarding
            # it.
            self.focus_worker.wait()
        self.focus_worker = None
        self._set_running(False)

    def _on_source_selected(self, x, y):
        """
        Handle
        :attr:`~nickel_focus.gui.views.image_panel.ImagePanel.sourceSelected`:
        mark that source, then reanalyze against it.
        """
        self.set_method((x, y))
        self.reanalyze()

    def _on_slew_finished(self):
        """
        Handle
        :attr:`~nickel_focus.gui.model.slew_worker.SlewWorker.slewFinished`:
        report that the telescope reached its target.
        """
        text = 'Move to target complete.'
        log.info(text)

    def _on_slew_worker_finished(self):
        """
        Handle :attr:`~nickel_focus.gui.model.slew_worker.SlewWorker.finished`
        (a signal every :class:`PySide6.QtCore.QThread` provides automatically,
        emitted once :meth:`~nickel_focus.gui.model.slew_worker.SlewWorker.run`
        returns): release the worker and restore hardware exclusivity.  See
        :meth:`~nickel_focus.gui.controller.Controller._on_focus_worker_finished`
        for why :meth:`PySide6.QtCore.QThread.wait` is called here.
        """
        if self.slew_worker is not None:
            self.slew_worker.wait()
        self.slew_worker = None
        self._set_running(False)

    def _connect_service(self, service_class):
        """
        Construct one of the shared KTL services
        (:attr:`~nickel_focus.gui.controller.Controller.poco`/
        :attr:`~nickel_focus.gui.controller.Controller.cam`), or return
        ``None`` if that's not possible.

        Without ``ktl``, this returns ``None`` silently.  Any other failure
        (e.g., ``ktl`` is available but the service doesn't answer) is logged
        as a warning rather than preventing the GUI from starting; the
        hardware tabs then report the missing connection when used.

        Parameters
        ----------
        service_class : :obj:`type`
            :class:`~nickel_focus.services.nickelpoco.NickelPoco` or
            :class:`~nickel_focus.services.nscicam.NSciCam`.
        """
        if not ktl_available():
            return None
        try:
            return service_class()
        except Exception as e:
            log.warning(
                f'Could not connect to the {service_class.service} service: {e}', exc_info=e
            )
            return None

    def _on_exposure_keyword(self, key, value):
        """
        Show a camera exposure setting (from its keyword's broadcast, or the
        value delivered when subscribing) on the Single/Grid/Auto tabs.

        Only a value that differs from what the fields already show is
        applied and logged -- as the camera's initial setting the first time,
        and as a change made elsewhere (e.g. another GUI, or the camera
        adjusting a requested exposure time) after that.  The broadcast that
        confirms the user's own edit therefore isn't logged.

        Parameters
        ----------
        key : :obj:`str`
            ``'exptime'``, ``'speed'``, or ``'binning'``.
        value : :obj:`str`
            The keyword's ascii value.
        """
        try:
            setting = ExposureConfig.setting_value(key, value)
        except ValueError as e:
            log.warning(f'Could not read the camera exposure {key} ({value!r}): {e}', exc_info=e)
            return
        self._camera_exposure[key] = setting
        previous = self._shown_exposure.get(key)
        if setting == previous:
            return
        self._show_exposure_settings({key: setting})
        if previous is None:
            log.info(f'Exposure {key} from the camera: {setting}')
        else:
            log.info(f'Exposure {key} changed on the camera: {previous} -> {setting}')

    def _on_exposure_setting_edited(self, key, value):
        """
        Slot for
        :attr:`~nickel_focus.gui.views.focus_control_panel.FocusControlPanel.exposureSettingEdited`:
        show the edited setting on all three exposure tabs, then write it to
        the camera (see
        :meth:`~nickel_focus.services.nscicam.ExposureConfig.configure`).

        The write runs on the GUI thread and waits for the camera to finish
        it, so a slow camera holds up the GUI meanwhile.  The camera's
        broadcast of the new value then confirms it.  A rejected write is
        reported as a failure, and the fields go back to the camera's
        setting.  Without the camera service, the edit only syncs the tabs.
        """
        self._show_exposure_settings({key: value})
        if self.cam is None:
            return
        try:
            self.cam.cfg.configure(**{key: value})
        except Exception as e:
            self._fail(f'Could not set the exposure {key} to {value}: {e}', e)
            if key in self._camera_exposure:
                self._show_exposure_settings({key: self._camera_exposure[key]})
            return
        log.info(f'Exposure {key} set to {value}')

    def _show_exposure_settings(self, config):
        """
        Show ``config`` (exposure ``key`` -> value) on the Single/Grid/Auto
        tabs and remember it as shown.  A speed or binning that isn't one of
        the options is added as a new button, with a warning.
        """
        added = self.window.control_panel.set_exposure_config(config)
        self._shown_exposure.update(config)
        for key in added:
            log.warning(
                f'Exposure {key} {config[key]!r} is not one of the GUI options; added it as a '
                f'{key} option.'
            )

    def _subscribe(self, service, names):
        """
        Subscribe :attr:`_ktl_bridge` to ``names`` in ``service`` (see
        :meth:`~nickel_focus.services.base.KTLService.subscribe`), unless
        ``service`` is ``None``.  A failure is logged as a warning; the
        affected indicators then stay red.
        """
        if service is None:
            return
        try:
            service.subscribe(names, self._ktl_bridge.relay)
        except Exception as e:
            log.warning(
                f'Could not subscribe to the {service.service} status keywords: {e}', exc_info=e
            )

    def _on_keyword_changed(self, service, keyword, value):
        """
        Slot for :attr:`~nickel_focus.gui.ktl_bridge.KTLBridge.keywordChanged`
        (on the GUI thread): record ``value`` and update whatever shows it --
        a status indicator, (rate-limited) the Slew tab's current position, or
        the exposure fields.
        """
        self._latest[service, keyword] = value
        if (service, keyword) in _EXPOSURE_KEYWORDS:
            self._on_exposure_keyword(_EXPOSURE_KEYWORDS[service, keyword], value)
            return
        if (service, keyword) in _POSITION_KEYWORDS:
            if not self._position_timer.isActive():
                self._position_timer.start()
            return
        for key in _INDICATOR_KEYWORDS.get((service, keyword), ()):
            self._show_indicator(key)

    def _show_indicator(self, key):
        """
        Set one :class:`~nickel_focus.gui.views.status_bar.StatusBar` indicator
        from the latest value of its keyword (see ``_INDICATORS`` for each
        keyword and its mapping to a color):

        - **Slew** -- ``nickelpoco.POCSTOP`` (telescope movement allowed).
        - **Track** -- ``nickelpoco.POCTRCK``.
        - **On Target** -- ``nickelpoco.POCOT``.
        - **Focus** -- ``nickelpoco.POCSECLK`` (the secondary's lock) sets the
          color; the text is the current focus value, ``nickelpoco.POCSECPA``.
        - **Exp** -- ``nscicam.EXPSTATE``.

        An indicator with no value yet (including when its service isn't
        connected) is red with its default text.
        """
        service, keyword, state_function = _INDICATORS[key]
        value = self._latest.get((service, keyword))
        state = 'error' if value is None else state_function(value)
        text = None
        if key == 'focus':
            text = _focus_text(self._latest.get(('nickelpoco', 'POCSECPA')))
        self.window.status_bar.set_indicator(key, state, text)

    def _show_current_position(self):
        """
        Show the latest ``POCORAA``/``POCODECA`` values on the Slew tab's live
        "current position" display, or "—" if there are none yet (e.g. no
        telescope connection) or they can't be parsed.  A value that can't be
        parsed is logged as a warning, once per run of bad values.
        """
        ra = self._latest.get(('nickelpoco', 'POCORAA'))
        dec = self._latest.get(('nickelpoco', 'POCODECA'))
        if ra is None or dec is None:
            self.window.control_panel.set_current_position('—', '—')
            return
        try:
            ra_text = Angle(ra, unit='hourangle').to_string(
                unit='hourangle', sep=':', pad=True, precision=2
            )
            dec_text = Angle(dec, unit='deg').to_string(
                unit='deg', sep=':', pad=True, alwayssign=True, precision=2
            )
        except ValueError as e:
            if not self._bad_position_logged:
                log.warning(
                    f'Could not read the current telescope position (RA={ra!r}, Dec={dec!r}): '
                    f'{e}', exc_info=e
                )
            self._bad_position_logged = True
            self.window.control_panel.set_current_position('—', '—')
            return
        self._bad_position_logged = False
        self.window.control_panel.set_current_position(ra_text, dec_text)

    def _set_running(self, running):
        """Propagate the hardware-exclusivity state to the View."""
        self.window.control_panel.set_running(running)
        self.window.status_bar.set_running(running)
        self.window.image_panel.set_selection_enabled(not running)


# -- status-indicator state mappings ------------------------------------------
#
# Each function maps one KTL keyword's (ascii) value to a StatusBar indicator
# state: 'ok' (green), 'warn' (yellow), or 'error' (red).
# String values are compared case-insensitively, ignoring surrounding
# whitespace.

def _normalize(value):
    """Return ``value`` as a lowercase string with surrounding whitespace removed."""
    return str(value).strip().lower()


def _movement_state(value):
    """``POCSTOP``: green if movement is ``allowed``, red otherwise."""
    return 'ok' if _normalize(value) == 'allowed' else 'error'


def _track_state(value):
    """``POCTRCK``: green if tracking is ``on``, yellow otherwise."""
    return 'ok' if _normalize(value) == 'on' else 'warn'


def _target_state(value):
    """
    ``POCOT``: green if on target (``0``), yellow if off target (``1``), red for
    an error (``-1``) or any value that isn't one of those three.
    """
    try:
        code = int(_normalize(value))
    except ValueError:
        return 'error'
    return {0: 'ok', 1: 'warn'}.get(code, 'error')


def _seclk_state(value):
    """``POCSECLK``: green if the secondary is locked (``on``), yellow otherwise."""
    return 'ok' if _normalize(value) == 'on' else 'warn'


def _expstate_state(value):
    """``EXPSTATE``: green if the camera is ``Ready``, yellow otherwise."""
    return 'ok' if _normalize(value) == 'ready' else 'warn'


def _focus_text(value):
    """``POCSECPA``: the Focus indicator's text, e.g. ``'345.2'``, or None if unknown."""
    try:
        return f'{float(value):.1f}'
    except (TypeError, ValueError):
        return None


# Each status-bar indicator: (service, keyword, mapping of its value to a state).
_INDICATORS = {
    'movement': ('nickelpoco', 'POCSTOP', _movement_state),
    'track': ('nickelpoco', 'POCTRCK', _track_state),
    'target': ('nickelpoco', 'POCOT', _target_state),
    'focus': ('nickelpoco', 'POCSECLK', _seclk_state),
    'exp': ('nscicam', 'EXPSTATE', _expstate_state),
}

# The indicators each keyword affects; POCSECPA is the Focus indicator's text.
_INDICATOR_KEYWORDS = {
    (service, keyword): (key,) for key, (service, keyword, _) in _INDICATORS.items()
}
_INDICATOR_KEYWORDS['nickelpoco', 'POCSECPA'] = ('focus',)

# The Slew tab's current position.
_POSITION_KEYWORDS = {('nickelpoco', 'POCORAA'), ('nickelpoco', 'POCODECA')}

# The exposure fields: keyword -> ExposureConfig setting key.
_EXPOSURE_KEYWORDS = {
    ('nscicam', keyword): key for key, keyword in ExposureConfig.keywords.items()
}

# Every keyword the Controller subscribes to (all of the above), by service.
_POCO_DISPLAY_KEYWORDS = (
    'POCSTOP', 'POCTRCK', 'POCOT', 'POCSECLK', 'POCSECPA', 'POCORAA', 'POCODECA',
)
_CAM_DISPLAY_KEYWORDS = ('EXPSTATE', 'EXPOSURE', 'AMPCONF', 'BINNING')
