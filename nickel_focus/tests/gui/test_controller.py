"""Tests for :mod:`gui.controller`."""
import logging
from pathlib import Path
import types

from astropy.coordinates import Angle
import numpy as np
import pytest

from nickel_focus import focus
from nickel_focus import log
from nickel_focus.gui.log_handler import QtLogHandler
from nickel_focus.gui.qt import QtCore
from nickel_focus.gui.views.main_window import MainWindow
from nickel_focus.gui.controller import Controller
from nickel_focus.tests.gui.keyboard import type_and_enter, type_text


def _configure_replay_tab(panel, focus_sweep):
    panel.tabs.setCurrentWidget(panel.replay_tab)
    panel.replay_datadir_edit.setText(str(focus_sweep['datadir']))
    panel.replay_prefix_edit.setText(focus_sweep['prefix'])
    panel.replay_suffix_edit.setText(focus_sweep['suffix'])
    panel.replay_obsnum_spin.setValue(focus_sweep['obsnum'])
    panel.replay_start_spin.setValue(int(focus_sweep['focus_values'][0]))
    panel.replay_step_spin.setValue(
        int(focus_sweep['focus_values'][1] - focus_sweep['focus_values'][0]))
    panel.replay_nstep_spin.setValue(len(focus_sweep['focus_values']))


def _wait_for_focus_worker(controller, timeout_ms=5000):
    worker = controller.focus_worker
    if worker is None:
        return
    loop = QtCore.QEventLoop()
    worker.finished.connect(loop.quit)
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


def _wait_for_slew_worker(controller, timeout_ms=5000):
    worker = controller.slew_worker
    if worker is None:
        return
    loop = QtCore.QEventLoop()
    worker.finished.connect(loop.quit)
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


def _deliver_broadcasts(controller):
    """
    Run the event loop long enough for queued keyword values -- and a
    rate-limited position redraw -- to reach the window.
    """
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(controller.POSITION_REDRAW_MS + 50, loop.quit)
    loop.exec()


def _log_text(window):
    """The Log tab's text, once any queued log records have been delivered."""
    QtCore.QCoreApplication.processEvents()
    return window.control_panel.log_widget.toPlainText()


def _make_step_result(index=0, focus_value=350., fwhm=3.2, centroid=(12.3, 45.6),
                       is_outlier=False):
    return focus.StepResult(
        index=index, focus_value=focus_value, exposure=Path('n2000.fits'), fwhm=fwhm,
        frame=np.zeros((4, 4)), centroid=centroid,
        is_outlier=is_outlier)


def _write_starlist(tmp_path, lines):
    """Write a small starlist file for a test to point the Slew tab's file field at."""
    path = tmp_path / 'stars.txt'
    path.write_text('\n'.join(lines) + '\n')
    return path


def _make_controller(focus_sweep):
    window = MainWindow()
    controller = Controller(window)
    _configure_replay_tab(window.control_panel, focus_sweep)
    return window, controller


def test_start_focus_sequence_runs_to_completion_for_archive(qapp, focus_sweep):
    window, controller = _make_controller(focus_sweep)

    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)

    assert controller.focus_worker is None, 'worker should be cleared once finished'
    assert len(window.image_panel._results) == len(focus_sweep['files']), \
        'image panel should have one entry per exposure'
    assert len(window.curve_panel._results) == len(focus_sweep['files']), \
        'curve panel should have one entry per exposure'
    best_focus = round(focus_sweep['best_focus'])
    assert window.control_panel.single_focus_spin.value() == pytest.approx(best_focus, abs=5), \
        'the Single tab should default to the fitted best focus'


def test_archive_sequence_never_uses_hardware(qapp, focus_sweep, fake_hardware):
    # With the (fake) telescope and camera connected, as at the telescope, a
    # Replay run must still never use them.
    window, controller = _make_controller(focus_sweep)

    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)

    assert 'Could not start sequence' not in _log_text(window), \
        'Replay should start even when ktl is available'
    replay = controller.focus_sequence
    assert replay._focus is None and replay._exposure is None, \
        'a Replay sequence should have no hardware handles'
    assert fake_hardware['sim'].ktl.writes == [], 'Replay should never write a keyword'
    assert len(window.curve_panel._results) == len(focus_sweep['files']), \
        'Replay should run to completion'


def test_running_state_is_set_synchronously_before_focus_worker_runs(qapp, focus_sweep):
    window, controller = _make_controller(focus_sweep)

    controller.start_focus_sequence()

    # _set_running(True) happens before worker.start(), so this should
    # already be true without needing to pump the event loop at all.
    assert not window.control_panel.replay_load_button.isEnabled(), \
        'Load should be disabled as soon as a sequence begins'
    assert not window.image_panel._selection_enabled, \
        'image selection should be disabled while a sequence is running'
    assert window.control_panel.tabs.isEnabled(), 'the tab bar should stay reachable while running'

    _wait_for_focus_worker(controller)
    assert window.control_panel.replay_load_button.isEnabled(), 'Load should re-enable once finished'
    assert window.image_panel._selection_enabled, 'selection should re-enable once finished'


def test_hardware_exclusivity_blocks_second_start(qapp, focus_sweep):
    window, controller = _make_controller(focus_sweep)
    controller.focus_worker = types.SimpleNamespace()  # pretend something is already running

    controller.start_focus_sequence()

    assert controller.focus_sequence is None, \
        'a second start should be a no-op while something is already running'


def test_missing_files_reports_failure(qapp, focus_sweep):
    window, controller = _make_controller(focus_sweep)
    window.control_panel.replay_prefix_edit.setText('does-not-exist-')

    controller.start_focus_sequence()

    assert controller.focus_worker is None, 'a config error should never start a worker'
    assert 'Could not start sequence' in _log_text(window), \
        'the failure should be reported to the user'


def test_stop_delegates_to_the_focus_worker(qapp):
    window = MainWindow()
    controller = Controller(window)
    stop_calls = []
    controller.focus_worker = types.SimpleNamespace(request_stop=lambda: stop_calls.append(True))

    controller.stop()

    assert stop_calls == [True], 'stop() should call request_stop() on the active worker'
    assert 'Stopping' in _log_text(window), \
        'stop() should log that it is waiting for the current exposure'


def test_stop_is_a_noop_with_nothing_running(qapp):
    window = MainWindow()
    controller = Controller(window)
    controller.stop()  # should not raise
    assert 'Stopping' not in _log_text(window), \
        'stop() with nothing running should log nothing about stopping'


def test_set_method_updates_state(qapp):
    window = MainWindow()
    controller = Controller(window)

    controller.set_method((123.4, 567.8))

    assert controller.method == (123.4, 567.8), 'set_method should store the new photometry method'


def test_reanalyze_is_a_noop_without_a_focus_sequence(qapp):
    window = MainWindow()
    controller = Controller(window)
    controller.reanalyze()
    assert controller.focus_worker is None, \
        'reanalyze() with no sequence loaded should not start a worker'


def test_source_selection_updates_measurements_in_place(qapp, focus_sweep):
    window, controller = _make_controller(focus_sweep)
    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)

    n_before = len(window.image_panel._results)

    controller._on_source_selected(50., 50.)
    _wait_for_focus_worker(controller)

    assert controller.method == (50., 50.), 'selecting a source should update the active method'
    assert len(window.image_panel._results) == n_before, \
        'reanalysis should update existing entries, not add duplicates'
    assert len(window.curve_panel._results) == n_before, \
        'reanalysis should update existing entries, not add duplicates'


def test_reanalyze_clears_the_curve_panel_immediately_not_iteratively(qapp, focus_sweep):
    window, controller = _make_controller(focus_sweep)
    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)
    assert len(window.curve_panel._results) == len(focus_sweep['files']), \
        'setup: the curve should be populated from the initial sequence'

    controller.reanalyze()

    # The clear must happen synchronously, before the worker thread has
    # produced any results -- not get whittled down to zero one point at
    # a time as each re-measured result streams back in.
    assert window.curve_panel._results == [], \
        'the curve should be cleared immediately when reanalysis starts'

    _wait_for_focus_worker(controller)
    assert len(window.curve_panel._results) == len(focus_sweep['files']), \
        'the curve should be fully repopulated once reanalysis completes'


