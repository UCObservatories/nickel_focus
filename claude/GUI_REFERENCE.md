# GUI Reference

Developer-facing reference for the `gui` package: what it is, how it's put
together, and how its pieces talk to each other. This document tracks the
implementation as it actually exists and should be kept up to date as the
GUI changes (e.g. the pointing workflow, the eventual package restructuring).

It is a companion to, not a replacement for, `claude/GUI_DESIGN.md` (the
original design spec, left as-is) and `claude/GUI_IMPLEMENTATION.md` (the
phase-by-phase build log).

## 1. Summary

`gui` is a PySide6 (Qt) desktop application wrapping `nickel_focus.focus`'s
telescope focus-sequence automation and `nickel_focus.slew`'s
telescope-pointing control for the Nickel 1-m telescope at Lick
Observatory. It lets an observer slew the telescope to a target (entered
directly, or found as the nearest pointing/focusing star in a starlist,
mirroring `scripts/slew_to_nearest.py`), configure and run a focus
sequence (a fixed grid of focus values, or an adaptive search), inspect
each exposure and its measured FWHM as it comes in, watch the live focus
curve, and replay/reanalyze previously collected exposures from disk --
all without using the `focus.py`/`slew_to_nearest.py` CLIs directly.

The GUI is strictly a View+Controller layer on top of the existing Model
code in `nickel_focus/` (`focus.py`, `exposure.py`, `slew.py`,
`starlist.py`, `photometry.py`, `quadratic.py`). It adds no new domain logic of its own;
every hardware call, exposure, slew, fit, and sequence rule lives in that
Model code, which is independently usable from the CLI
(`nickel_focus/scripts/`) with no Qt installed at all.

Without a live `ktl` connection, the Slew/Single/Grid/Auto tabs can only
ever fail, so they come up grayed out and the window opens on the Replay
tab -- the only one that works from archived exposures alone -- instead;
see §2's Controller bullet and §4.

## 2. Directory structure and design

```
gui/
├── __init__.py            package docstring only; no Qt import
├── qt.py                  the only module that imports PySide6
├── launcher.py            builds the QApplication/MainWindow (entry point: `nickel_focus_gui`)
├── controller.py          wires views to the model; owns app state
├── log_handler.py         logging.Handler that forwards nickel_focus.log records into the Log tab
├── model/
│   ├── __init__.py
│   ├── focus_worker.py    QThread that drives a FocusSequence
│   └── slew_worker.py     QThread that drives a telescope slew
└── views/
    ├── __init__.py
    ├── main_window.py       top-level window and layout
    ├── status_bar.py        Quit button and hardware status indicators
    ├── image_panel.py       exposure display/pan/zoom/source selection
    ├── focus_curve_panel.py live FWHM-vs-focus plot
    └── focus_control_panel.py Slew/Single/Grid/Auto/Replay tabs, log, help
```

This is a fairly conventional Model-View-Controller split, with one Qt-
specific wrinkle:

- **Model** is `focus.py`/`exposure.py`/`slew.py` and friends, plus the KTL
  service classes in `services/` they're built on -- reused as-is, not
  reimplemented. `gui/model/focus_worker.py` and `gui/model/slew_worker.py`
  aren't domain logic; they're the adapters that run the Model's blocking,
  synchronous methods (`FocusSequence.step`/`.reanalyze`/
  `.take_single_exposure`, `NickelTelescopePointing.slew_to`) on a
  background `QThread` so a real exposure (seconds to tens of seconds) or
  a slew (up to five minutes) doesn't freeze the GUI's event loop.
- **View** is `gui/views/` -- a one-row `StatusBar` (Quit button and
  hardware status indicators) above `ImagePanel` on the left, and
  `FocusCurvePanel` above `FocusControlPanel` on the right, composed
  inside `MainWindow`;
  `FocusControlPanel` itself holds the Slew/Single/Grid/Auto/Replay tabs
  plus Log/Help. Views only display state and emit Qt signals for
  user actions (button clicks, a key press, a picked drop-down entry);
  they never import `focus`/`slew` or talk to `FocusWorker`/`SlewWorker`
  directly.
