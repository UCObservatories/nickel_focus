"""Tests for :mod:`gui.views.focus_control_panel`."""
from pathlib import Path

from nickel_focus.gui.qt import QtCore, QtWidgets
from nickel_focus.gui.views.focus_control_panel import FocusControlPanel
from nickel_focus.tests.gui.keyboard import press, type_and_enter, type_text


def test_tabs_exist_in_the_requested_order(qapp):
    panel = FocusControlPanel()
    titles = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
    assert titles == ['Slew', 'Single', 'Grid', 'Auto', 'Replay', 'Log', 'Help'], \
        'tabs should appear in the documented order, with Slew first'


def test_set_hardware_tabs_enabled_false_grays_out_and_switches_to_replay(qapp):
    panel = FocusControlPanel()
    panel.tabs.setCurrentWidget(panel.grid_tab)

    panel.set_hardware_tabs_enabled(False)

    for tab in (panel.slew_tab, panel.single_tab, panel.grid_tab, panel.auto_tab):
        assert not panel.tabs.isTabEnabled(panel.tabs.indexOf(tab)), \
            f'{tab} should be disabled with no ktl connection'
    assert panel.tabs.isTabEnabled(panel.tabs.indexOf(panel.replay_tab)), \
        'Replay should stay enabled -- it never needs a ktl connection'
    assert panel.tabs.currentWidget() is panel.replay_tab, \
        'Replay should become the current tab once the others are disabled'


def test_set_hardware_tabs_enabled_true_leaves_every_tab_enabled(qapp):
    panel = FocusControlPanel()

    panel.set_hardware_tabs_enabled(True)

    for i in range(panel.tabs.count()):
        assert panel.tabs.isTabEnabled(i), f'tab {panel.tabs.tabText(i)!r} should be enabled'


def test_number_entry_boxes_have_no_increment_buttons(qapp):
    panel = FocusControlPanel()
    spin_boxes = (
        panel.single_focus_spin, panel.single_exptime_spin,
        panel.grid_start_spin, panel.grid_step_spin, panel.grid_nstep_spin,
        panel.grid_exptime_spin,
        panel.auto_start_spin, panel.auto_step_spin, panel.auto_maxsteps_spin,
        panel.auto_exptime_spin,
        panel.replay_obsnum_spin, panel.replay_start_spin, panel.replay_step_spin,
        panel.replay_nstep_spin,
    )
    for spin_box in spin_boxes:
        assert spin_box.buttonSymbols() == QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons, \
            f'{spin_box} should not show increment/decrement buttons'


def test_focus_value_entries_are_integers(qapp):
    # A focus value/step size is a whole-unit telescope position, even
    # though a best-fit focus can land on a fractional value (that gets
    # rounded before it's ever offered as the Single tab's default --
    # see the show_best_focus tests below).
    panel = FocusControlPanel()
    integer_entries = (
        panel.single_focus_spin,
        panel.grid_start_spin, panel.grid_step_spin,
        panel.auto_start_spin, panel.auto_step_spin,
        panel.replay_start_spin, panel.replay_step_spin,
    )
    for spin_box in integer_entries:
        assert isinstance(spin_box, QtWidgets.QSpinBox), f'{spin_box} should be an integer entry'


def test_get_focus_sequence_type(qapp):
    panel = FocusControlPanel()

    panel.tabs.setCurrentWidget(panel.single_tab)
    assert panel.get_focus_sequence_type() is None, 'the Single tab should have no sequence type'
    panel.tabs.setCurrentWidget(panel.grid_tab)
    assert panel.get_focus_sequence_type() == 'grid', \
        "the Grid tab's sequence type should be 'grid'"
    panel.tabs.setCurrentWidget(panel.auto_tab)
    assert panel.get_focus_sequence_type() == 'automated', \
        "the Auto tab's sequence type should be 'automated'"
    panel.tabs.setCurrentWidget(panel.replay_tab)
    assert panel.get_focus_sequence_type() == 'archive', \
        "the Replay tab's sequence type should be 'archive'"
    panel.tabs.setCurrentWidget(panel.log_tab)
    assert panel.get_focus_sequence_type() is None, 'the Log tab should have no sequence type'
    panel.tabs.setCurrentWidget(panel.help_tab)
    assert panel.get_focus_sequence_type() is None, 'the Help tab should have no sequence type'