def test_start_focus_sequence_runs_grid_against_fake_hardware(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.grid_tab)
    panel.grid_start_spin.setValue(340)
    panel.grid_step_spin.setValue(5)
    panel.grid_nstep_spin.setValue(5)
    type_and_enter(panel.grid_exptime_spin, '7.5')

    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)

    assert len(window.image_panel._results) == 5, 'image panel should have one entry per exposure'
    assert panel.single_focus_spin.value() == pytest.approx(fake_hardware['best_focus'], abs=5), \
        'the Single tab should default to the fitted best focus'
    assert fake_hardware['sim'].focus == 360., 'the simulated focus should have been commanded'
    assert fake_hardware['sim'].keyword('nscicam', 'EXPOSURE').value == 7.5, \
        'exposure settings from the control panel should have been applied'
    assert controller.focus_sequence._focus.poco is controller.poco, \
        'the sequence should use the shared telescope service'
    assert controller.focus_sequence._exposure.cam is controller.cam, \
        'the sequence should use the shared camera service'


def test_start_focus_sequence_runs_automated_against_fake_hardware(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.auto_tab)
    panel.auto_start_spin.setValue(340)
    panel.auto_step_spin.setValue(5)
    panel.auto_maxsteps_spin.setValue(12)

    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)

    assert isinstance(controller.focus_sequence, focus.AutomatedFocusSequence), \
        'the Auto tab should start an AutomatedFocusSequence'
    assert panel.single_focus_spin.value() == pytest.approx(fake_hardware['best_focus'], abs=5), \
        'the adaptive search should converge near the known best focus'


def test_start_focus_sequence_without_ktl_reports_clear_failure(qapp):
    # No fake_hardware fixture here: this dev machine has no ktl, so a
    # live sequence type should fail immediately and clearly, without
    # ever starting a worker thread.
    window = MainWindow()
    controller = Controller(window)
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.grid_tab)
    panel.grid_nstep_spin.setValue(5)

    controller.start_focus_sequence()

    assert controller.focus_worker is None, 'no worker should start without a ktl connection'
    assert 'no ktl connection' in _log_text(window).lower(), \
        'the failure should explain that there is no ktl connection'


def test_take_single_exposure_is_a_noop_while_something_is_running(qapp):
    window = MainWindow()
    controller = Controller(window)
    controller.focus_worker = types.SimpleNamespace()  # pretend something is already running

    controller.take_single_exposure(340.)

    assert controller._standalone_focus_sequence is None, \
        'a take-single-exposure request should be ignored while something is running'


def test_take_single_exposure_without_ktl_reports_clear_failure(qapp):
    window = MainWindow()
    controller = Controller(window)

    controller.take_single_exposure(340.)

    assert controller.focus_worker is None, 'no worker should start without a ktl connection'
    assert 'no ktl connection' in _log_text(window).lower(), \
        'the failure should explain that there is no ktl connection'


def test_take_single_exposure_runs_against_fake_hardware(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.single_tab)
    type_and_enter(panel.single_exptime_spin, '6')

    panel.takeSingleExposureRequested.emit(345.)
    _wait_for_focus_worker(controller)

    assert controller.focus_worker is None, 'worker should be cleared once the exposure finishes'
    assert fake_hardware['sim'].focus == 345., 'the simulated focus should have been commanded'
    assert controller.focus_sequence is None, \
        'a standalone single exposure should not create/replace a loaded sequence'
    assert len(window.image_panel._results) == 1, \
        'the exposure should be displayed in the image panel'
    assert window.curve_panel._results == [], \
        'a standalone exposure should not appear on the focus curve'
    assert f'{345:.0f}' in _log_text(window), \
        'the result should be logged, and so shown on the Log tab'


def test_source_selection_reanalyzes_a_standalone_exposure(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    controller.take_single_exposure(345.)
    _wait_for_focus_worker(controller)
    original_exposure = window.image_panel._current.exposure

    controller._on_source_selected(50., 50.)
    _wait_for_focus_worker(controller)

    assert controller.method == (50., 50.), 'selecting a source should update the active method'
    assert len(window.image_panel._results) == 1, \
        'reanalysis should update the exposure in place, not add a duplicate'
    assert window.image_panel._current.exposure == original_exposure, \
        'still the same exposure, just reanalyzed'
    assert window.curve_panel._results == [], \
        'a standalone reanalyzed exposure still should not appear on the focus curve'


def test_start_focus_sequence_discards_standalone_focus_sequence_state(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    controller.take_single_exposure(345.)
    _wait_for_focus_worker(controller)
    assert controller._standalone_focus_sequence is not None, \
        'setup: a standalone exposure should exist'

    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.grid_tab)
    panel.grid_start_spin.setValue(340)
    panel.grid_step_spin.setValue(5)
    panel.grid_nstep_spin.setValue(5)
    controller.start_focus_sequence()

    assert controller._standalone_focus_sequence is None, \
        'starting a new sequence should discard standalone single-exposure state'
    _wait_for_focus_worker(controller)


def test_controller_without_ktl_shows_placeholder_current_position(qapp):
    # No fake_telescope fixture here: this dev machine has no ktl, so
    # `telescope` should end up None and the live display should show a
    # clear placeholder rather than stale/bogus coordinates.
    window = MainWindow()
    controller = Controller(window)

    assert controller.telescope is None, 'setup: no ktl connection is available'
    assert window.control_panel.slew_current_ra_label.text() == '—', \
        'with no telescope, the current RA should show a placeholder'
    assert window.control_panel.slew_current_dec_label.text() == '—', \
        'with no telescope, the current Dec should show a placeholder'


def test_hardware_tabs_disabled_without_ktl(qapp):
    # No fake_hardware fixture here: this dev machine has no ktl, so the
    # four tabs that need it should come up grayed out, with Replay (the
    # only one that works with no ktl connection) shown initially.
    window = MainWindow()
    Controller(window)
    panel = window.control_panel

    for tab in (panel.slew_tab, panel.single_tab, panel.grid_tab, panel.auto_tab):
        assert not panel.tabs.isTabEnabled(panel.tabs.indexOf(tab)), \
            f'{tab} should be disabled without a ktl connection'
    assert panel.tabs.currentWidget() is panel.replay_tab, \
        'Replay should be the tab shown when the window opens with no ktl connection'


def test_hardware_tabs_enabled_with_fake_hardware(qapp, fake_hardware):
    window = MainWindow()
    Controller(window)
    panel = window.control_panel

    for i in range(panel.tabs.count()):
        assert panel.tabs.isTabEnabled(i), \
            f'tab {panel.tabs.tabText(i)!r} should be enabled once ktl is available'


def test_force_enable_hardware_tabs_overrides_no_ktl(qapp):
    # The GUI script's suppressed --test flag: a developer with no ktl
    # connection can still see/click through the hardware tabs, even
    # though every action on them still fails (see the "no ktl
    # connection" tests above/below, none of which pass this flag).
    window = MainWindow()
    controller = Controller(window, force_enable_hardware_tabs=True)
    panel = window.control_panel

    assert controller.ktl_available is False, 'setup: no ktl connection is available'
    for i in range(panel.tabs.count()):
        assert panel.tabs.isTabEnabled(i), \
            f'tab {panel.tabs.tabText(i)!r} should stay enabled with force_enable_hardware_tabs'


def test_current_position_follows_the_telescope(qapp, fake_telescope):
    fake_telescope.ra, fake_telescope.dec = 5.5, 30.25
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)
    panel = window.control_panel

    assert panel.slew_current_ra_label.text() == '05:30:00.00', \
        'the current-position display should start at the telescope RA'
    assert panel.slew_current_dec_label.text() == '+30:15:00.00', \
        'the current-position display should start at the telescope Dec'

    fake_telescope.ra, fake_telescope.dec = 6.25, -10.5  # e.g. moved by another GUI
    _deliver_broadcasts(controller)

    assert panel.slew_current_ra_label.text() == '06:15:00.00', \
        'the display should follow a broadcast RA change'
    assert panel.slew_current_dec_label.text() == '-10:30:00.00', \
        'the display should follow a broadcast Dec change'


def test_current_position_redraws_are_rate_limited(qapp, fake_telescope, monkeypatch):
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)
    redraws = []
    monkeypatch.setattr(window.control_panel, 'set_current_position',
                        lambda ra, dec: redraws.append((ra, dec)))

    for hours in (1.0, 1.1, 1.2, 1.3, 1.4):
        fake_telescope.ra = hours
    _deliver_broadcasts(controller)

    assert len(redraws) == 1, 'a burst of position broadcasts should be redrawn once'
    assert redraws[0][0] == '01:24:00.00', 'the redraw should show the latest position'