- **Controller** (`gui/controller.py`) is the only module that imports
  both the views and the model. It owns one shared service object per KTL
  service -- `poco` (`services.NickelPoco`, the telescope) and `cam`
  (`services.NSciCam`, the camera) -- and passes them to every sequence or
  single exposure it builds; it also owns the currently active
  `focus.FocusSequence`/`FocusWorker` and the `slew.NickelTelescopePointing`
  handle/`SlewWorker` (if any). It connects each view's signals to its own
  handler methods, and implements the "hardware exclusivity" rule: only
  one operation (a running sequence, a single exposure, or a slew) may be
  active at a time. Finding the nearest target is exempt from that rule --
  it's a fast, synchronous `ktl` read plus a starlist search, not a move.

Each service is constructed once, at startup (`Controller._connect_service`):
without `ktl` it is silently `None`; if `ktl` is available but the service
can't be reached (e.g. a priming read times out), it is `None` and a
warning is logged. Every action that needs one checks for `None` and reports
a clear failure.

The Controller also decides, once, at construction, whether the
Slew/Single/Grid/Auto tabs are enabled at all: `self.ktl_available =
services.ktl_available()`, passed to
`FocusControlPanel.set_hardware_tabs_enabled`, which grays out those four
tabs and switches to Replay when it's `False`. A `force_enable_hardware_tabs`
constructor flag skips *only* that graying -- every action on those tabs
still fails exactly as it would otherwise, checking `poco`/`cam`
itself -- so a developer with no `ktl` connection can still see and click
through them; it's wired to `nickel_focus_gui`'s suppressed `--test` flag
(§4), never set any other way.

At construction, the Controller also starts the Single/Grid/Auto tabs'
exposure fields (exposure time, speed, binning) at the camera's current
settings, read via `cam.cfg.current_settings()` (`services.nscicam.ExposureConfig`, which
reports the speed by its GUI name, e.g. `Fast`) and applied with
`FocusControlPanel.set_exposure_config`. A failed read, or a speed/binning that isn't one of the drop-down options,
is logged as a warning and leaves those fields unchanged. Without the camera
service the fields keep their built-in defaults (visible only with `--test`).

`gui/qt.py` exists so exactly one module fails, with one clear message, if
PySide6 isn't installed -- every other `gui` module imports Qt classes from
here (`from nickel_focus.gui.qt import QtWidgets`) rather than importing
PySide6 directly. `scripts/` never imports `gui.qt` and must never need to:
installing the base package is enough to run the CLI with no Qt involved,
and the `gui` extra in `pyproject.toml` adds the one extra dependency the
GUI needs.

`ktl` (the KTL package used to talk to real telescope/camera hardware) gets
the same single-entry-point treatment, but outside `gui/`, in
`nickel_focus/services/base.py`, since it's needed by the Model layer
independent of whether the GUI is even installed. `services/base.py` is the
only module that imports `ktl`; everything else uses the service classes
built on its `KTLService` (`services/nickelpoco.py`'s `NickelPoco` and
`services/nscicam.py`'s `NSciCam`, the latter with the camera's
`ExposurePath`/`ExposureConfig` helpers), which take and return plain
values and raise only `services/base.py`'s own exceptions (`KTLError`,
`KTLTimeoutError`, `KTLReadOnlyError`, `KTLUnavailableError`). Every read
has a timeout (`DEFAULT_READ_TIMEOUT`, 1 s; to be confirmed at the
telescope); writes don't yet. Each service monitors its status and
displayed keywords from construction on (priming each with a timed read),
and registers a heartbeat keyword (`POCHEART`, `DISP0CLK`; provisional
30 s periods) so `ktl` reconnects after a dispatcher restart. Conditions
are checked with `KTLService.check`, which returns `True`, `False`, or
`None` when the service doesn't reply; the device classes turn these into a
`ValueError` (condition false) or a `KTLTimeoutError` ("No reply from ...")
with `services.require`, so a failure says which it was. The device classes (`focus.Focus`,
`exposure.Exposure`, `slew.NickelTelescopePointing`) take a service
object rather than touching keywords themselves; the "camera idle before a
focus move" check lives in `FocusSequence.set_focus`, which holds both
devices. The two entry points differ in how they treat their dependency
being missing, though: `gui/qt.py` *hard*-fails (raises `ImportError` with
a clear message), since the GUI extra requires PySide6 to do anything at
all, whereas `services/base.py` *soft*-fails -- if `import ktl` raises any
`ImportError` (not installed, or installed but broken) it warns once and
sets `ktl = None` -- because `ktl` being unavailable is expected and
supported: the CLI's archive/replay path and `focus.ArchiveFocusSequence`
must keep working with no live `ktl` connection at all. Constructing a
service then raises `KTLUnavailableError` (a `RuntimeError`), and
`services.ktl_available()` reports it. Nothing imports `services` unless it
needs hardware, so a replay-only CLI run never imports `ktl`.