def test_get_focus_sequence_config_for_grid(qapp):
    panel = FocusControlPanel()
    panel.tabs.setCurrentWidget(panel.grid_tab)
    panel.grid_start_spin.setValue(300)
    panel.grid_step_spin.setValue(2)
    panel.grid_nstep_spin.setValue(7)

    assert panel.get_focus_sequence_config() == {'start': 300, 'step': 2, 'nstep': 7}, \
        "the Grid tab's configuration should match its fields"


def test_get_focus_sequence_config_for_auto(qapp):
    panel = FocusControlPanel()
    panel.tabs.setCurrentWidget(panel.auto_tab)
    panel.auto_start_spin.setValue(310)
    panel.auto_step_spin.setValue(3)
    panel.auto_maxsteps_spin.setValue(9)

    assert panel.get_focus_sequence_config() == {'start': 310, 'step': 3, 'maxsteps': 9}, \
        "the Auto tab's configuration should match its fields"


def test_get_focus_sequence_config_for_replay(qapp):
    panel = FocusControlPanel()
    panel.tabs.setCurrentWidget(panel.replay_tab)
    panel.replay_datadir_edit.setText('/tmp/some/dir')
    panel.replay_prefix_edit.setText('x')
    panel.replay_suffix_edit.setText('.fit')
    panel.replay_obsnum_spin.setValue(1234)
    panel.replay_start_spin.setValue(300)
    panel.replay_step_spin.setValue(2)
    panel.replay_nstep_spin.setValue(7)

    assert panel.get_focus_sequence_config() == {
        'datadir': Path('/tmp/some/dir'),
        'prefix': 'x',
        'suffix': '.fit',
        'obsnum': 1234,
        'start': 300,
        'step': 2,
        'nstep': 7,
    }, "the Replay tab's configuration should match its fields"


def test_get_focus_sequence_config_is_empty_outside_grid_auto_replay(qapp):
    panel = FocusControlPanel()
    for tab in (panel.single_tab, panel.log_tab, panel.help_tab):
        panel.tabs.setCurrentWidget(tab)
        assert panel.get_focus_sequence_config() == {}, \
            'only Grid/Auto/Replay should have a sequence configuration'


def test_get_exposure_config_for_single_grid_auto(qapp):
    panel = FocusControlPanel()

    panel.tabs.setCurrentWidget(panel.single_tab)
    type_and_enter(panel.single_exptime_spin, '1.5')
    panel.single_speed_combo.setCurrentText('Fast')
    panel.single_binning_combo.setCurrentText('2,2')
    assert panel.get_exposure_config() == {'exptime': 1.5, 'speed': 'Fast', 'binning': '2,2'}, \
        "the Single tab's exposure configuration should match its fields"

    panel.tabs.setCurrentWidget(panel.grid_tab)
    type_and_enter(panel.grid_exptime_spin, '2.5')
    panel.grid_speed_combo.setCurrentText('Slow')
    panel.grid_binning_combo.setCurrentText('1,1')
    assert panel.get_exposure_config() == {'exptime': 2.5, 'speed': 'Slow', 'binning': '1,1'}, \
        "the Grid tab's exposure configuration should match its fields"

    panel.tabs.setCurrentWidget(panel.auto_tab)
    type_and_enter(panel.auto_exptime_spin, '3.5')
    panel.auto_speed_combo.setCurrentText('Fast')
    panel.auto_binning_combo.setCurrentText('4,4')
    assert panel.get_exposure_config() == {'exptime': 3.5, 'speed': 'Fast', 'binning': '4,4'}, \
        "the Auto tab's exposure configuration should match its fields"


def test_get_exposure_config_is_empty_for_replay_log_help(qapp):
    panel = FocusControlPanel()
    for tab in (panel.replay_tab, panel.log_tab, panel.help_tab):
        panel.tabs.setCurrentWidget(tab)
        assert panel.get_exposure_config() == {}, \
            'only Single/Grid/Auto should have an exposure configuration'


