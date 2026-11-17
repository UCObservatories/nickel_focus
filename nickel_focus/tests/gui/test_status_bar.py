"""Tests for :mod:`gui.views.status_bar`."""
from nickel_focus.gui.views.status_bar import StatusBar


def test_quit_button_then_indicators_in_order(qapp):
    bar = StatusBar()
    layout = bar.layout()
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]

    assert widgets[0] is bar.quit_button, 'Quit should come first'
    assert [w.text() for w in widgets[1:]] == ['Slew', 'Track', 'On Target', 'Focus', 'Exp'], \
        'the indicators should follow Quit, in the requested order'


def test_indicators_start_red(qapp):
    bar = StatusBar()

    for key, label in bar.indicators.items():
        assert label.property('state') == 'error', f'{key} should start red until polled'


def test_set_indicator_sets_state_color_and_text(qapp):
    bar = StatusBar()

    bar.set_indicator('focus', 'ok', '345.2')

    label = bar.indicators['focus']
    assert label.property('state') == 'ok'
    assert label.text() == '345.2'
    assert StatusBar.STATE_COLORS['ok'] in label.dot.styleSheet(), 'the circle should be green'


def test_set_indicator_without_text_restores_the_default(qapp):
    bar = StatusBar()
    bar.set_indicator('focus', 'ok', '345.2')

    bar.set_indicator('focus', 'error')

    assert bar.indicators['focus'].text() == 'Focus'


def test_quit_button_emits_quit_requested(qapp):
    bar = StatusBar()
    received = []
    bar.quitRequested.connect(lambda: received.append(True))

    bar.quit_button.click()

    assert received == [True]


def test_set_running_disables_and_reenables_quit(qapp):
    bar = StatusBar()

    bar.set_running(True)
    assert not bar.quit_button.isEnabled(), 'Quit should be disabled while something runs'

    bar.set_running(False)
    assert bar.quit_button.isEnabled(), 'Quit should come back once nothing is running'


def test_indicator_circle_sits_left_of_left_aligned_text(qapp):
    bar = StatusBar()
    bar.resize(800, bar.sizeHint().height())
    bar.show()
    qapp.processEvents()

    for key, indicator in bar.indicators.items():
        dot, label = indicator.dot.geometry(), indicator.label.geometry()
        assert dot.width() == dot.height(), f'{key}: the circle should be square (round)'
        assert dot.left() == 0, f'{key}: the circle should start at the left edge'
        assert 0 < label.left() - dot.right() <= 6, f'{key}: the text should sit right next to the circle'
        assert label.right() < indicator.width() - 10, \
            f'{key}: extra width should fall to the right of the text, not center it'
    bar.close()