Views hold no reference to each other or to the Controller. All
cross-panel communication (e.g. a new exposure updating both the image
display and the focus curve) is mediated by the Controller reacting to one
signal and calling methods on multiple views -- no view ever calls another
view's methods directly.

The Log tab's scrolling history (`FocusControlPanel.log_widget`) is
populated entirely by real `nickel_focus.log` output, not by view methods
appending text directly: `Controller.__init__` attaches a `QtLogHandler`
(`gui/log_handler.py`) to the `nickel_focus.log` singleton, formatted with
`pkg/logger.GuiFormatter`, which forwards each log record into the Log tab
via a Qt signal -- safe even when the record is logged from a background
thread (e.g. inside a `FocusWorker`/`SlewWorker`, or from `focus.py`/
`photometry.py` while one is running). The Controller's own `log.info(...)`/
`log.error(...)` calls (including its `_fail(message, exc=None)` helper,
used everywhere a failure needs to be both logged -- with the exception's
traceback, if given -- and shown) are what actually drive the Log tab's
content. For each message, the Controller builds the text once, logs it,
and passes the same string to the view (`FocusControlPanel.show_status`/
`update_step`, or `show_best_focus`/`show_nearest_target`, which also fill
in fields), which only updates the status/step labels. Because `log` is a module-level singleton rather than
one per `Controller`, `Controller.__init__` calls
`log.remove_handlers_of_type(QtLogHandler)` before attaching its own, so a
handler pointing at a since-destroyed window never lingers. See
`claude/GUI_LOGGING_PLAN.md` for the full design rationale and phased
implementation history.

## 3. Key modules