def test_each_tab_has_the_requested_buttons(qapp):
    panel = FocusControlPanel()

    def button_texts(tab):
        return sorted(b.text() for b in tab.findChildren(QtWidgets.QPushButton))

    assert button_texts(panel.slew_tab) == sorted([
        'Browse…', 'Move to Target', 'Object', 'Pointing *', 'Focusing *',
    ]), 'the Slew tab should have its file-browse, move, and three find-nearest buttons'
    assert button_texts(panel.single_tab) == ['Acquire'], 'the Single tab should have only Acquire'
    assert button_texts(panel.grid_tab) == ['Acquire', 'Interrupt'], \
        'the Grid tab should have Acquire and Interrupt'
    assert button_texts(panel.auto_tab) == ['Acquire', 'Interrupt'], \
        'the Auto tab should have Acquire and Interrupt'
    # Replay also has its pre-existing "Browse…" button, unrelated to
    # the acquisition action.
    assert button_texts(panel.replay_tab) == ['Browse…', 'Load'], \
        'the Replay tab should have Browse and Load'
    assert button_texts(panel.log_tab) == [], 'the Log tab should have no buttons'
    assert button_texts(panel.help_tab) == [], 'the Help tab should have no buttons'


def test_single_tab_acquire_emits_take_single_exposure_requested(qapp):
    panel = FocusControlPanel()
    panel.single_focus_spin.setValue(356)
    singles = []
    panel.takeSingleExposureRequested.connect(singles.append)

    panel.single_acquire_button.click()

    assert singles == [356], 'Acquire should emit the configured focus value'


def test_grid_tab_acquire_and_interrupt_signals(qapp):
    panel = FocusControlPanel()
    starts, stops = [], []
    panel.startRequested.connect(lambda: starts.append(True))
    panel.stopRequested.connect(lambda: stops.append(True))

    panel.grid_acquire_button.click()
    assert starts == [True], 'Acquire should emit startRequested'

    # Interrupt starts disabled (nothing is running yet); a disabled
    # button doesn't emit clicked() at all, so put the panel in a
    # running state first, matching how the Controller will actually
    # use it.
    panel.set_running(True)
    panel.grid_interrupt_button.click()
    assert stops == [True], 'Interrupt should emit stopRequested'


def test_auto_tab_acquire_and_interrupt_signals(qapp):
    panel = FocusControlPanel()
    starts, stops = [], []
    panel.startRequested.connect(lambda: starts.append(True))
    panel.stopRequested.connect(lambda: stops.append(True))

    panel.auto_acquire_button.click()
    assert starts == [True], 'Acquire should emit startRequested'

    panel.set_running(True)
    panel.auto_interrupt_button.click()
    assert stops == [True], 'Interrupt should emit stopRequested'


def test_replay_tab_load_emits_start_requested(qapp):
    panel = FocusControlPanel()
    starts = []
    panel.startRequested.connect(lambda: starts.append(True))

    panel.replay_load_button.click()

    assert starts == [True], 'Load should emit startRequested'


def test_slew_move_button_emits_move_to_target_requested(qapp):
    panel = FocusControlPanel()
    panel.slew_target_ra_edit.setText('05:30:00')
    panel.slew_target_dec_edit.setText('+20:15:00')
    moves = []
    panel.moveToTargetRequested.connect(lambda ra, dec: moves.append((ra, dec)))

    panel.slew_move_button.click()

    assert moves == [('05:30:00', '+20:15:00')], \
        'Move to Target should emit the current target RA/Dec field text'


def test_slew_find_object_button_emits_file_and_search_text(qapp):
    panel = FocusControlPanel()
    panel.slew_file_edit.setText('/some/stars.txt')
    panel.slew_search_edit.setText('Pointing')
    requests = []
    panel.findNearestObjectRequested.connect(lambda file, search: requests.append((file, search)))

    panel.slew_find_object_button.click()

    assert requests == [('/some/stars.txt', 'Pointing')], \
        'Find nearest object should emit the current file/search field text'


def test_slew_find_pointing_and_focus_buttons_emit_their_signals(qapp):
    panel = FocusControlPanel()
    pointing_requests, focus_requests = [], []
    panel.findNearestPointingRequested.connect(lambda: pointing_requests.append(True))
    panel.findNearestFocusRequested.connect(lambda: focus_requests.append(True))

    panel.slew_find_pointing_button.click()
    panel.slew_find_focus_button.click()

    assert pointing_requests == [True], 'Find nearest pointing star should emit its own signal'
    assert focus_requests == [True], 'Find nearest focus star should emit its own signal'