def test_unparseable_position_is_logged_once(qapp, caplog, fake_telescope):
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)
    raa = fake_telescope.keyword('nickelpoco', 'POCORAA')

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        raa.value = 'garbage'
        _deliver_broadcasts(controller)
        raa.value = 'more garbage'
        _deliver_broadcasts(controller)

    warnings = [r for r in caplog.records if 'current telescope position' in r.getMessage()]
    assert len(warnings) == 1, 'a run of bad position values should be logged only once'
    assert warnings[0].exc_info is not None, 'the traceback should be logged'
    assert window.control_panel.slew_current_ra_label.text() == '—', \
        'an unparseable position should show the placeholder'


def test_move_to_target_runs_against_fake_telescope(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)

    controller.move_to_target('05:30:00', '+20:15:00')
    _wait_for_slew_worker(controller)

    assert controller.slew_worker is None, 'the slew worker should be cleared once finished'
    assert len(fake_telescope.slew_calls) == 1, \
        'the telescope should have been commanded to slew once'
    commanded_ra, commanded_dec = fake_telescope.slew_calls[0]
    assert commanded_ra == pytest.approx(5.5), \
        'the telescope should be commanded to the entered target RA, in hours'
    assert 'complete' in _log_text(window).lower(), \
        'a successful move should be reported'


def test_move_to_target_reports_telescope_failure(qapp, fake_telescope):
    fake_telescope.tracking_on = False
    window = MainWindow()
    controller = Controller(window)

    controller.move_to_target('05:30:00', '+20:15:00')
    _wait_for_slew_worker(controller)

    assert controller.slew_worker is None, 'the slew worker should be cleared once finished'
    assert 'Tracking is disabled' in _log_text(window), \
        'the telescope failure message should be reported'


def test_move_to_target_reports_a_parse_failure_for_bad_coordinates(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)

    controller.move_to_target('not a coordinate', '+20:15:00')

    assert controller.slew_worker is None, 'a bad target should never start a slew worker'
    assert 'Could not parse target coordinates' in _log_text(window), \
        'an unparseable target should be reported'


def test_move_to_target_without_ktl_reports_clear_failure(qapp):
    window = MainWindow()
    controller = Controller(window)

    controller.move_to_target('05:30:00', '+20:15:00')

    assert controller.slew_worker is None, 'no worker should start without a ktl connection'
    assert 'no ktl connection' in _log_text(window).lower(), \
        'the failure should explain that there is no ktl connection'


def test_starting_a_slew_clears_the_marked_star(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)
    controller.set_method((42.0, 17.0))

    controller.move_to_target('05:30:00', '+20:15:00')
    assert controller.method == 'brightest', \
        'starting a slew should return to measuring the brightest star'
    _wait_for_slew_worker(controller)
    assert 'Cleared the marked star' in _log_text(window), 'clearing the mark should be logged'


def test_a_marked_star_persists_without_a_slew(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)
    controller.set_method((42.0, 17.0))

    controller.move_to_target('not a coordinate', '+20:15:00')
    assert controller.method == (42.0, 17.0), \
        'a slew that never starts should leave the marked star in effect'

    controller.focus_worker = types.SimpleNamespace()  # pretend a sequence is running
    controller.move_to_target('05:30:00', '+20:15:00')
    assert controller.method == (42.0, 17.0), \
        'an ignored move request should leave the marked star in effect'


def test_slewing_without_a_marked_star_logs_nothing_about_it(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)

    controller.move_to_target('05:30:00', '+20:15:00')
    _wait_for_slew_worker(controller)
    assert 'marked star' not in _log_text(window), \
        'with no star marked, a slew should not mention clearing one'


def test_move_to_target_is_a_noop_while_something_is_running(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)
    controller.focus_worker = types.SimpleNamespace()  # pretend a focus sequence is running

    controller.move_to_target('05:30:00', '+20:15:00')

    assert controller.slew_worker is None, \
        'a move request should be ignored while a focus sequence is running'
    assert fake_telescope.slew_calls == [], 'the telescope should never have been commanded'


def test_start_focus_sequence_is_a_noop_while_slewing(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)
    controller.slew_worker = types.SimpleNamespace()  # pretend a slew is running
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.grid_tab)
    panel.grid_nstep_spin.setValue(5)

    controller.start_focus_sequence()

    assert controller.focus_sequence is None, \
        'a focus sequence should be ignored while the telescope is slewing'


def test_find_nearest_pointing_populates_target_fields(qapp, fake_telescope):
    fake_telescope.ra, fake_telescope.dec = 0.5, 29.75  # near the packaged Pointing00 entry
    window = MainWindow()
    controller = Controller(window)

    controller.find_nearest_pointing()

    assert window.control_panel.slew_target_ra_edit.text() != '', \
        'the target RA field should be populated with the found target'
    assert 'Pointing' in _log_text(window), \
        'the found target name should be reported'


def test_find_nearest_focus_populates_target_fields(qapp, fake_telescope):
    fake_telescope.ra, fake_telescope.dec = 0.5, 28.1  # near the packaged Focusing00 entry
    window = MainWindow()
    controller = Controller(window)

    controller.find_nearest_focus()

    assert window.control_panel.slew_target_ra_edit.text() != '', \
        'the target RA field should be populated with the found target'
    assert 'Focusing' in _log_text(window), \
        'the found target name should be reported'


def test_find_nearest_object_requires_a_file(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)

    controller.find_nearest_object('', '')

    assert 'starlist file must be provided' in _log_text(window), \
        'a missing starlist file should be reported, not raised'


def test_find_nearest_object_searches_the_given_file(qapp, fake_telescope, tmp_path):
    path = _write_starlist(tmp_path, [
        'StarA 01:00:00 +10:00:00 2000.0',
        'StarB 13:00:00 -20:00:00 2000.0',
    ])
    fake_telescope.ra, fake_telescope.dec = 1.0, 10.0
    window = MainWindow()
    controller = Controller(window)

    controller.find_nearest_object(str(path), '')

    assert 'StarA' in _log_text(window), \
        'the nearest target in the given file should be found and reported'


def test_find_nearest_object_reports_a_missing_file(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)

    controller.find_nearest_object('/does/not/exist.txt', '')

    assert 'Could not find nearest target' in _log_text(window), \
        'a missing starlist file should be reported'


def test_find_nearest_without_ktl_reports_clear_failure(qapp):
    window = MainWindow()
    controller = Controller(window)

    controller.find_nearest_pointing()

    assert 'no ktl connection' in _log_text(window).lower(), \
        'the failure should explain that there is no ktl connection'


def test_log_records_appear_in_log_tab(qapp):
    window = MainWindow()
    controller = Controller(window)  # noqa: F841 (kept alive; see Controller's own note on this)

    log.info('hello from the logger')
    QtCore.QCoreApplication.processEvents()

    assert 'hello from the logger' in window.control_panel.log_widget.toPlainText(), \
        'a log.info() call should be delivered into the Log tab'


def test_log_tab_follows_the_console_level(qapp):
    log.sh.setLevel(logging.DEBUG)  # as with --verbosity 2; restored after the test
    window = MainWindow()
    controller = Controller(window)  # noqa: F841 (kept alive)

    log.debug('a debug message')
    QtCore.QCoreApplication.processEvents()

    assert 'a debug message' in window.control_panel.log_widget.toPlainText(), \
        'with the console at debug level, the Log tab should show debug messages too'


def test_log_tab_hides_what_the_console_hides(qapp):
    window = MainWindow()
    controller = Controller(window)  # noqa: F841 (kept alive)

    log.debug('a hidden debug message')
    QtCore.QCoreApplication.processEvents()

    assert 'a hidden debug message' not in window.control_panel.log_widget.toPlainText(), \
        'with the console at its default (info) level, debug messages should not appear'


def test_repeated_controller_construction_does_not_leak_handlers(qapp):
    first_window = MainWindow()
    first_controller = Controller(first_window)  # noqa: F841 (kept alive)
    second_window = MainWindow()
    second_controller = Controller(second_window)  # noqa: F841 (kept alive)

    qt_handlers = [h for h in log.handlers if isinstance(h, QtLogHandler)]
    assert len(qt_handlers) == 1, \
        'constructing a new Controller must remove any handler a previous one added'


