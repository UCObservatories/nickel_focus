"""Tests for :mod:`gui.views.image_panel`."""
import dataclasses

import pytest
from matplotlib.backend_bases import KeyEvent, MouseEvent
from matplotlib.colors import to_rgba

from nickel_focus import focus
from nickel_focus.gui.qt import QtCore, QtGui, QtWidgets
from nickel_focus.gui.views.image_panel import ElidingComboBox, ImagePanel


def _step_results(focus_sweep):
    seq = focus.ArchiveFocusSequence(focus_sweep['focus_values'], focus_sweep['files'])
    return list(seq.step(method='brightest'))


def test_add_result_keeps_dropdown_sorted_by_focus_value(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    # results are naturally ascending in focus (340..360); add them out of
    # order to confirm sorting doesn't depend on acquisition order.
    scrambled = [results[2], results[0], results[4], results[1], results[3]]

    panel = ImagePanel()
    for r in scrambled:
        panel.add_result(r)

    combo_focus_values = [r.focus_value for r in panel._results]
    assert combo_focus_values == sorted(r.focus_value for r in results), \
        'dropdown should stay sorted by focus value regardless of insertion order'
    assert panel.exposure_combo.count() == len(results), 'dropdown item count should match'

    # "Latest" tracks acquisition order: the last-added result (focus 355)
    # should be displayed, even though it isn't the highest focus value.
    assert panel._current is scrambled[-1], \
        'panel should auto-follow the most recently *added* result, not the sorted-last one'


def test_manual_selection_is_not_disrupted_by_new_results(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    for r in results[:3]:
        panel.add_result(r)

    panel.exposure_combo.setCurrentIndex(0)
    assert panel._current is results[0], 'setup: manual selection should change the displayed result'

    panel.add_result(results[3])
    assert panel._current is results[0], \
        'adding a new result should not disrupt a manually-selected older entry'
    assert results[3] in panel._results, 'the new result should still be listed in the dropdown'


def test_m_key_emits_signal_only_when_selection_enabled(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    panel.add_result(results[0])

    received = []
    panel.sourceSelected.connect(lambda x, y: received.append((x, y)))

    event = KeyEvent('key_press_event', panel.canvas, 'm')
    event.inaxes = panel.ax
    event.xdata, event.ydata = 42.0, 17.0

    panel._on_key_press(event)
    assert received == [], "'m' should be ignored while selection is disabled"

    panel.set_selection_enabled(True)
    panel._on_key_press(event)
    assert received == [(42.0, 17.0)], \
        "'m' should emit the data coordinates under the cursor once enabled"


def test_other_keys_do_not_select_a_source(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    panel.add_result(results[0])
    panel.set_selection_enabled(True)

    received = []
    panel.sourceSelected.connect(lambda x, y: received.append((x, y)))

    other_key = KeyEvent('key_press_event', panel.canvas, 'a')
    other_key.inaxes = panel.ax
    other_key.xdata, other_key.ydata = 42.0, 17.0
    panel._on_key_press(other_key)
    assert received == [], "only the 'm' key should select a source"


def test_plain_click_does_not_select_a_source(qapp, focus_sweep):
    # A plain click must never select a source on its own -- on macOS,
    # the click that refocuses an inactive window is delivered to
    # whatever's underneath the cursor, so a click-to-select would
    # trigger reanalysis just from bringing the window forward.
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    panel.add_result(results[0])
    panel.set_selection_enabled(True)

    received = []
    panel.sourceSelected.connect(lambda x, y: received.append((x, y)))

    # Dispatch through the canvas's own callback registry (matching how
    # an actual click arrives), rather than calling a handler directly --
    # this proves nothing is listening for clicks anymore, not just that
    # some particular method is gone.
    event = MouseEvent('button_press_event', panel.canvas, x=10, y=10, button=1)
    event.inaxes = panel.ax
    event.xdata, event.ydata = 42.0, 17.0
    panel.canvas.callbacks.process('button_press_event', event)

    assert received == [], 'a plain click should never select a source'


def test_outlier_box_color_reflects_is_outlier(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()

    panel.show_result(dataclasses.replace(results[0], is_outlier=False))
    colors = [p.get_edgecolor() for p in panel.ax.patches]
    assert to_rgba('red') in colors, 'a non-outlier result should be boxed in red'

    panel.show_result(dataclasses.replace(results[0], is_outlier=True))
    colors = [p.get_edgecolor() for p in panel.ax.patches]
    assert to_rgba('yellow') in colors, 'an outlier result should be boxed in yellow'


def test_dropdown_selection_preserves_zoom_and_center(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    for r in results:
        panel.add_result(r)

    panel.set_zoom(2.0)
    panel.h_scroll.setValue(10)
    panel.v_scroll.setValue(15)
    zoom_before = panel._zoom
    xlim_before, ylim_before = panel.ax.get_xlim(), panel.ax.get_ylim()

    panel.exposure_combo.setCurrentIndex(0)

    assert panel._current is results[0], 'setup: the drop-down selection should have changed'
    assert panel._zoom == zoom_before, \
        'switching frames via the drop-down should not change the zoom factor'
    assert panel.ax.get_xlim() == pytest.approx(xlim_before), \
        'switching frames via the drop-down should keep the same view center'
    assert panel.ax.get_ylim() == pytest.approx(ylim_before), \
        'switching frames via the drop-down should keep the same view center'


def test_zoom_and_recenter_affect_scroll_range(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    panel.show_result(results[0])

    assert panel.h_scroll.maximum() == 0, 'a fit-to-view zoom should start with nothing to scroll'
    assert panel.v_scroll.maximum() == 0, 'a fit-to-view zoom should start with nothing to scroll'

    panel.set_zoom(2.0)
    assert panel.h_scroll.maximum() > 0, 'zooming in should open up horizontal scroll range'
    assert panel.v_scroll.maximum() > 0, 'zooming in should open up vertical scroll range'

    panel.recenter()
    assert panel.h_scroll.maximum() == 0, 'recenter should return to a fit-to-view zoom'


def test_update_result_replaces_in_place(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    for r in results:
        panel.add_result(r)

    updated = dataclasses.replace(results[2], fwhm=results[2].fwhm + 10., is_outlier=True)
    panel.update_result(updated)

    assert len(panel._results) == len(results), \
        'update_result() should not add a new entry for an already-known exposure'
    assert panel.exposure_combo.count() == len(results), \
        'update_result() should not add a new dropdown entry either'
    assert panel._results[2].fwhm == updated.fwhm, 'the stored measurement should be replaced'


def test_update_result_redisplays_if_it_is_the_current_one(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    for r in results:
        panel.add_result(r)  # panel is now showing results[-1] (the latest added)

    updated = dataclasses.replace(results[-1], is_outlier=True)
    panel.update_result(updated)

    assert panel._current is updated, 'updating the currently-displayed result should redisplay it'


def test_update_result_falls_back_to_add_for_an_unknown_exposure(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    panel.add_result(results[0])

    panel.update_result(results[1])

    assert len(panel._results) == 2, \
        'update_result() for an exposure not yet shown should behave like add_result()'


def test_wheel_zoom_keeps_the_cursor_point_fixed(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    panel.show_result(results[0])  # 101x101 frame, fit-to-view (zoom=1)

    # Scroll in centered on a point away from the image center; if the
    # zoom weren't anchored to the cursor, the view would instead stay
    # anchored at the (0, 0) top-left corner and this point would drift
    # away from the cursor's screen position.
    cursor_x, cursor_y = 20.0, 80.0
    event = MouseEvent('scroll_event', panel.canvas, x=10, y=10, button='up')
    event.inaxes = panel.ax
    event.xdata, event.ydata = cursor_x, cursor_y

    panel._on_wheel_zoom(event)

    assert panel._zoom > 1.0, 'scrolling up should zoom in'
    # Limits in increasing order: the y-axis is inverted (row 0 at the top).
    xlim, ylim = sorted(panel.ax.get_xlim()), sorted(panel.ax.get_ylim())
    assert xlim[0] <= cursor_x <= xlim[1], 'the cursor location should remain within view'
    assert ylim[0] <= cursor_y <= ylim[1], 'the cursor location should remain within view'
    # The cursor's fractional position within the view should be
    # essentially unchanged (it started at fraction (0.2, 0.8) of the
    # full 0-101 frame).
    frac_x = (cursor_x - xlim[0]) / (xlim[1] - xlim[0])
    frac_y = (cursor_y - ylim[0]) / (ylim[1] - ylim[0])
    assert frac_x == pytest.approx(0.2, abs=0.02), 'the cursor should stay over the same data point'
    assert frac_y == pytest.approx(0.8, abs=0.02), 'the cursor should stay over the same data point'


def test_reset_clears_everything(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    for r in results:
        panel.add_result(r)

    panel.reset()

    assert panel.exposure_combo.count() == 0, 'reset should clear the dropdown'
    assert panel._current is None, 'reset should clear the currently displayed result'
    assert panel._results == [], 'reset should clear the stored results'


def test_every_stretch_option_renders_without_error(qapp, focus_sweep):
    result = _step_results(focus_sweep)[0]
    panel = ImagePanel()

    for name in panel.STRETCHES:
        panel.stretch_combo.setCurrentText(name)
        panel.show_result(result)
        assert panel._stretch_name == name, f'the {name} stretch should be selected'
        assert panel.ax.get_images(), f'{name} should have actually drawn an image'


def test_image_is_anchored_to_the_top_of_the_canvas(qapp, focus_sweep):
    # Keeps the image directly under the Exposure row, instead of centered
    # with a blank band above it when the frame and canvas shapes differ.
    panel = ImagePanel()
    panel.add_result(_step_results(focus_sweep)[0])

    assert panel.ax.get_anchor() == 'N', 'the image should be anchored to the top of the canvas'


def test_exposure_entries_show_file_and_object(qapp, focus_sweep):
    panel = ImagePanel()
    result = dataclasses.replace(
        _step_results(focus_sweep)[0], object_name='Pointing12: focus=345.0'
    )

    panel.add_result(result)

    assert panel.exposure_combo.itemText(0) == \
        f'{result.exposure.name} - Pointing12: focus=345.0', \
        'the entry should be the file name and the object name'


def test_exposure_entries_fall_back_to_the_focus(qapp, focus_sweep):
    panel = ImagePanel()
    result = _step_results(focus_sweep)[0]  # these files record no OBJECT

    panel.add_result(result)

    assert panel.exposure_combo.itemText(0) == \
        f'{result.exposure.name} - focus={result.focus_value:.1f}', \
        'without an object name, the entry should show the focus value'


def test_exposure_dropdown_width_does_not_follow_its_entries(qapp, focus_sweep):
    panel = ImagePanel()
    before = panel.exposure_combo.sizeHint().width()
    long_name = 'AVeryLongObjectNameFromSomeoneElsesStarlist' * 3 + ': focus=345.0'

    panel.add_result(dataclasses.replace(_step_results(focus_sweep)[0], object_name=long_name))

    assert panel.exposure_combo.sizeHint().width() == before, \
        'a long entry should not change the size of the exposure drop-down'


def test_top_row_is_exposure_label_dropdown_and_stretch(qapp):
    panel = ImagePanel()
    row = panel.layout().itemAt(0).layout()

    widgets = [row.itemAt(i).widget() for i in range(row.count())]

    assert len(widgets) == 3 and isinstance(widgets[0], QtWidgets.QLabel), \
        'the top row should be just the Exposure label, its drop-down, and the stretch drop-down'
    assert widgets[1] is panel.exposure_combo and widgets[2] is panel.stretch_combo, \
        'the stretch drop-down should be at the right'
    assert row.stretch(1) == 1 and row.stretch(2) == 0, \
        'the exposure drop-down should fill the remaining width'
    assert not hasattr(panel, 'recenter_button'), 'there should be no Recenter button'


def _key_event(panel, key):
    return KeyEvent('key_press_event', panel.canvas, key, x=10, y=10)


def test_r_key_recenters(qapp, focus_sweep):
    panel = ImagePanel()
    panel.add_result(_step_results(focus_sweep)[0])
    panel.set_zoom(3.)
    panel.set_selection_enabled(False)  # recentering works even while a sequence runs

    panel.canvas.callbacks.process('key_press_event', _key_event(panel, 'r'))

    assert panel._zoom == 1.0, "pressing 'r' should return to a fit-to-view zoom"


def test_capital_r_does_not_recenter(qapp, focus_sweep):
    panel = ImagePanel()
    panel.add_result(_step_results(focus_sweep)[0])
    panel.set_zoom(3.)

    panel.canvas.callbacks.process('key_press_event', _key_event(panel, 'R'))

    assert panel._zoom == 3.0, "the recenter key is case sensitive: 'R' should do nothing"


LONG_ENTRY = 'n3247.fits - 2MASS J04361038+2259560 (Taurus field star): focus=345.0'


def _shown_combo(qapp, width=220):
    combo = ElidingComboBox()
    combo.addItem(LONG_ENTRY)
    combo.resize(width, combo.sizeHint().height())
    combo.show()
    qapp.processEvents()
    return combo


def test_long_entry_is_shortened_in_the_middle(qapp, monkeypatch):
    combo = _shown_combo(qapp)
    drawn = []
    original = QtWidgets.QStylePainter.drawControl

    def record(painter, element, option):
        if element == QtWidgets.QStyle.ControlElement.CE_ComboBoxLabel:
            drawn.append(option.currentText)
        return original(painter, element, option)

    monkeypatch.setattr(QtWidgets.QStylePainter, 'drawControl', record)
    combo.repaint()

    assert drawn, 'setup: the label should have been drawn'
    text = drawn[-1]
    assert '…' in text and len(text) < len(LONG_ENTRY), 'a long entry should be shortened'
    assert text.startswith('n3247.fits') and text.endswith('focus=345.0'), \
        'the file name and the focus should both stay visible'
    combo.close()


def test_popup_is_wide_enough_for_long_entries(qapp):
    combo = _shown_combo(qapp)

    combo.showPopup()
    qapp.processEvents()

    view = combo.view()
    assert view.width() >= view.sizeHintForColumn(0), \
        'the open list should be wide enough to show the full entry'
    assert view.width() > combo.width(), 'the open list may be wider than the box'
    combo.hidePopup()
    combo.close()


def test_tooltip_shows_the_full_entry(qapp, monkeypatch):
    combo = _shown_combo(qapp)
    shown = []
    monkeypatch.setattr(QtWidgets.QToolTip, 'showText',
                        lambda pos, text, widget=None, *args: shown.append(text))

    combo.event(QtGui.QHelpEvent(QtCore.QEvent.Type.ToolTip, QtCore.QPoint(5, 5),
                                 combo.mapToGlobal(QtCore.QPoint(5, 5))))

    assert shown == [LONG_ENTRY], 'hovering should show the full current entry'
    combo.close()


def test_image_is_shown_north_up(qapp, focus_sweep):
    # The frames' row 0 is at the south, so drawing row 0 at the top puts
    # North up (East stays to the left).
    panel = ImagePanel()
    panel.add_result(_step_results(focus_sweep)[0])

    image = panel.ax.get_images()[0]
    bottom, top = panel.ax.get_ylim()
    assert image.origin == 'upper', 'row 0 of the frame should be drawn at the top'
    assert top < bottom, 'the y-axis should increase downward, so row 0 is at the top'
    left, right = panel.ax.get_xlim()
    assert left < right, 'columns should still run left to right'


def test_results_keep_the_raw_frame(qapp, focus_sweep):
    from astropy.io import fits
    result = _step_results(focus_sweep)[0]
    with fits.open(result.exposure) as hdu:
        expected = hdu[0].data.astype(float)
    assert result.raw is not None, 'each step result should keep the raw image data'
    assert (result.raw == expected).all(), 'the raw data should match the file'
    assert not (result.raw == result.frame).all(), \
        'the displayed frame should still be background-subtracted'


def _motion(panel, xdata, ydata, x=100, y=100, over_image=True):
    """A mouse-move event at canvas position (x, y), matplotlib's convention
    (physical pixels from the bottom-left), over data point (xdata, ydata)."""
    event = MouseEvent('motion_notify_event', panel.canvas, x=x, y=y)
    event.inaxes = panel.ax if over_image else None
    event.xdata, event.ydata = (xdata, ydata) if over_image else (None, None)
    return event


def _hover_panel(focus_sweep):
    panel = ImagePanel()
    panel.canvas.resize(300, 300)
    panel.add_result(_step_results(focus_sweep)[0])
    return panel


def test_readout_shows_raw_value_under_the_pointer(qapp, focus_sweep):
    panel = _hover_panel(focus_sweep)
    raw = panel._current.raw
    # 12.4, 7.6 lies in the pixel centred on column 12, row 8.
    panel._on_mouse_move(_motion(panel, 12.4, 7.6))
    assert not panel.readout.isHidden(), 'the readout should show over the image'
    assert panel.readout.text() == f'12, 8: {raw[8, 12]:.7g}', \
        'the readout should give the 0-based pixel and its raw value'


def test_readout_sits_below_right_of_the_pointer(qapp, focus_sweep):
    panel = _hover_panel(focus_sweep)
    ratio = panel.canvas.device_pixel_ratio
    panel._on_mouse_move(_motion(panel, 5, 5, x=50 * ratio, y=(300 - 60) * ratio))
    offset = ImagePanel.READOUT_OFFSET
    assert (panel.readout.x(), panel.readout.y()) == (50 + offset, 60 + offset), \
        'the readout should sit just below and to the right of the pointer'


def test_readout_flips_near_the_bottom_right_edge(qapp, focus_sweep):
    panel = _hover_panel(focus_sweep)
    ratio = panel.canvas.device_pixel_ratio
    panel._on_mouse_move(_motion(panel, 5, 5, x=295 * ratio, y=5 * ratio))
    offset = ImagePanel.READOUT_OFFSET
    assert panel.readout.x() + panel.readout.width() == 295 - offset, \
        'near the right edge the readout should move to the left of the pointer'
    assert panel.readout.y() + panel.readout.height() == 295 - offset, \
        'near the bottom edge the readout should move above the pointer'


def test_readout_hides_off_the_image(qapp, focus_sweep):
    panel = _hover_panel(focus_sweep)
    ny, nx = panel._current.frame.shape
    panel._on_mouse_move(_motion(panel, 5, 5))
    panel._on_mouse_move(_motion(panel, 5, 5, over_image=False))
    assert panel.readout.isHidden(), 'the readout should hide when the pointer leaves the axes'

    panel._on_mouse_move(_motion(panel, 5, 5))
    panel._on_mouse_move(_motion(panel, nx + 3, 5))
    assert panel.readout.isHidden(), 'the readout should hide beyond the edge of the image'

    panel._on_mouse_move(_motion(panel, 5, 5))
    panel._hide_readout(MouseEvent('figure_leave_event', panel.canvas, x=0, y=0))
    assert panel.readout.isHidden(), 'the readout should hide when the pointer leaves the canvas'


def test_readout_hides_when_the_view_or_exposure_changes(qapp, focus_sweep):
    panel = _hover_panel(focus_sweep)
    results = _step_results(focus_sweep)

    panel._on_mouse_move(_motion(panel, 5, 5))
    panel.add_result(results[1])
    assert panel.readout.isHidden(), 'a newly displayed exposure should hide the readout'

    panel.set_zoom(4)
    panel._on_mouse_move(_motion(panel, 5, 5))
    panel.h_scroll.setValue(panel.h_scroll.value() + 1)
    assert panel.readout.isHidden(), 'scrolling should hide the (now stale) readout'

    panel._on_mouse_move(_motion(panel, 5, 5))
    panel.reset()
    assert panel.readout.isHidden(), 'clearing the panel should hide the readout'


def test_readout_without_raw_data_shows_only_the_pixel(qapp, focus_sweep):
    panel = ImagePanel()
    panel.add_result(dataclasses.replace(_step_results(focus_sweep)[0], raw=None))
    assert panel.readout_text(3.2, 4.9) == '3, 5', \
        'without raw data the readout should give only the pixel'
    assert panel.readout_text(-1, 4) is None, 'off the image there should be no readout'


def test_raw_frame_keeps_the_files_16_bit_type(qapp, tmp_path):
    # Like the camera's files: 16-bit unsigned integers (stored with BZERO).
    import numpy as np
    from astropy.io import fits
    from nickel_focus.tests.conftest import gaussian_frame
    data = np.round(gaussian_frame(3.0)).astype(np.uint16)
    path = tmp_path / 'n1000.fits'
    fits.writeto(path, data)
    result = list(focus.ArchiveFocusSequence([350.], [path]).step(method='brightest'))[0]
    assert result.raw.dtype == np.uint16, 'the raw data should keep the file\'s 16-bit type'
    assert (result.raw == data).all(), 'the raw data should match the file exactly'
    assert result.frame.dtype == np.float64, 'the displayed frame should be floating point'

    panel = ImagePanel()
    panel.add_result(result)
    assert panel.readout_text(0, 0) == f'0, 0: {data[0, 0]}', \
        'the readout should show the 16-bit value as an integer'


# -- Pointing offset ------------------------------------------------------------

def _pointing_offset_for(result, star=(10., 12.)):
    from nickel_focus import pointing
    return pointing.pointing_offset(star, result.frame.shape, 1, exposure=result.exposure)


def _offset_labels(panel):
    return [t.get_text() for t in panel.ax.texts if '″' in t.get_text()]


def test_p_key_emits_signal_only_when_selection_enabled(qapp, focus_sweep):
    panel = ImagePanel()
    panel.add_result(_step_results(focus_sweep)[0])
    received = []
    panel.pointingStarSelected.connect(lambda x, y: received.append((x, y)))
    marked = []
    panel.sourceSelected.connect(lambda x, y: marked.append((x, y)))

    event = KeyEvent('key_press_event', panel.canvas, 'p')
    event.inaxes = panel.ax
    event.xdata, event.ydata = 42.0, 17.0

    panel._on_key_press(event)
    assert received == [], "'p' should be ignored while selection is disabled"

    panel.set_selection_enabled(True)
    panel._on_key_press(event)
    assert received == [(42.0, 17.0)], \
        "'p' should emit the data coordinates under the cursor once enabled"
    assert marked == [], "'p' should not mark a star for measurement"


def test_pointing_offset_is_drawn_on_every_exposure(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    for r in results[:2]:
        panel.add_result(r)
    offset = _pointing_offset_for(results[1])

    panel.show_pointing_offset(offset)
    assert _offset_labels(panel) == [offset.text()], \
        'the offset should be labeled on the exposure it was measured on'
    line = [l for l in panel.ax.lines if len(l.get_xdata()) == 2]
    assert len(line) == 1 and list(line[0].get_xdata()) == [offset.star[0], offset.center[0]], \
        'a line should join the star and the image center'

    panel.exposure_combo.setCurrentIndex(0)
    assert _offset_labels(panel) == [offset.text()], \
        "the offset should also be drawn on the sequence's other exposures"


def test_pointing_offset_is_not_drawn_on_a_different_size_exposure(qapp, focus_sweep):
    result = _step_results(focus_sweep)[0]
    other = dataclasses.replace(
        result, exposure=result.exposure.with_name('other.fits'), frame=result.frame[:-2, :-2],
        focus_value=result.focus_value + 1
    )
    panel = ImagePanel()
    panel.add_result(result)
    panel.add_result(other)
    panel.show_pointing_offset(_pointing_offset_for(result))

    assert panel.current_result is other, 'setup: the other exposure should be displayed'
    assert _offset_labels(panel) == [] and len(panel.ax.lines) == 0, \
        "the offset should not be drawn on an exposure whose center differs"


@pytest.mark.parametrize('star,below', [((10., 12.), False), ((10., 80.), True)])
def test_offset_label_avoids_the_line(qapp, focus_sweep, star, below):
    # The frames are 101x101, so the center is at y = 50: y = 12 is North of
    # it (above), y = 80 South (below).
    result = _step_results(focus_sweep)[0]
    panel = ImagePanel()
    panel.add_result(result)
    panel.show_pointing_offset(_pointing_offset_for(result, star=star))

    label = next(t for t in panel.ax.texts if '″' in t.get_text())
    dy = label.xyann[1]
    if below:
        assert dy < 0 and label.get_va() == 'top', \
            'for a star South of the center, the label should be below the star'
    else:
        assert dy > 0 and label.get_va() == 'bottom', \
            'for a star North of the center, the label should be above the star'


def test_clearing_the_pointing_offset_removes_it(qapp, focus_sweep):
    result = _step_results(focus_sweep)[0]
    panel = ImagePanel()
    panel.add_result(result)
    panel.show_pointing_offset(_pointing_offset_for(result))

    panel.clear_pointing_offset()
    assert _offset_labels(panel) == [] and len(panel.ax.lines) == 0, \
        'clearing the offset should remove its drawing'

    panel.show_pointing_offset(_pointing_offset_for(result))
    panel.reset()
    panel.add_result(result)
    assert _offset_labels(panel) == [], 'resetting the panel should discard the offset'


def test_current_result_is_the_displayed_exposure(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    assert panel.current_result is None, 'with nothing displayed there should be no result'
    panel.add_result(results[0])
    assert panel.current_result is results[0], 'the displayed exposure should be current'


def test_unmeasured_exposure_is_shown_without_a_box(qapp, focus_sweep):
    result = dataclasses.replace(
        _step_results(focus_sweep)[0], fwhm=None, centroid=None, source_index=None,
        photometry_error='No sources were detected in the image.'
    )
    panel = ImagePanel()
    panel.add_result(result)

    assert len(panel.ax.get_images()) == 1, 'an unmeasured exposure should still be shown'
    assert len(panel.ax.patches) == 0, 'an unmeasured exposure should have no measurement box'


# -- Selected stars ('a' and 'd') ----------------------------------------------

@pytest.mark.parametrize('key,signal', [('a', 'starAdded'), ('d', 'starRemoved')])
def test_a_and_d_keys_emit_only_when_selection_enabled(qapp, focus_sweep, key, signal):
    panel = ImagePanel()
    panel.add_result(_step_results(focus_sweep)[0])
    received = []
    getattr(panel, signal).connect(lambda x, y: received.append((x, y)))

    event = KeyEvent('key_press_event', panel.canvas, key)
    event.inaxes = panel.ax
    event.xdata, event.ydata = 42.0, 17.0

    panel._on_key_press(event)
    assert received == [], f"'{key}' should be ignored while selection is disabled"
    panel.set_selection_enabled(True)
    panel._on_key_press(event)
    assert received == [(42.0, 17.0)], f"'{key}' should emit the coordinates under the cursor"


def _box(centroid, fwhm=3., is_outlier=False):
    import types
    return types.SimpleNamespace(centroid=centroid, fwhm=fwhm, is_outlier=is_outlier)


def test_every_selected_star_is_boxed(qapp, focus_sweep):
    result = _step_results(focus_sweep)[0]
    panel = ImagePanel()
    panel.add_result(result)

    panel.set_star_boxes({result.exposure: [_box((20., 20.)), _box((60., 70.), is_outlier=True)]})

    colors = [to_rgba(p.get_edgecolor()) for p in panel.ax.patches]
    assert colors == [to_rgba('red'), to_rgba('yellow')], \
        'each selected star should be boxed, red or yellow (outlier)'
    assert [t.get_text() for t in panel.ax.texts] == ['Outlier centroid'], \
        'only the outlier should be labeled'


def test_an_exposure_with_no_selected_stars_has_no_boxes(qapp, focus_sweep):
    result = _step_results(focus_sweep)[0]
    panel = ImagePanel()
    panel.add_result(result)

    panel.set_star_boxes({result.exposure: []})

    assert len(panel.ax.patches) == 0, 'with every star removed there should be no boxes'


def test_an_exposure_without_selected_stars_boxes_its_own_source(qapp, focus_sweep):
    results = _step_results(focus_sweep)
    panel = ImagePanel()
    panel.add_result(results[0])

    panel.set_star_boxes({results[1].exposure: [_box((20., 20.))]})

    assert len(panel.ax.patches) == 1, \
        'an exposure the selection does not cover should box its own measured source'
    x, y = panel.ax.patches[0].get_xy()
    width = panel.ax.patches[0].get_width()
    assert (x + width / 2, y + width / 2) == pytest.approx(results[0].centroid), \
        'the box should be on its own measured source'