def test_set_running_locks_config_widgets_and_acquire_buttons(qapp):
    panel = FocusControlPanel()

    panel.set_running(True)
    assert not panel.grid_start_spin.isEnabled(), 'config fields should be locked while running'
    assert not panel.slew_target_ra_edit.isEnabled(), \
        'the Slew tab target field should be locked while running'
    for button in (panel.single_acquire_button, panel.grid_acquire_button,
                   panel.auto_acquire_button, panel.replay_load_button,
                   panel.slew_move_button, panel.slew_find_object_button,
                   panel.slew_find_pointing_button, panel.slew_find_focus_button):
        assert not button.isEnabled(), f'{button.text()} should be disabled while running'
    for button in (panel.grid_interrupt_button, panel.auto_interrupt_button):
        assert button.isEnabled(), f'{button.text()} should be enabled while running'

    panel.set_running(False)
    assert panel.grid_start_spin.isEnabled(), 'config fields should unlock once stopped'
    assert panel.slew_target_ra_edit.isEnabled(), \
        'the Slew tab target field should unlock once stopped'
    for button in (panel.single_acquire_button, panel.grid_acquire_button,
                   panel.auto_acquire_button, panel.replay_load_button,
                   panel.slew_move_button, panel.slew_find_object_button,
                   panel.slew_find_pointing_button, panel.slew_find_focus_button):
        assert button.isEnabled(), f'{button.text()} should be re-enabled once stopped'
    for button in (panel.grid_interrupt_button, panel.auto_interrupt_button):
        assert not button.isEnabled(), f'{button.text()} should be disabled once stopped'


def test_tab_bar_stays_enabled_while_running(qapp):
    # The Log tab must stay reachable while a sequence runs.
    panel = FocusControlPanel()
    panel.set_running(True)
    assert panel.tabs.isEnabled(), 'the tab bar itself should never be disabled'


def test_set_stopping_disables_interrupt_buttons_and_shows_status(qapp):
    panel = FocusControlPanel()
    panel.set_running(True)

    panel.set_stopping()

    assert not panel.grid_interrupt_button.isEnabled(), 'Interrupt should disable itself once clicked'
    assert not panel.auto_interrupt_button.isEnabled(), 'Interrupt should disable itself once clicked'
    assert 'Stopping' in panel.status_label.text(), 'status should explain the wait'


def test_update_step_updates_label(qapp):
    panel = FocusControlPanel()

    panel.update_step('Step 2/5 — Focus 345')

    assert panel.step_label.text() == 'Step 2/5 — Focus 345', \
        'update_step should show the given text'


def test_show_best_focus_updates_status_and_single_tab_default(qapp):
    panel = FocusControlPanel()

    panel.show_best_focus(356.6, 'Sequence finished')

    assert panel.status_label.text() == 'Sequence finished', \
        'show_best_focus should show the given text as the status'
    assert panel.single_focus_spin.value() == 357, \
        'the Single tab default should be the best focus, rounded to the nearest integer'


def test_show_status_updates_status(qapp):
    panel = FocusControlPanel()
    panel.show_status('Stopped early -- not enough points for a focus fit')

    assert panel.status_label.text() == 'Stopped early -- not enough points for a focus fit', \
        'show_status should show the given text'


def test_status_and_step_labels_wrap_instead_of_widening(qapp):
    # An unwrapped QLabel reports its entire single-line text width as
    # its minimum size, which would balloon the whole panel's width to
    # fit a long message (e.g. a failure listing several file paths).
    panel = FocusControlPanel()
    assert panel.status_label.wordWrap(), 'status_label must wrap long messages'
    assert panel.step_label.wordWrap(), 'step_label must wrap long messages'

    # Realistic long message (space-separated words) -- word wrap can
    # only break between words, so a single unbroken token of the same
    # length wouldn't exercise the fix meaningfully.
    long_message = 'Expected to find the following files, but they are not available: ' + \
        ', '.join(f'/some/long/data/directory/n{2000 + i}.fits' for i in range(10))
    panel.show_status(long_message)

    assert panel.status_label.minimumSizeHint().width() < 200, \
        'a long message should not inflate the label (and thus the panel) width'


