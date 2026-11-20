"""Smoke tests for :mod:`nickel_focus.gui.launcher`'s scaffolding."""
import pytest

from nickel_focus.gui import launcher
from nickel_focus.gui.qt import QtCore


def test_build_window_opens_without_error(qapp):
    window = launcher.build_window()
    window.show()
    qapp.processEvents()

    assert window.windowTitle() == 'Nickel Focus GUI', 'window title not set as expected'

    window.close()


def test_build_window_fits_within_the_screen(qapp):
    # A fixed 1200x600 window can be larger than the available screen
    # (excluding docks/taskbars), pushing its resize handles off screen
    # and leaving the user stuck -- `_size_to_screen` should never
    # request a size larger than what's actually available. But no
    # request can shrink the window below what its content genuinely
    # requires (`minimumSizeHint`) -- e.g. the offscreen test platform's
    # synthetic screen is a mere 800px wide, narrower than this app's
    # real, content-driven minimum width -- so the bound checked here is
    # whichever of the two is larger, rather than a hard "always fits
    # this one screen" expectation with no real content behind it. A
    # regression in the clamping logic itself (e.g. `_size_to_screen`
    # never being called) would still show up here, since the window
    # would then exceed *both* bounds, not just the screen-specific one.
    window = launcher.build_window()
    window.show()
    qapp.processEvents()

    available = window.screen().availableGeometry()
    assert window.width() <= max(available.width(), window.minimumSizeHint().width()), \
        'window should not be wider than the screen, unless its content cannot shrink further'
    assert window.height() <= max(available.height(), window.minimumSizeHint().height()), \
        'window should not be taller than the screen, unless its content cannot shrink further'

    window.close()


def test_control_panel_sits_directly_in_the_splitter(qapp):
    # Each tab page scrolls internally instead (see
    # `FocusControlPanel._tab_widget`) -- wrapping the whole panel in
    # another, outer scroll area on top of that would be redundant and
    # would let the tab bar itself scroll out of view along with a tall
    # page's content, which is exactly what an outer scroll area used to
    # do here.
    window = launcher.build_window()
    window.show()
    qapp.processEvents()

    right = window.centralWidget().widget(1)
    assert right.widget(1) is window.control_panel, \
        'the control panel should sit directly in the splitter, not wrapped in a scroll area'

    window.close()


def test_control_panel_does_not_snap_to_an_unrelated_width_when_squeezed(qapp):
    # QScrollArea.setWidgetResizable(True) has a real bug: if its
    # viewport is ever squeezed narrower than the scrolled widget's own
    # minimum width, the widget doesn't get a horizontal scrollbar as
    # you'd expect -- it snaps to some unrelated, much larger width (seen
    # in practice: a ~340px-minimum panel jumping to ~640px) and gets
    # stuck there. That risk no longer applies at this level now that the
    # control panel isn't wrapped in an outer scroll area (see
    # test_control_panel_sits_directly_in_the_splitter) -- this confirms
    # squeezing it instead just shrinks it toward its own minimum.
    window = launcher.build_window()
    window.show()
    qapp.processEvents()

    central = window.centralWidget()
    central.setSizes([central.width() - 50, 50])
    qapp.processEvents()

    panel = window.control_panel
    assert panel.width() < panel.sizeHint().width() / 2, \
        'squeezing below its sizeHint should shrink the panel toward its floor, not snap wider'

    window.close()


def test_control_panel_gets_a_typical_tab_sized_initial_share(qapp):
    # `right`'s very first layout hands the control panel just enough
    # height for a typical (non-Help) tab (see
    # `FocusControlPanel.preferred_height_excluding_help`) and gives the
    # curve panel everything else, rather than the panel claiming most of
    # the window the way an unconfigured splitter's default share would.
    window = launcher.build_window()
    window.show()
    qapp.processEvents()

    right = window.centralWidget().widget(1)
    panel = window.control_panel
    assert right.sizes()[1] == pytest.approx(panel.preferred_height_excluding_help(), abs=5), \
        "the control panel's initial height share should match its preferred height"
    for tab_name in ('slew_tab', 'single_tab', 'grid_tab', 'auto_tab', 'replay_tab',
                      'log_tab'):
        tab = getattr(panel, tab_name)
        assert right.sizes()[1] >= tab.widget().minimumSizeHint().height(), \
            f'{tab_name} should fit within the initial share without scrolling'

    window.close()


def test_status_bar_sits_directly_above_the_image_panel(qapp):
    window = launcher.build_window()
    window.show()
    qapp.processEvents()

    assert window.status_bar.parentWidget() is window.image_panel.parentWidget(), \
        'the status bar should share the left column with the image panel'
    assert window.status_bar.geometry().bottom() < window.image_panel.geometry().top(), \
        'the status bar should be above the image panel'
    window.close()


def test_status_bar_lines_up_with_and_sits_close_to_the_image_panel_rows(qapp):
    window = launcher.build_window()
    window.show()
    qapp.processEvents()
    status_bar, image_panel = window.status_bar, window.image_panel

    def window_rect(widget):
        return QtCore.QRect(widget.mapTo(window, QtCore.QPoint(0, 0)), widget.size())

    quit_rect = window_rect(status_bar.quit_button)
    exp_rect = window_rect(status_bar.indicators['exp'])
    combo_row = image_panel.layout().itemAt(0).layout()
    label_rect = window_rect(combo_row.itemAt(0).widget())
    stretch_rect = window_rect(image_panel.stretch_combo)

    assert quit_rect.left() == label_rect.left(), 'left edges should line up'
    assert exp_rect.right() == stretch_rect.right(), 'right edges should line up'
    gap = window_rect(image_panel.exposure_combo).top() - quit_rect.bottom()
    assert gap <= 4, f'the status bar should sit tight against the Exposure row (gap {gap}px)'
    window.close()


def test_build_app_uses_the_fusion_style(qapp):
    # Fusion is the default on Linux (where the GUI is deployed); forcing it
    # everywhere keeps the layout the same on other platforms, e.g. macOS.
    assert launcher.build_app().style().name().lower() == 'fusion', \
        'the app should use the Fusion style'