| Module | Purpose |
| --- | --- |
| `gui/qt.py` | Single point of contact with PySide6; the only module allowed to `import PySide6` directly. |
| `gui/launcher.py` | Builds the `QApplication` (always with Qt's "Fusion" widget style -- the Linux default -- so the layout is the same on every platform, including macOS and the offscreen test platform) and `MainWindow`. Wiring up the `Controller`, showing the window, and starting the Qt event loop happens one layer up, in `scripts/focus_gui.py`'s `NickelFocusGUI.main` (the `nickel_focus_gui` console script's entry point). |
| `gui/controller.py` | Mediates between views and model; owns the shared `NickelPoco`/`NSciCam` services, the active sequence/worker, the telescope handle/slew worker, and the hardware-exclusivity state machine; also decides, once, whether the ktl-driven tabs start out enabled. |
| `gui/log_handler.py` | `QtLogHandler`, a `logging.Handler` that forwards formatted `nickel_focus.log` records into the Log tab via a Qt signal, so records logged from a background thread are delivered safely to the GUI thread. |
| `gui/model/focus_worker.py` | `QThread` subclass that runs one `FocusSequence` operation (step/reanalyze/single exposure) off the GUI thread, reporting progress via signals. |
| `gui/model/slew_worker.py` | `QThread` subclass that runs one `NickelTelescopePointing.slew_to` call off the GUI thread, since a slew can block for up to five minutes. |
| `gui/views/main_window.py` | Top-level `QMainWindow`; lays out the panels (status bar stacked above the image panel; splitters between left/right and between the two right-hand panels). |
| `gui/views/status_bar.py` | One row: a Quit button (disabled while anything is running) and five indicators (Slew, Track, On Target, Focus, Exp), each a `StatusIndicator`: a green/yellow/red circle, as set by the Controller, next to left-aligned text. |
| `gui/views/image_panel.py` | Displays collected exposures one at a time: pan, zoom, stretch selection, and source selection (hover and press 'm') for reanalysis. |
| `gui/views/focus_curve_panel.py` | Live scatter plot of FWHM vs. focus value, with the fitted quadratic and its vertex once enough points exist. |
| `gui/views/focus_control_panel.py` | Tabbed sequence configuration -- Slew (target entry, "Move to Target", "Find nearest object/pointing/focus star") plus Single/Grid/Auto/Replay -- and Log/Help tabs; emits the signals that request an action. `set_hardware_tabs_enabled` grays out the four ktl-driven tabs (and switches to Replay) on the Controller's say-so. |

### Lines of communication

```
                        ┌─────────────────────────┐
                        │        MainWindow        │
                        │  (layout)                │
                        └───────────┬───────────────┘
                                    │ owns
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                      ▼
      ImagePanel            FocusCurvePanel         FocusControlPanel
   (exposure display)       (FWHM vs focus)      (Slew/Single/Grid/Auto/
              │                     ▲              Replay tabs, log, help)
    sourceSelected            add_result /                │
    (Signal)                  reset              startRequested / stopRequested /
              │                     ▲            takeSingleExposureRequested /
              ▼                     │           moveToTargetRequested / findNearest*Requested
              └─────────────► Controller ◄──────────────────┘
                                    │
                    ┌───────────────┴────────────────┐
              builds / starts /                 builds / starts
                 stops                                 │
                    ▼                                  ▼
            FocusWorker (QThread)             SlewWorker (QThread)
     stepComplete / focusSequenceFinished /    slewFinished / slewFailed
     focusSequenceFailed / singleExposureFinished
                    │                                  │
                    ▼                                  ▼
         focus.py (FocusSequence and            slew.py (NickelTelescopePointing/
         subclasses; Model code)                find_nearest_target; Model code)
```

- **Views → Controller**: exclusively via Qt signals
  (`FocusControlPanel.startRequested`/`stopRequested`/
  `takeSingleExposureRequested`/`moveToTargetRequested`/
  `findNearestObjectRequested`/`findNearestPointingRequested`/
  `findNearestFocusRequested`, `ImagePanel.sourceSelected`,
  `StatusBar.quitRequested`), connected
  once in `Controller.__init__`. Views never call Controller methods
  directly.
- **Controller → Views**: exclusively via direct method calls (e.g.
  `self.window.image_panel.add_result(result)`,
  `self.window.control_panel.show_status(message)`) -- views expose a
  small "public API used by the Controller" for this, documented at the
  top of each view's method list.
- **Controller → FocusWorker**: the Controller constructs a new
  `FocusWorker` per run (`_start_focus_worker`), connects its signals to
  its own `_on_*` handlers, and calls `.start()`.
  `FocusWorker.request_stop()` is the only method called on a running
  worker.
  FocusWorker → Controller: Qt signals only
  (`stepComplete`, `focusSequenceFinished`, `focusSequenceFailed`,
  `singleExposureFinished`, plus `QThread`'s built-in `finished`), safely
  delivered across the thread boundary by Qt's queued-connection
  machinery.
- **FocusWorker → Model**: ordinary synchronous Python calls into
  `focus.FocusSequence` (`.step()`, `.reanalyze()`, `.take_single_exposure()`,
  `.fit_best_focus()`) -- the Model has no idea it's being driven from a
  background thread.
- **Controller → SlewWorker**: same pattern as `FocusWorker`, one level
  simpler -- `move_to_target` constructs a `SlewWorker` per slew, connects
  its signals, and calls `.start()`. There is no stop/interrupt for a
  slew already in progress. SlewWorker → Controller: `slewFinished`/
  `slewFailed` (plus `finished`), the same queued-connection signals.
  `find_nearest_object`/`find_nearest_pointing`/`find_nearest_focus`, by
  contrast, run synchronously on the GUI thread with no worker at all --
  a `ktl` position read plus an in-memory starlist search is fast enough
  not to need one.
- **SlewWorker → Model**: one call, `slew.NickelTelescopePointing.slew_to(ra, dec)`.
- Not shown above: `MainWindow` also owns the `StatusBar`, whose
  `quitRequested` signal the Controller connects to `quit()` (closing the
  window); `Controller._set_running` disables Quit, along with the action
  buttons, while anything is running. The Controller also owns a `QTimer` that polls the hardware
  once a second, independent of whatever else is running:
  `telescope.current` for the Slew tab's live position display, and the
  keywords behind the status indicators (`POCSTOP`, `POCTRCK`, `POCOT`,
  `POCSECLK` plus the focus value, `EXPSTATE`), read through `poco`/`cam`,
  each mapped to a color by a small pure function in `controller.py` and
  pushed to `StatusBar.set_indicator`. Without either service the timer
  never starts and every indicator stays red; a failed read (including a
  timeout) shows red and is logged once per streak of failures. The reads
  run on the GUI thread, so an unresponsive service stalls the GUI for up to
  1 s per read on each poll.

## 4. Usage

```
nickel_focus_gui
```

Requires PySide6 (`pip install .[gui]`). Enter a target RA/Dec (or find the
nearest object/pointing/focus star from a starlist) and press Move to
Target on the Slew tab; configure a sequence on the Single, Grid, Auto, or
Replay tab and press Acquire/Load; watch progress on the image panel,
focus curve, and Log tab; press Interrupt to stop a running Grid/Auto
sequence between steps. A slew and a focus sequence/exposure can't run at
once. The status bar above the image shows telescope/camera readiness at a
glance (green ready, yellow needs attention, red error or unavailable), and
its Quit button closes the GUI (disabled while a sequence, exposure, or
slew is running). See the in-app Help tab for a short per-tab usage summary.

With no live `ktl` connection, the Slew/Single/Grid/Auto tabs come up
grayed out (they can only fail without one) and the window opens on
Replay instead. A developer working on the GUI's layout with no `ktl`
connection can pass the undocumented `nickel_focus_gui --test` flag to
leave those tabs enabled anyway -- every action on them still fails the
same way; only the graying is skipped.

For installation (including the `gui` extra) and running the test suite,
see the top-level `README.md`.

## 5. Testing

The test suite (`nickel_focus/tests/`, including `tests/gui/`) is designed
to run -- and pass -- identically whether or not `ktl` or PySide6 are
installed, and to never manipulate real telescope/camera hardware no
matter which is true:

- `tests/gui/conftest.py` skips the entire `tests/gui/` subtree via
  `pytest.importorskip('PySide6')` if PySide6 isn't installed, and forces
  the offscreen Qt platform plugin so whatever GUI tests do run never need
  a real display.
- `tests/conftest.py`'s autouse `no_ktl` fixture sets `services.base.ktl`
  -- the package's only reference to `ktl` -- to `None` before every test,
  regardless of whether the real `ktl` package happens to be importable in
  the environment running the suite. Every service class then raises
  `KTLUnavailableError` on construction, so no test can reach a real
  keyword.
- Tests that need to exercise the "live hardware" code paths (stepping a
  `GridFocusSequence`/`AutomatedFocusSequence`, taking a single exposure,
  slewing, the status indicators) request the `fake_hardware`/
  `fake_telescope` fixtures instead. These run after `no_ktl` and install a
  keyword-level fake `ktl` (`tests/fake_ktl.py`) at the same place, so the
  real service and device classes run unchanged. Its `Simulator` defines the
  `nickelpoco`/`nscicam` keywords and makes writes act like the hardware:
  commanding the focus moves and re-locks the secondary, commanding a slew
  moves the telescope, and `EXPOSE = Go` steps `EXPSTATE` through an
  exposure and writes a synthesized FITS frame. Its keywords record their
  reads (with timeouts) and writes, and can be made unresponsive.

Net effect: the same test run gives the same pass/fail result on a
developer laptop with neither dependency installed and on a machine at the
telescope with both installed and a live KTL dispatcher running -- and it
never commands the telescope or camera in either case.

**Live tests.** A few tests (`tests/test_ktl_live.py`, marked `ktl_live`)
check the real services at the telescope -- that `NickelPoco` and `NSciCam`
construct (every monitored keyword exists and answers), that the device
classes construct on them, that their values read, and that each service's
heartbeat keyword changes within two heartbeat periods. They run only when
requested:

```
pytest nickel_focus/tests --ktl-live               # normal suite plus the live tests
pytest nickel_focus/tests --ktl-live -m ktl_live   # live tests only
```

Without `--ktl-live` they are skipped, so `pytest`, `tox`, and CI never run
them. With it, they need a live `ktl` connection, and fail (rather than
skip) without one. They **never change a keyword value**: every service
they use is constructed with `read_only=True`, so any write raises
`KTLReadOnlyError` before reaching `ktl` (the ordinary suite tests this
guard). `no_ktl` leaves `ktl` alone only for these tests.

## 6. Version history

This column will eventually hold git tags; until the project starts
tagging releases, it holds the short commit hash current as of each entry.

| Version | Date | Summary |
| --- | --- | --- |
| `c6058e8` | 2026-08-24 | Initial reference document, covering the GUI as of the code-quality/docstring pass following Phase 5. |
| `8222c32` | 2026-09-01 | Updated the document to reflect the Slew tab, merged earlier without a doc update. |
| `c702eb1` | 2026-09-01 | Documented the `nickel_focus/pkg/ktl.py` single entry point (mirroring `gui/qt.py`) and added the "Testing" section covering the autouse `no_ktl` fixture, which together let the test suite pass regardless of whether `ktl` is installed while never touching real hardware. |
| `1e47148` | 2026-09-01 | Documented that the Slew/Single/Grid/Auto tabs gray out (and the window opens on Replay) with no live `ktl` connection, and the suppressed `nickel_focus_gui --test` flag that overrides it for development. |
| `73141ca` | 2026-09-01 | Documented that the Log tab is now driven by real `nickel_focus.log` output via `gui/log_handler.py`'s `QtLogHandler`, rather than by view methods appending text directly; see `claude/GUI_LOGGING_PLAN.md` for the full logging-integration design and phased implementation history. |
| `034a374` | 2026-11-16 | Status text built once in the Controller (`show_status`); 'm'-key selection and curve-panel API corrected; install/testing now point to `README.md`. |
| `33f7060` | 2026-11-16 | Added `StatusBar` (Quit + Slew/Track/On Target/Focus/Exp colored-circle indicators) above the image panel (Quit disabled while running); image anchored to the top of its canvas; Fusion style forced on all platforms. |
| `945b2ab` | 2026-11-17 | Exposure control (`ExposurePath`/`ExposureConfig`/`Exposure`) moved from `focus.py` to its own `exposure.py` module; exposure fields start at the camera's current settings. |
| `e560841` | 2026-11-17 | Removed the Options tab and settings persistence. |
| `4a21d56` | 2026-11-17 | KTL access moved into `services/` (`NickelPoco`/`NSciCam` on `KTLService`, the only `ktl` import; 1 s read timeouts); Controller shares one service of each; tests run on a keyword-level fake `ktl`; `--ktl-live` tests. |
| *pending* | 2026-11-17 | Services monitor their status/display keywords and register heartbeats at construction; checks distinguish "condition false" from "no reply" (`check`/`require`); live heartbeat test. |