def test_reset_clears_status_and_log(qapp):
    panel = FocusControlPanel()
    panel.update_step('Step 1')
    panel.show_status('oops')
    panel.append_log_line('oops')  # the view itself no longer appends to the log

    panel.reset()

    assert panel.step_label.text() == 'Step: —', 'reset should restore the step placeholder'
    assert panel.status_label.text() == '', 'reset should clear the status'
    assert panel.log_widget.toPlainText() == '', 'reset should clear the Log tab'


def test_reset_does_not_touch_the_single_tab_default(qapp):
    panel = FocusControlPanel()
    panel.show_best_focus(356.6, 'Sequence finished')

    panel.reset()

    assert panel.single_focus_spin.value() == 357, \
        "reset() clears status/log, but shouldn't discard the Single tab's best-focus default"


def test_browse_button_updates_datadir(qapp, monkeypatch):
    panel = FocusControlPanel()

    monkeypatch.setattr(QtWidgets.QFileDialog, 'getExistingDirectory',
                         lambda *a, **k: '/some/chosen/dir')
    panel._on_browse_clicked()
    assert panel.replay_datadir_edit.text() == '/some/chosen/dir', \
        'Browse should put the chosen directory in the data-directory field'

    # Canceling the dialog (empty string) should leave the field alone.
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getExistingDirectory', lambda *a, **k: '')
    panel._on_browse_clicked()
    assert panel.replay_datadir_edit.text() == '/some/chosen/dir', 'canceling should not clear the field'


def test_slew_browse_button_updates_file_edit(qapp, monkeypatch):
    panel = FocusControlPanel()

    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileName',
                         lambda *a, **k: ('/some/stars.txt', ''))
    panel._on_slew_browse_clicked()
    assert panel.slew_file_edit.text() == '/some/stars.txt', \
        'Browse should put the chosen file in the starlist field'

    # Canceling the dialog (empty string) should leave the field alone.
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileName', lambda *a, **k: ('', ''))
    panel._on_slew_browse_clicked()
    assert panel.slew_file_edit.text() == '/some/stars.txt', 'canceling should not clear the field'


def test_set_current_position_updates_labels(qapp):
    panel = FocusControlPanel()

    panel.set_current_position('05:30:00.00', '+20:15:00.00')

    assert panel.slew_current_ra_label.text() == '05:30:00.00', \
        'the current-RA label should show exactly what was passed in'
    assert panel.slew_current_dec_label.text() == '+20:15:00.00', \
        'the current-Dec label should show exactly what was passed in'


def test_show_nearest_target_populates_target_fields_and_status(qapp):
    panel = FocusControlPanel()

    panel.show_nearest_target('00:30:07.29', '+29:45:06.20', 'Nearest target: Pointing00')

    assert panel.slew_target_ra_edit.text() == '00:30:07.29', \
        'the target RA field should be populated with the found target'
    assert panel.slew_target_dec_edit.text() == '+29:45:06.20', \
        'the target Dec field should be populated with the found target'
    assert panel.status_label.text() == 'Nearest target: Pointing00', \
        'the found target should be reported as the status'


def test_preferred_height_excluding_help_ignores_the_tallest_page(qapp):
    panel = FocusControlPanel()

    height = panel.preferred_height_excluding_help()

    tab_bar_height = panel.tabs.tabBar().sizeHint().height()
    assert height < panel.help_tab.widget().minimumSizeHint().height() + tab_bar_height, \
        'excluding Help should give a smaller preferred height than including it would'
    other_tabs = (panel.slew_tab, panel.single_tab, panel.grid_tab, panel.auto_tab,
                  panel.replay_tab, panel.log_tab)
    for tab in other_tabs:
        assert height >= tab.widget().minimumSizeHint().height(), \
            'the preferred height should be tall enough for every tab except Help'


def test_help_tab_has_reminder_text(qapp):
    panel = FocusControlPanel()
    labels = panel.help_tab.findChildren(QtWidgets.QLabel)
    assert labels, 'the Help tab should contain some descriptive text'
    text = ' '.join(label.text() for label in labels)
    for keyword in ('Slew', 'Single', 'Grid', 'Auto', 'Replay', 'Log'):
        assert keyword in text, f'Help text should mention {keyword}'