def test_fail_logs_error_and_shows_failure(qapp, caplog):
    # _fail() is the single consolidated handler for FocusWorker.focusSequenceFailed,
    # SlewWorker.slewFailed, and every other failure path in this class.
    window = MainWindow()
    controller = Controller(window)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller._fail('boom')

    assert any(r.levelname == 'ERROR' and 'boom' in r.getMessage() for r in caplog.records), \
        'a failure should be logged as an error'
    assert 'boom' in _log_text(window), 'the failure should appear on the Log tab'


def test_slew_finished_logs_info(qapp, caplog):
    window = MainWindow()
    controller = Controller(window)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller._on_slew_finished()

    assert any(
        r.levelname == 'INFO' and 'complete' in r.getMessage() for r in caplog.records
    ), 'a completed slew should be logged at info level'


def test_focus_sequence_finished_logs_info(qapp, caplog):
    window = MainWindow()
    controller = Controller(window)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller._on_focus_sequence_finished(356.6, 3.3)

    messages = [
        r.getMessage() for r in caplog.records
        if r.levelname == 'INFO' and r.getMessage().startswith('Sequence finished')
    ]
    assert len(messages) == 1, 'a finished sequence should be logged once at info level'
    text = messages[0]
    assert 'best focus 356.6' in text, 'result text should show the best focus'
    assert 'expected FWHM 3.30' in text, 'result text should show the expected FWHM'
    assert text in _log_text(window), 'the logged text should appear on the Log tab'


def test_single_exposure_finished_logs_info(qapp, caplog):
    window = MainWindow()
    controller = Controller(window)
    controller._standalone_focus_sequence = types.SimpleNamespace(
        observed_focus=[], exposures=[], img_quality=[], binnings=[],
        centroids=[], step_iter=0)
    result = _make_step_result(focus_value=356.)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller._on_single_exposure_finished(result)

    messages = [
        r.getMessage() for r in caplog.records
        if r.levelname == 'INFO' and r.getMessage().startswith('Took exposure')
    ]
    assert len(messages) == 1, 'a completed single exposure should be logged once at info level'
    text = messages[0]
    assert 'focus 356' in text, 'result text should show the focus value'
    assert 'FWHM 3.20' in text, 'result text should show the measured FWHM'
    assert 'source (12.3, 45.6)' in text, 'result text should show the measured source coordinates'
    assert text in _log_text(window), 'the logged text should appear on the Log tab'


def test_find_nearest_target_logs_info(qapp, caplog, fake_telescope):
    fake_telescope.ra, fake_telescope.dec = 0.5, 29.75  # near the packaged Pointing00 entry
    window = MainWindow()
    controller = Controller(window)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller.find_nearest_pointing()

    messages = [
        r.getMessage() for r in caplog.records
        if r.levelname == 'INFO' and r.getMessage().startswith('Nearest target')
    ]
    assert len(messages) == 1, 'a found target should be logged once at info level'
    assert 'Pointing' in messages[0], 'the logged text should name the found target'
    assert messages[0] in _log_text(window), 'the logged text should appear on the Log tab'


def test_step_complete_logs_info(qapp, caplog):
    window = MainWindow()
    controller = Controller(window)
    result = _make_step_result(index=1, focus_value=356., is_outlier=True)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller._on_step_complete(result)

    messages = [r.getMessage() for r in caplog.records if r.levelname == 'INFO']
    step_messages = [m for m in messages if m.startswith('Step 2')]
    assert len(step_messages) == 1, 'each completed step should be logged once at info level'
    text = step_messages[0]
    assert 'Focus 356' in text, 'step text should show the focus value'
    assert 'FWHM 3.20' in text, 'step text should show the measured FWHM'
    assert 'Source (12.3, 45.6)' in text, 'step text should show the measured source coordinates'
    assert '[outlier]' in text, 'step text should flag outliers'
    assert text in _log_text(window), 'the logged step should appear on the Log tab'


def test_log_tab_reflects_worker_signal_end_to_end(qapp):
    window = MainWindow()
    controller = Controller(window)  # noqa: F841 (kept alive)

    controller._fail('total failure')
    QtCore.QCoreApplication.processEvents()

    assert 'total failure' in window.control_panel.log_widget.toPlainText(), \
        'a worker failure should reach the Log tab via Controller -> log.error() -> QtLogHandler'


# -- status indicators and Quit -----------------------------------------------

def _indicator_states(window):
    return {key: label.property('state') for key, label in window.status_bar.indicators.items()}


def test_status_indicators_are_red_without_ktl(qapp):
    window = MainWindow()
    controller = Controller(window)

    assert controller.poco is None and controller.cam is None, \
        'without ktl, neither service should be connected'
    assert set(_indicator_states(window).values()) == {'error'}, \
        'every indicator should be red without a ktl connection'
    assert window.status_bar.indicators['focus'].text() == 'Focus', \
        'with no focus value to show, the Focus indicator keeps its default text'


@pytest.mark.parametrize('function, value, expected', [
    ('_movement_state', 'allowed', 'ok'),
    ('_movement_state', 'disabled', 'error'),
    ('_track_state', 'on', 'ok'),
    ('_track_state', 'off', 'warn'),
    ('_target_state', '0', 'ok'),
    ('_target_state', '1', 'warn'),
    ('_target_state', '-1', 'error'),
    ('_target_state', 'garbage', 'error'),
    ('_target_state', 0, 'ok'),
    ('_seclk_state', 'on', 'ok'),
    ('_seclk_state', 'off', 'warn'),
    ('_expstate_state', 'Ready', 'ok'),
    ('_expstate_state', ' ready ', 'ok'),
    ('_expstate_state', 'Start', 'warn'),
])
def test_status_state_mappings(function, value, expected):
    from nickel_focus.gui import controller as controller_module
    assert getattr(controller_module, function)(value) == expected, \
        f'{function}({value!r}) should map to {expected!r}'


def test_status_indicators_start_at_the_keyword_values(qapp, fake_hardware):
    sim = fake_hardware['sim']
    sim.tracking_on = False
    sim.ready = False
    sim.keyword('nickelpoco', 'POCSECPA').value = 345.24
    sim.keyword('nscicam', 'EXPSTATE').value = 'Exposing'
    window = MainWindow()
    controller = Controller(window)

    _deliver_broadcasts(controller)

    assert _indicator_states(window) == {
        'movement': 'ok', 'track': 'warn', 'target': 'warn', 'focus': 'ok', 'exp': 'warn',
    }, 'each indicator should start at its keyword value'
    assert window.status_bar.indicators['focus'].text() == '345.2', \
        'the Focus indicator should show the current focus value'


def test_status_indicators_follow_broadcasts(qapp, fake_hardware):
    # E.g. another GUI disables movement, starts an exposure, or moves the
    # focus: the indicators follow without the focus GUI doing anything.
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)
    assert _indicator_states(window)['movement'] == 'ok', 'setup: movement starts allowed'
    sim = fake_hardware['sim']

    sim.movement_allowed = False
    sim.keyword('nscicam', 'EXPSTATE').value = 'Readout'
    sim.keyword('nickelpoco', 'POCSECPA').value = 352.06
    _deliver_broadcasts(controller)

    states = _indicator_states(window)
    assert states['movement'] == 'error', 'disabled movement should turn the Slew indicator red'
    assert states['exp'] == 'warn', 'an exposure in progress should turn the Exp indicator yellow'
    assert window.status_bar.indicators['focus'].text() == '352.1', \
        'the Focus indicator should follow the focus value'


def test_failed_subscription_is_logged_and_left_red(qapp, caplog, fake_hardware, monkeypatch):
    sim = fake_hardware['sim']

    def refuse(function, remove=False, preferred=False):
        raise sim.ktl.ktlError('KTL::-1::no callbacks today')

    monkeypatch.setattr(sim.keyword('nscicam', 'EXPSTATE'), 'callback', refuse)
    window = MainWindow()

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller = Controller(window)
        _deliver_broadcasts(controller)

    assert any(
        r.levelname == 'WARNING' and 'Could not subscribe to the nscicam' in r.getMessage()
        for r in caplog.records
    ), 'the failed subscription should be logged as a warning'
    assert _indicator_states(window)['exp'] == 'error', \
        'an indicator with no subscription should stay red'
    assert _indicator_states(window)['movement'] == 'ok', \
        'the other service should be unaffected'


def test_quit_closes_the_window(qapp):
    window = MainWindow()
    controller = Controller(window)  # noqa: F841 (kept alive, so its connections stay live)
    window.show()

    window.status_bar.quit_button.click()

    assert not window.isVisible(), 'Quit should close the window'


def test_quit_is_disabled_while_a_sequence_runs(qapp, focus_sweep):
    window = MainWindow()
    controller = Controller(window)
    _configure_replay_tab(window.control_panel, focus_sweep)
    assert window.status_bar.quit_button.isEnabled(), 'setup: Quit starts enabled'

    controller.start_focus_sequence()
    assert not window.status_bar.quit_button.isEnabled(), \
        'Quit should be disabled as soon as a sequence starts'

    _wait_for_focus_worker(controller)
    assert window.status_bar.quit_button.isEnabled(), \
        'Quit should be re-enabled once the sequence finishes'


def test_quit_is_disabled_while_a_slew_runs(qapp, fake_telescope):
    window = MainWindow()
    controller = Controller(window)

    controller.move_to_target('05:30:00', '+20:15:00')
    assert not window.status_bar.quit_button.isEnabled(), \
        'Quit should be disabled as soon as a slew starts'

    _wait_for_slew_worker(controller)
    assert window.status_bar.quit_button.isEnabled(), \
        'Quit should be re-enabled once the slew finishes'


# -- exposure fields follow the camera, both ways -----------------------------

def _set_camera_settings(fake_hardware, exptime, ampconf, binning):
    sim = fake_hardware['sim']
    sim.keyword('nscicam', 'EXPOSURE').value = exptime
    sim.keyword('nscicam', 'AMPCONF').value = ampconf
    sim.keyword('nscicam', 'BINNING').value = binning


def _exposure_configs(panel):
    """The exposure configuration shown on each of Single, Grid, and Auto."""
    configs = []
    for tab in (panel.single_tab, panel.grid_tab, panel.auto_tab):
        panel.tabs.setCurrentWidget(tab)
        configs.append(panel.get_exposure_config())
    return configs


def _messages(caplog, level='INFO'):
    return [r.getMessage() for r in caplog.records if r.levelname == level]


def test_exposure_fields_keep_their_defaults_without_ktl(qapp):
    reference_window = MainWindow()  # kept alive so its widgets aren't deleted
    defaults = reference_window.control_panel
    defaults.tabs.setCurrentWidget(defaults.single_tab)
    window = MainWindow()
    Controller(window)

    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.single_tab)
    assert panel.get_exposure_config() == defaults.get_exposure_config(), \
        'without ktl, the exposure fields should keep their defaults'


def test_exposure_fields_start_at_the_camera_settings(qapp, caplog, fake_hardware):
    _set_camera_settings(fake_hardware, '30.000', '1.0MHz', '1,1')
    window = MainWindow()

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller = Controller(window)
        _deliver_broadcasts(controller)

    expected = {'exptime': 30.0, 'speed': 'Fast', 'binning': '1,1'}
    assert _exposure_configs(window.control_panel) == [expected] * 3, \
        'every exposure tab should show the camera settings'
    assert 'Exposure exptime from the camera: 30.0' in _messages(caplog), \
        "the camera's initial exposure time should be logged"


def test_exposure_fields_follow_camera_changes(qapp, caplog, fake_hardware):
    # E.g. the exposure time changed from the data-acquisition GUI.
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        fake_hardware['sim'].keyword('nscicam', 'EXPOSURE').value = '45.000'
        _deliver_broadcasts(controller)

    assert [c['exptime'] for c in _exposure_configs(window.control_panel)] == [45.0] * 3, \
        'every exposure tab should follow the camera'
    assert 'Exposure exptime changed on the camera: 5.0 -> 45.0' in _messages(caplog), \
        'a change made elsewhere should be logged'


def test_entered_exposure_time_is_written_to_the_camera(qapp, caplog, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.grid_tab)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        type_and_enter(panel.grid_exptime_spin, '12')
        _deliver_broadcasts(controller)

    assert fake_hardware['sim'].keyword('nscicam', 'EXPOSURE').writes == [12.0], \
        'entering an exposure time should write it to the camera at once'
    assert [c['exptime'] for c in _exposure_configs(panel)] == [12.0] * 3, \
        'the other exposure tabs should show the new exposure time too'
    assert 'Exposure exptime set to 12.0' in _messages(caplog), 'the change should be logged'
    assert not any('changed on the camera' in m for m in _messages(caplog)), \
        "the camera's confirmation of our own edit should not be logged as an outside change"


def test_chosen_speed_is_written_to_the_camera(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)
    window.control_panel.single_speed_choice.button('Fast').click()

    assert fake_hardware['sim'].keyword('nscicam', 'AMPCONF').writes == ['1.0MHz'], \
        'choosing a speed should write its AMPCONF value to the camera at once'


def test_rejected_write_is_reported_and_reverted(qapp, fake_hardware, monkeypatch):
    sim = fake_hardware['sim']
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)

    def reject(value, wait=True, binary=False, timeout=None):
        raise sim.ktl.ktlError('KTL::-5::exposure time out of range')

    monkeypatch.setattr(sim.keyword('nscicam', 'EXPOSURE'), 'write', reject)
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.single_tab)
    type_and_enter(panel.single_exptime_spin, '99')

    assert 'Could not set the exposure exptime to 99.0' in _log_text(window), \
        'a rejected write should be reported'
    assert [c['exptime'] for c in _exposure_configs(panel)] == [5.0] * 3, \
        "the fields should go back to the camera's exposure time"


def test_unknown_camera_setting_is_added_and_logged(qapp, caplog, fake_hardware):
    _set_camera_settings(fake_hardware, '30.0', '0.5MHz', '1,1')
    window = MainWindow()

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller = Controller(window)
        _deliver_broadcasts(controller)

    assert any("'0.5MHz'" in m for m in _messages(caplog, 'WARNING')), \
        'an unknown speed should be reported'
    assert [c['speed'] for c in _exposure_configs(window.control_panel)] == ['0.5MHz'] * 3, \
        'an unknown speed should be added to the options and shown'


def test_unreadable_camera_setting_is_logged(qapp, caplog, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        fake_hardware['sim'].keyword('nscicam', 'EXPOSURE').value = 'garbage'
        _deliver_broadcasts(controller)

    assert any('camera exposure exptime' in m for m in _messages(caplog, 'WARNING')), \
        'an exposure time that is not a number should be logged as a warning'
    assert [c['exptime'] for c in _exposure_configs(window.control_panel)] == [5.0] * 3, \
        'an unreadable value should leave the fields alone'


def test_edit_without_a_camera_only_syncs_the_tabs(qapp):
    window = MainWindow()
    controller = Controller(window, force_enable_hardware_tabs=True)  # noqa: F841 (kept alive)
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.auto_tab)

    type_and_enter(panel.auto_exptime_spin, '8')

    assert [c['exptime'] for c in _exposure_configs(panel)] == [8.0] * 3, \
        'without a camera, an entered exposure time should still appear on every tab'


# -- the exposures' OBJECT -----------------------------------------------------

def _run_grid(controller, window):
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.grid_tab)
    panel.grid_start_spin.setValue(340)
    panel.grid_step_spin.setValue(5)
    panel.grid_nstep_spin.setValue(3)
    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)


def _point_at_target(controller, window, sim, offset_arcmin=0.):
    """Point the simulated telescope at the Slew tab's target, offset in Dec."""
    ra_text, dec_text = window.control_panel.get_target_coordinates()
    sim.ra = Angle(ra_text, unit='hourangle').hour
    sim.dec = Angle(dec_text, unit='deg').deg + offset_arcmin / 60
    _deliver_broadcasts(controller)


def _find_focus_star(controller, window, sim):
    sim.ra, sim.dec = 0.5, 28.1  # near Focusing00
    _deliver_broadcasts(controller)
    controller.find_nearest_focus()
    return window.control_panel.get_target_name()