def test_set_exposure_config_sets_every_exposure_tab(qapp):
    panel = FocusControlPanel()

    unmatched = panel.set_exposure_config({'exptime': 12.5, 'speed': 'Fast', 'binning': '1,1'})

    assert unmatched == [], 'every setting should match a GUI option'
    for tab in (panel.single_tab, panel.grid_tab, panel.auto_tab):
        panel.tabs.setCurrentWidget(tab)
        assert panel.get_exposure_config() == {
            'exptime': 12.5, 'speed': 'Fast', 'binning': '1,1'}, \
            'every exposure tab should show the given settings'


def test_set_exposure_config_adds_unknown_options(qapp):
    panel = FocusControlPanel()
    panel.tabs.setCurrentWidget(panel.single_tab)
    before = panel.get_exposure_config()

    added = panel.set_exposure_config({'exptime': 7.0, 'binning': '3,3'})

    assert added == ['binning'], 'the unknown binning should be reported as added'
    after = panel.get_exposure_config()
    assert after['exptime'] == 7.0, 'the exposure time should be applied'
    assert after['binning'] == '3,3', 'an unknown binning should be added and shown'
    assert after['speed'] == before['speed'], 'a missing key should leave the field alone'
    for combo in (panel.single_binning_combo, panel.grid_binning_combo, panel.auto_binning_combo):
        assert combo.findText('3,3') >= 0, 'the new option should be added on every exposure tab'


# -- exposure-time entry and exposure-setting edits ---------------------------

def _pending(spin):
    return spin.is_pending() and spin.styleSheet() == spin.PENDING_STYLE


def test_exposure_time_edit_is_pending_until_enter(qapp):
    panel = FocusControlPanel()
    panel.tabs.setCurrentWidget(panel.single_tab)
    spin = panel.single_exptime_spin
    edits = []
    panel.exposureSettingEdited.connect(lambda key, value: edits.append((key, value)))

    type_text(spin, '12.5')

    assert _pending(spin), 'an edited, unentered exposure time should be shown light red'
    assert panel.get_exposure_config()['exptime'] == 5.0, \
        'an unentered exposure time should not be used'
    assert edits == [], 'nothing should be reported before Enter'

    press(spin, QtCore.Qt.Key.Key_Return)

    assert not _pending(spin), 'Enter should clear the light-red background'
    assert panel.get_exposure_config()['exptime'] == 12.5, 'Enter should commit the new value'
    assert edits == [('exptime', 12.5)], 'Enter should report the committed exposure time'


def test_arrow_key_steps_are_pending_too(qapp):
    panel = FocusControlPanel()
    spin = panel.single_exptime_spin

    press(spin, QtCore.Qt.Key.Key_Up)

    assert _pending(spin), 'an arrow-key step is an edit, pending until Enter'


def test_set_exposure_config_discards_a_pending_edit(qapp):
    panel = FocusControlPanel()
    panel.tabs.setCurrentWidget(panel.single_tab)
    type_text(panel.single_exptime_spin, '99')

    panel.set_exposure_config({'exptime': 30.0})

    assert not _pending(panel.single_exptime_spin), \
        'a value set by the program should clear the pending state'
    assert panel.single_exptime_spin.value() == 30.0, 'the set value should be shown'


def _choose(combo, text):
    """Choose ``text`` in ``combo`` the way a user does: select it, then `activated`."""
    index = combo.findText(text)
    combo.setCurrentIndex(index)
    combo.activated.emit(index)


def test_choosing_a_speed_or_binning_is_reported_at_once(qapp):
    panel = FocusControlPanel()
    edits = []
    panel.exposureSettingEdited.connect(lambda key, value: edits.append((key, value)))

    _choose(panel.grid_speed_combo, 'Fast')
    _choose(panel.auto_binning_combo, '4,4')

    assert edits == [('speed', 'Fast'), ('binning', '4,4')], \
        'choosing a speed or binning should report it immediately'


def test_setting_fields_by_program_reports_nothing(qapp):
    panel = FocusControlPanel()
    edits = []
    panel.exposureSettingEdited.connect(lambda key, value: edits.append((key, value)))

    panel.set_exposure_config({'exptime': 30.0, 'speed': 'Fast', 'binning': '1,1'})

    assert edits == [], 'only user edits should be reported, not values set by the program'