def test_sequence_object_is_the_found_target(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    sim = fake_hardware['sim']
    name = _find_focus_star(controller, window, sim)
    _point_at_target(controller, window, sim)

    _run_grid(controller, window)

    assert name.startswith('Focusing'), 'setup: a focus star should have been found'
    assert sim.keyword('nscicam', 'OBJECT').writes == [
        f'{name}: focus=340.0', f'{name}: focus=345.0', f'{name}: focus=350.0'
    ], "each exposure's OBJECT should be the found target's name and its focus"


def test_an_edited_name_is_recorded(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    sim = fake_hardware['sim']
    _find_focus_star(controller, window, sim)
    _point_at_target(controller, window, sim)
    type_text(window.control_panel.slew_target_name_edit, 'HD 12345')

    _run_grid(controller, window)

    assert sim.keyword('nscicam', 'OBJECT').writes[0] == 'HD 12345: focus=340.0', \
        'a name the user typed should be recorded'


def test_object_is_unknown_when_the_telescope_is_off_target(qapp, caplog, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    sim = fake_hardware['sim']
    name = _find_focus_star(controller, window, sim)
    _point_at_target(controller, window, sim, offset_arcmin=3.)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        _run_grid(controller, window)

    assert sim.keyword('nscicam', 'OBJECT').writes[0] == 'unknown: focus=340.0', \
        "more than 2 arcmin from the target, the object should be recorded as 'unknown'"
    assert any(
        r.levelname == 'WARNING' and '3.0 arcmin' in r.getMessage() and name in r.getMessage()
        for r in caplog.records
    ), 'the user should be warned that the telescope is off target'
    assert window.control_panel.get_target_name() == name, \
        "the Slew tab's name should be left as it is"


def test_object_is_unknown_within_but_near_the_limit(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    sim = fake_hardware['sim']
    name = _find_focus_star(controller, window, sim)
    _point_at_target(controller, window, sim, offset_arcmin=1.5)

    _run_grid(controller, window)

    assert sim.keyword('nscicam', 'OBJECT').writes[0] == f'{name}: focus=340.0', \
        'within 2 arcmin of the target, its name should be recorded'


def test_sequence_object_is_unknown_for_typed_coordinates(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    sim = fake_hardware['sim']
    _find_focus_star(controller, window, sim)
    type_text(window.control_panel.slew_target_ra_edit, '01:00:00')

    _run_grid(controller, window)

    assert window.control_panel.get_target_name() is None, \
        'typing coordinates should clear the target name'
    assert sim.keyword('nscicam', 'OBJECT').writes[0] == 'unknown: focus=340.0', \
        "typed coordinates should give the object name 'unknown'"


def test_object_is_unknown_without_a_position_to_compare(qapp, caplog, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    window.control_panel.show_nearest_target('00:30:00.00', '+28:00:00.00', 'Focusing00')
    controller._latest.pop(('nickelpoco', 'POCORAA'), None)  # no position received

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        _run_grid(controller, window)

    assert fake_hardware['sim'].keyword('nscicam', 'OBJECT').writes[0] == \
        'unknown: focus=340.0', "with nothing to compare against, the object should be 'unknown'"
    assert any(
        r.levelname == 'WARNING' and 'Could not compare' in r.getMessage()
        for r in caplog.records
    ), 'the user should be warned that the target could not be checked'


def test_single_exposure_object_is_the_found_target(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    sim = fake_hardware['sim']
    sim.ra, sim.dec = 0.5, 29.75  # near Pointing00
    _deliver_broadcasts(controller)
    controller.find_nearest_pointing()
    name = window.control_panel.get_target_name()
    _point_at_target(controller, window, sim)

    controller.take_single_exposure(345.)
    _wait_for_focus_worker(controller)

    assert sim.keyword('nscicam', 'OBJECT').writes == [f'{name}: focus=345.0'], \
        "the Single tab's exposure should record the found target and its focus"
    entry = window.image_panel.exposure_combo.itemText(0)
    assert entry == f'fake0001.fits - {name}: focus=345.0', \
        "the exposure's entry should show its file name and object, from its header"


# -- FWHM in pixels and arcsec ---------------------------------------------------

def test_fwhm_is_reported_in_pixels_and_arcsec(qapp, caplog, fake_hardware):
    window = MainWindow()
    controller = Controller(window)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        _run_grid(controller, window)

    messages = [r.getMessage() for r in caplog.records]
    steps = [m for m in messages if m.startswith('Step ')]
    finished = [m for m in messages if m.startswith('Sequence finished')]
    assert len(steps) == 3 and all(' pix (' in m and ' arcsec)' in m for m in steps), \
        "each step's FWHM should be reported in pixels and arcsec (binning 2,2 is known)"
    assert len(finished) == 1 and ' arcsec)' in finished[0], \
        'the best-fit FWHM should be reported in pixels and arcsec'


def test_fwhm_is_reported_in_pixels_only_without_binning(qapp, caplog, focus_sweep):
    window, controller = _make_controller(focus_sweep)  # replayed files record no binning

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller.start_focus_sequence()
        _wait_for_focus_worker(controller)

    reports = [r.getMessage() for r in caplog.records
               if r.getMessage().startswith(('Step ', 'Sequence finished'))]
    assert reports and all('arcsec' not in m and ' pix' in m for m in reports), \
        'without a known binning, the FWHM should be reported in pixels only'


# -- Use current focus ----------------------------------------------------------

def test_use_current_focus_fills_the_single_focus(qapp, fake_hardware):
    fake_hardware['sim'].keyword('nickelpoco', 'POCSECPA').value = 352.98
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)

    window.control_panel.single_current_focus_button.click()

    assert window.control_panel.single_focus_spin.value() == 353, \
        "the Single tab's focus should be the current focus, rounded"


def test_acquire_at_the_current_focus_does_not_move_it(qapp, caplog, fake_hardware):
    sim = fake_hardware['sim']  # starts with the focus at 340, commanded and actual
    window = MainWindow()
    controller = Controller(window)
    _deliver_broadcasts(controller)
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.single_tab)
    panel.single_focus_spin.setValue(360)

    panel.single_current_focus_button.click()
    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        panel.single_acquire_button.click()
        _wait_for_focus_worker(controller)

    assert panel.single_focus_spin.value() == 340, 'setup: the current focus should be used'
    assert sim.keyword('nickelpoco', 'POCSECPD').writes == [], \
        'acquiring at the current focus should not command a focus move'
    assert any('No change needed' in r.getMessage() for r in caplog.records), \
        'the focus should take the already-at-position path'
    assert sim.exposure_count == 1, 'the exposure should still be taken'


def test_use_current_focus_without_a_value_warns(qapp, caplog):
    window = MainWindow()
    controller = Controller(window)  # no ktl: no current focus
    window.control_panel.single_focus_spin.setValue(360)

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller.use_current_focus()

    assert window.control_panel.single_focus_spin.value() == 360, \
        'without a current focus, the field should be left unchanged'
    assert any(r.levelname == 'WARNING' and 'No current focus' in r.getMessage()
               for r in caplog.records), 'the missing value should be logged as a warning'


# -- the shared services --------------------------------------------------------

def test_controller_shares_one_service_per_ktl_service(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)

    assert controller.poco is not None and controller.cam is not None, \
        'both shared services should be connected'
    assert controller.telescope.poco is controller.poco, \
        'the Slew tab should use the shared telescope service'
    controller.take_single_exposure(345.)
    standalone = controller._standalone_focus_sequence
    _wait_for_focus_worker(controller)
    assert standalone._focus.poco is controller.poco, \
        'a single exposure should use the shared telescope service'
    assert standalone._exposure.cam is controller.cam, \
        'a single exposure should use the shared camera service'


def test_unresponsive_service_is_logged_and_left_unconnected(qapp, caplog, fake_hardware):
    fake_hardware['sim'].keyword('nscicam', 'EXPSTATE').responsive = False
    window = MainWindow()

    with caplog.at_level(logging.INFO, logger='nickel_focus'):
        controller = Controller(window)

    assert controller.cam is None, 'the camera service should not be connected'
    assert controller.poco is not None, 'the telescope service is unaffected'
    _deliver_broadcasts(controller)
    assert any(
        r.levelname == 'WARNING' and 'Could not connect to the nscicam service' in r.getMessage()
        for r in caplog.records
    ), 'the unresponsive service should be logged as a warning'
    assert _indicator_states(window)['exp'] == 'error', \
        'the Exp indicator should be red without the camera service'


# -- Pointing offset ------------------------------------------------------------

def _wait_for_offset_worker(controller, timeout_ms=5000):
    worker = controller.offset_worker
    if worker is None:
        return
    loop = QtCore.QEventLoop()
    worker.finished.connect(loop.quit)
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


def _single_exposure_with_offset(fake_hardware):
    """A Controller whose Single exposure has a pending pointing offset."""
    window = MainWindow()
    controller = Controller(window)
    controller.take_single_exposure(345.)
    _wait_for_focus_worker(controller)
    x, y = window.image_panel.current_result.centroid
    # Slightly off the star: 'p' snaps to its centroid.
    window.image_panel.pointingStarSelected.emit(x + 1.5, y - 1.)
    return window, controller


def test_p_key_sets_a_pending_offset(qapp, fake_hardware):
    window, controller = _single_exposure_with_offset(fake_hardware)
    result = window.image_panel.current_result
    offset = controller.pointing_offset

    assert offset is not None, "'p' near a star should make a pending pointing offset"
    assert offset.star == pytest.approx(result.centroid), \
        'the offset should be measured from the star centroid, not the cursor'
    assert offset.exposure == result.exposure, 'the offset should belong to the displayed exposure'
    assert window.image_panel._pointing_offset is offset, 'the offset should be drawn on the image'
    button = window.control_panel.slew_offset_button
    assert button.text() == f'Offset by {offset.text()}', 'the Slew tab should show the offset'
    assert button.isEnabled(), 'the offset button should be enabled once an offset is pending'
    assert f'{offset.text()}.' in _log_text(window), 'the offset should be logged'


def test_p_key_away_from_any_star_keeps_the_offset(qapp, fake_hardware):
    window, controller = _single_exposure_with_offset(fake_hardware)
    offset = controller.pointing_offset

    window.image_panel.pointingStarSelected.emit(1., 1.)

    assert controller.pointing_offset is offset, \
        "'p' away from any star should leave the pending offset unchanged"
    assert 'No star found within' in _log_text(window), 'missing the star should be a warning'


def test_p_key_with_unknown_binning_warns(qapp, focus_sweep):
    # The Replay files record no binning, so no offset can be measured.
    window, controller = _make_controller(focus_sweep)
    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)
    x, y = window.image_panel.current_result.centroid

    window.image_panel.pointingStarSelected.emit(x, y)

    assert controller.pointing_offset is None, 'without the binning there should be no offset'
    assert 'binning is unknown' in _log_text(window), 'the reason should be logged'


def test_apply_pointing_offset_runs_against_fake_telescope(qapp, fake_hardware):
    window, controller = _single_exposure_with_offset(fake_hardware)
    sim = fake_hardware['sim']
    offset = controller.pointing_offset

    window.control_panel.slew_offset_button.click()
    _wait_for_offset_worker(controller)

    assert sim.guider_offsets == [
        ('POCGDIX', pytest.approx(Angle(offset.east, unit='arcsec').radian)),
        ('POCGDIY', pytest.approx(Angle(offset.north, unit='arcsec').radian)),
    ], 'the pending offset should be written to the guider offsets'
    assert sim.on_target_marks == 1, 'the telescope should be marked on target'
    assert controller.offset_worker is None, 'the worker should be cleared once finished'
    assert controller.pointing_offset is None, 'an applied offset should no longer be pending'
    button = window.control_panel.slew_offset_button
    assert button.text() == 'Offset by —' and not button.isEnabled(), \
        'the button should return to the placeholder, disabled'
    assert window.image_panel._pointing_offset is None, 'the offset should no longer be drawn'
    assert 'Take a Single exposure to check the pointing.' in _log_text(window), \
        'the observer should be reminded to check'


def test_a_failed_pointing_offset_is_reported_and_cleared(qapp, fake_hardware):
    window, controller = _single_exposure_with_offset(fake_hardware)
    fake_hardware['sim'].guider_move_succeeds = False

    controller.apply_pointing_offset()
    _wait_for_offset_worker(controller)

    assert 'Could not apply the pointing offset' in _log_text(window), \
        'a failed offset should be reported'
    assert controller.pointing_offset is None, 'a failed offset should be cleared too'
    assert fake_hardware['sim'].on_target_marks == 0, 'a failed offset must not be marked on target'


def test_pointing_offset_is_a_noop_while_something_is_running(qapp, fake_hardware):
    window, controller = _single_exposure_with_offset(fake_hardware)
    controller.slew_worker = types.SimpleNamespace()  # pretend a slew is running

    controller.apply_pointing_offset()

    assert controller.offset_worker is None, 'an offset request should be ignored while busy'
    assert fake_hardware['sim'].guider_offsets == [], 'the telescope should never be offset'
    controller.slew_worker = None


def test_slewing_clears_the_pending_offset(qapp, fake_hardware):
    window, controller = _single_exposure_with_offset(fake_hardware)

    controller.move_to_target('05:30:00', '+20:15:00')
    _wait_for_slew_worker(controller)

    assert controller.pointing_offset is None, 'a slew should clear the pending offset'
    assert window.control_panel.slew_offset_button.text() == 'Offset by —', \
        'a slew should return the button to the placeholder'


def test_a_new_sequence_clears_the_pending_offset(qapp, fake_hardware, focus_sweep):
    window, controller = _single_exposure_with_offset(fake_hardware)
    _configure_replay_tab(window.control_panel, focus_sweep)

    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)

    assert controller.pointing_offset is None, 'a new sequence should clear the pending offset'
    assert window.control_panel.slew_offset_button.text() == 'Offset by —', \
        'a new sequence should return the button to the placeholder'


def test_pointing_offset_without_ktl_reports_clear_failure(qapp):
    from nickel_focus import pointing
    window = MainWindow()
    controller = Controller(window)
    controller.pointing_offset = pointing.PointingOffset((1., 2.), (3., 4.), 1., 2.)

    controller.apply_pointing_offset()

    assert controller.offset_worker is None, 'no worker should start without a ktl connection'
    assert 'no ktl connection' in _log_text(window).lower(), \
        'the failure should explain that there is no ktl connection'


# -- Single-tab FWHM, and exposures that can't be measured ------------------------

def _blank_frames(fake_hardware):
    """Make the simulated camera produce frames with no star."""
    rng = np.random.default_rng(3)
    fake_hardware['sim'].make_frame = lambda focus_value: rng.normal(1000., 10., (101, 101))


def test_single_exposure_shows_its_fwhm_on_the_single_tab(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    label = window.control_panel.single_fwhm_label
    assert label.text() == 'FWHM: — (pix); — (arcsec)', \
        'before any exposure, the FWHM label should show placeholders'

    controller.take_single_exposure(345.)
    _wait_for_focus_worker(controller)

    result = window.image_panel.current_result
    arcsec = focus.fwhm_arcsec(result.fwhm, result.binning)
    assert result.binning is not None, 'setup: the simulated exposure should record its binning'
    assert label.text() == f'FWHM: {result.fwhm:.2f} (pix); {arcsec:.2f} (arcsec)', \
        'the Single tab should show the measured FWHM in pixels and arcsec'


def test_reanalyzing_a_single_exposure_updates_its_fwhm(qapp, fake_hardware):
    window = MainWindow()
    controller = Controller(window)
    controller.take_single_exposure(345.)
    _wait_for_focus_worker(controller)
    window.control_panel.set_single_fwhm(None, None)

    x, y = window.image_panel.current_result.centroid
    window.image_panel.sourceSelected.emit(x, y)
    _wait_for_focus_worker(controller)

    assert window.control_panel.single_fwhm_label.text() != 'FWHM: — (pix); — (arcsec)', \
        'remeasuring the Single exposure should update its FWHM'


def test_unmeasurable_single_exposure_is_still_displayed(qapp, fake_hardware):
    _blank_frames(fake_hardware)
    window = MainWindow()
    controller = Controller(window)

    controller.take_single_exposure(345.)
    _wait_for_focus_worker(controller)

    result = window.image_panel.current_result
    assert result is not None and not result.measured, \
        'an exposure that cannot be measured should still be displayed'
    assert window.control_panel.single_fwhm_label.text() == 'FWHM: — (pix); — (arcsec)', \
        'with no measurement, the FWHM label should show placeholders'
    text = _log_text(window)
    assert 'Could not measure the image quality' in text, 'the reason should be logged'
    assert 'its FWHM could not be measured' in text, 'the exposure itself should be reported'
    assert 'Could not take single exposure' not in text, 'the exposure should not count as failed'

    window.image_panel.pointingStarSelected.emit(50., 50.)
    assert controller.pointing_offset is None, "'p' should find no star on a blank exposure"
    assert 'No star found within' in _log_text(window), "'p' should say there is no star"


def test_unmeasurable_sequence_exposure_is_displayed_before_stopping(qapp, fake_hardware):
    _blank_frames(fake_hardware)
    window = MainWindow()
    controller = Controller(window)
    panel = window.control_panel
    panel.tabs.setCurrentWidget(panel.grid_tab)

    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)

    assert len(window.image_panel._results) == 1, \
        'the unmeasurable exposure should be displayed before the sequence stops'
    assert window.curve_panel._results == [], 'it should not be added to the focus curve'
    assert 'stopping the sequence' in _log_text(window), 'the sequence should report stopping'


# -- Selected stars ('a' and 'd') ----------------------------------------------

def _three_star_replay(three_star_sweep):
    """A Controller with the three-star sequence loaded through Replay."""
    window, controller = _make_controller(three_star_sweep)
    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)
    return window, controller


def _curve_points(window):
    return list(window.curve_panel._results)


def test_replay_starts_with_the_first_star(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)
    assert len(_curve_points(window)) == 5, 'the curve should start with one star per exposure'
    assert len(window.image_panel.ax.patches) == 1, 'the displayed exposure should box one star'


def test_a_adds_a_star_and_refits(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)

    window.image_panel.starAdded.emit(70., 40.)

    assert len(_curve_points(window)) == 10, 'the added star should add a point per exposure'
    assert len(window.image_panel.ax.patches) == 2, 'both selected stars should be boxed'
    text = _log_text(window)
    assert 'Added the star at' in text and 'matched in 5 of 5 exposures' in text, \
        'the added star should be logged'
    assert 'Refit 10 points from 2 star(s): best focus' in text, 'the refit should be logged'
    assert window.control_panel.single_focus_spin.value() == pytest.approx(350, abs=2), \
        "the refit's best focus should become the Single tab's default"


def test_a_on_a_selected_star_matches_it_again(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)

    window.image_panel.starAdded.emit(30., 30.)

    assert len(_curve_points(window)) == 5, 'the star should be re-matched, not duplicated'
    assert 'Re-matched the star at' in _log_text(window), 'the re-match should be logged'


def test_d_can_remove_every_star_then_a_adds_one_back(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)

    window.image_panel.starRemoved.emit(30., 30.)
    assert _curve_points(window) == [], 'removing the only star should empty the curve'
    assert len(window.image_panel.ax.patches) == 0, 'no star should be boxed'
    assert "No data to fit; press 'a' on a star to add one." in _log_text(window), \
        'an empty curve should be warned about'

    window.image_panel.starAdded.emit(70., 40.)
    assert len(_curve_points(window)) == 5, 'adding a star should give the curve data again'
    assert 'Refit 5 points from 1 star(s)' in _log_text(window), 'and it should be refit'


def test_d_on_a_star_not_plotted_warns(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)

    window.image_panel.starRemoved.emit(70., 40.)

    assert len(_curve_points(window)) == 5, 'the plotted data should be unchanged'
    assert 'is not in the plotted data' in _log_text(window), 'the observer should be told why'


def test_a_away_from_any_star_warns(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)

    window.image_panel.starAdded.emit(5., 95.)

    assert len(_curve_points(window)) == 5, 'the plotted data should be unchanged'
    assert 'No star found within' in _log_text(window), 'the observer should be told why'


def test_m_resets_the_selection_to_the_marked_star(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)
    window.image_panel.starAdded.emit(70., 40.)

    window.image_panel.sourceSelected.emit(50., 75.)
    _wait_for_focus_worker(controller)

    points = _curve_points(window)
    assert len(points) == 5, "'m' should go back to one star per exposure"
    # The third star is missing from the last exposure, where the nearest
    # source to the mark is another star; the other four are the marked one.
    assert sum(abs(p.centroid[1] - 75.) < 2. for p in points) == 4, \
        "'m' should measure the marked star in every exposure that has it"


def test_a_new_sequence_starts_from_the_marked_star(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)
    window.image_panel.sourceSelected.emit(70., 40.)
    _wait_for_focus_worker(controller)
    window.image_panel.starRemoved.emit(70., 40.)
    assert _curve_points(window) == [], 'setup: the marked star should have been removed'

    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)

    points = _curve_points(window)
    assert len(points) == 5, 'a new sequence should start again from one star'
    assert all(abs(p.centroid[0] - 70.) < 2. for p in points), \
        'removing the marked star should not stop it seeding the next sequence'


def test_a_and_d_are_ignored_while_something_runs(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)
    controller.slew_worker = types.SimpleNamespace()  # pretend a slew is running

    window.image_panel.starAdded.emit(70., 40.)
    window.image_panel.starRemoved.emit(30., 30.)

    assert len(_curve_points(window)) == 5, "'a' and 'd' should do nothing while busy"
    controller.slew_worker = None


def test_a_and_d_on_a_single_exposure_update_its_mean_fwhm(qapp, fake_hardware):
    from nickel_focus.tests.conftest import three_star_frame
    fake_hardware['sim'].make_frame = three_star_frame
    window = MainWindow()
    controller = Controller(window)
    controller.take_single_exposure(355.)
    _wait_for_focus_worker(controller)
    label = window.control_panel.single_fwhm_label
    one_star = label.text()

    window.image_panel.starAdded.emit(70., 40.)
    sel = controller._single_selection
    mean = sel.mean_fwhm(0)
    arcsec = focus.fwhm_arcsec(mean, sel.results[0].binning)
    assert len(sel.stars_in(0)) == 2, 'setup: the second star should have been added'
    assert label.text() == f'FWHM: {mean:.2f} (pix); {arcsec:.2f} (arcsec)', \
        'the Single tab should show the mean FWHM of the selected stars'
    assert _curve_points(window) == [], 'a Single exposure should never appear on the curve'
    assert 'Refit' not in _log_text(window), 'a Single exposure should not be refit'

    window.image_panel.starRemoved.emit(70., 40.)
    assert label.text() == one_star, 'removing the star should restore the one-star FWHM'

    window.image_panel.starRemoved.emit(30., 30.)  # the brightest, the first star
    assert label.text() == 'FWHM: — (pix); — (arcsec)', \
        'with no star selected the Single tab should show placeholders'


def _flat(limits):
    """``((a, b), (c, d))`` as ``[a, b, c, d]``, for pytest.approx."""
    return [value for pair in limits for value in pair]


def test_curve_limits_are_fixed_when_the_sequence_finishes(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)
    ax = window.curve_panel.ax
    expected = controller._selection.plot_limits()
    assert _flat((ax.get_xlim(), ax.get_ylim())) == pytest.approx(_flat(expected)), \
        'the limits should be fixed once the sequence finishes'

    window.image_panel.starAdded.emit(70., 40.)
    window.image_panel.starRemoved.emit(30., 30.)
    window.image_panel.starRemoved.emit(70., 40.)
    assert _flat((ax.get_xlim(), ax.get_ylim())) == pytest.approx(_flat(expected)), \
        'adding and removing stars should not move the limits'


def test_curve_limits_are_recalculated_when_remeasured(qapp, three_star_sweep):
    window, controller = _three_star_replay(three_star_sweep)
    window.curve_panel.fix_limits((0., 1.), (0., 1.))  # stand-in for stale limits

    window.image_panel.sourceSelected.emit(70., 40.)
    _wait_for_focus_worker(controller)
    assert _flat(window.curve_panel._fixed_limits) \
        == pytest.approx(_flat(controller._selection.plot_limits())), \
        "'m' should recalculate the limits"

    window.curve_panel.fix_limits((0., 1.), (0., 1.))
    controller.start_focus_sequence()
    _wait_for_focus_worker(controller)
    assert _flat(window.curve_panel._fixed_limits) \
        == pytest.approx(_flat(controller._selection.plot_limits())), \
        'a new sequence should recalculate the limits'
