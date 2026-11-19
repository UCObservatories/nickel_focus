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
├── ktl_bridge.py          QObject that hands KTL keyword values from ktl's callback thread to the GUI thread
├── model/
│   ├── __init__.py
│   ├── focus_worker.py    QThread that drives a FocusSequence
│   ├── slew_worker.py     QThread that drives a telescope slew
│   └── pointing_offset_worker.py QThread that applies a pointing offset
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
  reimplemented. `gui/model/focus_worker.py`, `gui/model/slew_worker.py`,
  and `gui/model/pointing_offset_worker.py` aren't domain logic; they're the adapters that run the Model's blocking,
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

The Single/Grid/Auto tabs' exposure fields (exposure time, speed, binning)
follow the camera, both ways, and the three tabs always show the same
values:

- **Camera → fields.** The Controller subscribes to `EXPOSURE`, `AMPCONF`,
  and `BINNING` along with the status keywords (see "KTL → Controller"
  below); `Controller._on_exposure_keyword` converts each value with
  `ExposureConfig.setting_value` (which names the speed by its GUI name,
  e.g. `Fast`) and shows it on all three tabs with
  `FocusControlPanel.set_exposure_config` -- only if it differs from what's
  shown, so the broadcast confirming the user's own edit is a no-op. The
  first value is logged as the camera's setting, later ones as changes
  made on the camera (e.g. from another GUI, or the camera adjusting a
  requested exposure time). Speed and binning are each a `RadioChoice` (a
  row of radio buttons side by side); a value that isn't one of its options
  gets a new button, with a warning.
- **Fields → camera.** Clicking a speed or binning button
  (`RadioChoice.chosen`, which fires only for user clicks), or entering an
  exposure time, emits `FocusControlPanel.exposureSettingEdited(key, value)`. The
  exposure time is an `ExposureTimeSpinBox`: like the data-acquisition GUI,
  an edit turns it light red and takes effect only when Enter is pressed;
  until then `get_exposure_config` reports the previous (committed) value.
  `Controller._on_exposure_setting_edited` shows the new value on all three
  tabs and writes it with `cam.cfg.configure(...)` **on the GUI thread**
  (deliberately: settings changes are quick, and blocking the GUI while a
  slow or unresponsive camera finishes is acceptable). A rejected write is
  reported via `_fail`, and the fields go back to the camera's value.
- **`OBJECT`**, not shown among the fields, is written before every
  exposure a sequence or the Single tab takes, as
  `'<name>: focus=<focus>'` (`FocusSequence.exposure_object`; the focus
  read just before the exposure, to one decimal place), capped at
  `FocusSequence.OBJECT_MAX_LENGTH` (68, the most a FITS string value holds)
  by shortening only the name, ending in `'...'`. The name is the
  Slew tab's target **Name** (`slew_target_name_edit`,
  `FocusControlPanel.get_target_name()`): filled in by
  `show_nearest_target`, editable, and cleared when the user types in the
  target RA/Dec fields. `Controller._name_object` sets it on each new
  sequence or standalone exposure, but only if the Slew tab's target
  coordinates are within `Controller.MAX_TARGET_OFFSET_ARCMIN` (2') of the
  telescope's current position (its latest `POCORAA`/`POCODECA`
  broadcast); otherwise, or if the two can't be compared, it logs a warning
  and leaves the object `'unknown'` (`FocusSequence.object_name`'s default,
  which the CLI uses) -- without changing the Slew tab's name.
- The image panel's **Exposure** drop-down lists each exposure as
  `'<file name> - <OBJECT>'` (`ImagePanel.exposure_label`), the object read
  from the file's header (`focus.exposure_object_name`, carried as
  `StepResult.object_name`) -- so replayed exposures show theirs too --
  or `'<file name> - focus=<focus>'` if it has none. The drop-down's width
  doesn't follow its entries: it's sized from
  `ImagePanel.EXPOSURE_MIN_CHARACTERS` (3, so it never sets the window's
  minimum width) and stretches to fill its row, between the "Exposure:"
  label and the stretch drop-down at the right (which has no label, only a
  tooltip). It's an `ElidingComboBox`: the closed box shortens a long entry
  in the middle ("…") to its current width, keeping the file name and focus
  visible; the open list widens to fit the longest entry; and hovering
  shows the full entry as a tooltip.
- Acquire still applies the fields with `configure()` before exposing, as a
  safeguard (normally a no-op). Fields are disabled while anything runs, but
  broadcasts still update them.
- Without the camera service the fields are plain inputs with their
  built-in defaults (visible only with `--test`); an edit still syncs the
  three tabs.

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

The Log tab (`FocusControlPanel.log_widget`, a `LogView`) is a repeated view
of the console's log output, and holds nothing else -- there is no separate
status or step line. It's populated entirely by real `nickel_focus.log`
output: `Controller.__init__` attaches a `QtLogHandler`
(`gui/log_handler.py`) to the `nickel_focus.log` singleton, which forwards
each formatted record into the Log tab via a Qt signal -- safe even when the record is logged from a background
thread (e.g. inside a `FocusWorker`/`SlewWorker`, or from `focus.py`/
`photometry.py` while one is running). The Controller's own `log.info(...)`/
`log.error(...)` calls (including its `_fail(message, exc=None)` helper,
used for every failure, which logs the message with the exception's
traceback, if given) are what drive the Log tab's content: step results,
fitted best focus, completed slews and exposures, nearest targets, and
failures are all simply logged. Where a result also fills in a field, the
Controller calls the view for that alone (`show_best_focus` sets the Single
tab's default, `show_nearest_target` the Slew tab's target). Because `log` is a module-level singleton rather than
one per `Controller`, `Controller.__init__` calls
`log.remove_handlers_of_type(QtLogHandler)` before attaching its own, so a
handler pointing at a since-destroyed window never lingers.

The Log tab shows exactly what the console shows, as the console shows it:
the `QtLogHandler`'s level is the console handler's (`log.sh.level`), which
`scripts/focus_gui.py` sets from `--verbosity` via `ScriptBase.init_log`
(along with `--log_file`/`--log_level`), and its formatter is a new
instance of the console's (`StreamFormatter`, or `DebugStreamFormatter` at
debug level, which adds the calling file/function/line). Those formatters
color the text with ANSI escape codes (`pkg/logger.color_text`), which
`LogView.append_text` renders as Qt text formats (bold, 24-bit color)
instead of showing them. `LogView` uses the system's fixed-width font,
re-sized whenever the view is resized to the largest size that fits
`LogView.TARGET_COLUMNS` (80) characters across (between 6 and 18 pt;
glyph widths step, so it typically lands at 80-85 columns); keeps the most
recent `LogView.MAX_LINES` (5000) lines; stays scrolled to the newest line
unless the user has scrolled up; and is never cleared: like the console,
it's one continuous record of the session. KTL's own log is part of this stream
too, as `ktl: ...` messages (see `services/base.py`). See
`claude/GUI_LOGGING_PLAN.md` for the full design rationale and phased
implementation history.

## 3. Key modules

| Module | Purpose |
| --- | --- |
| `gui/qt.py` | Single point of contact with PySide6; the only module allowed to `import PySide6` directly. |
| `gui/launcher.py` | Builds the `QApplication` (always with Qt's "Fusion" widget style -- the Linux default -- so the layout is the same on every platform, including macOS and the offscreen test platform) and `MainWindow`. Wiring up the `Controller`, showing the window, and starting the Qt event loop happens one layer up, in `scripts/focus_gui.py`'s `NickelFocusGUI.main` (the `nickel_focus_gui` console script's entry point). |
| `gui/controller.py` | Mediates between views and model; owns the shared `NickelPoco`/`NSciCam` services, the active sequence/worker, the telescope handle/slew worker, the pending pointing offset (`pointing.PointingOffset`, from 'p': snapped with `pointing.nearest_source` to a detected centroid in `StepResult.sources`, computed by `pointing.pointing_offset`; cleared once applied, or by a new 'p', a slew, or a new sequence) and its `PointingOffsetWorker`, the photometry method ('brightest', or the star marked with 'm', which persists until a Slew-tab move starts), the selected stars (`selection.StarSelection`: one for the loaded sequence, plotted and refit on the curve after each 'a'/'d', and one for the latest Single exposure, whose mean FWHM the Single tab shows; each starts from the first star, and 'm' or a new sequence starts it again), and the hardware-exclusivity state machine; also decides, once, whether the ktl-driven tabs start out enabled. |
| `gui/log_handler.py` | `QtLogHandler`, a `logging.Handler` that forwards formatted `nickel_focus.log` records into the Log tab via a Qt signal, so records logged from a background thread are delivered safely to the GUI thread. |
| `gui/model/focus_worker.py` | `QThread` subclass that runs one `FocusSequence` operation (step/reanalyze/single exposure) off the GUI thread, reporting progress via signals. A run that takes exposures (a live sequence or a single exposure) reads the camera's `RECORD`, has every exposure set it to `Yes` first (`FocusSequence.record`), and writes the original back when the run ends, however it ends; reanalysis and Replay never touch it. |
| `gui/model/slew_worker.py` | `QThread` subclass that runs one `NickelTelescopePointing.slew_to` call off the GUI thread, since a slew can block for up to five minutes. |
| `gui/model/pointing_offset_worker.py` | `PointingOffsetWorker`, a `QThread` subclass that runs one `NickelTelescopePointing.apply_offset` call (guider offset, then `POCGDOT`) off the GUI thread. |
| `gui/views/main_window.py` | Top-level `QMainWindow`; lays out the panels (status bar stacked above the image panel; splitters between left/right and between the two right-hand panels). |
| `gui/views/status_bar.py` | One row: a Quit button (disabled while anything is running) and five indicators (Slew, Track, On Target, Focus, Exp), each a `StatusIndicator`: a green/yellow/red circle, as set by the Controller, next to left-aligned text. |
| `gui/views/image_panel.py` | Displays collected exposures one at a time, North up (row 0 drawn at the top, y-axis inverted; pixel coordinates unchanged): pan, zoom ('r' recenters to fit the frame), stretch selection, source selection (hover and press 'm') for reanalysis, adding and removing stars on the focus curve ('a'/'d': `starAdded`/`starRemoved`; every selected star boxed via `set_star_boxes`, an exposure it doesn't cover boxing its own measured source), pointing-star selection (hover and press 'p': `pointingStarSelected`; the pending offset is drawn in cyan on every displayed exposure the same size as its own -- star circle, line, center cross, and a label above the star, or below it for a star south of the center -- via `show_pointing_offset`/`clear_pointing_offset`), and a pixel-value readout beside the pointer (a `QLabel` child of the canvas; 0-based pixel, like the centroids, and `StepResult.raw` value); an exposure that couldn't be measured (`StepResult.measured` false; see `focus.measure_exposure`) is shown without a measurement box; `ElidingComboBox` for the exposure drop-down. |
| `gui/views/focus_curve_panel.py` | Live scatter plot of FWHM vs. focus value -- the selected stars' pooled points, all alike, from `set_points(points, binnings)` -- with the fitted quadratic and its vertex (an open black star) once enough points exist. The limits are set by the measured points alone while a sequence runs, so the fit and best focus never widen them; when a sequence's run or remeasurement ends, the Controller fixes them (`fix_limits`, from `StarSelection.plot_limits`: the fitted curve's full FWHM range over the plotted focus values, extended by 30% of that range above and below but not below 0 -- the points' range without a fit -- and the focus values with a 5% margin), and `reset` (a new sequence, or 'm') releases them. The right axis (a `twinx`) is FWHM in arcsec -- `focus.ARCSEC_PER_UNBINNED_PIXEL` (0.1665) times the binning, from each exposure's `CD1_1C`/`CD2_2C` header (`focus.exposure_binning`, `StepResult.binning`) -- when every point has the same known binning (`focus.common_binning`), and otherwise repeats the left axis in pixels. A binning that can't be read is logged as a warning. The Controller's step, single-exposure, and best-fit log messages give FWHMs the same way, via `focus.fwhm_text` (`'3.21 pix (1.07 arcsec)'`, or `'3.21 pix'`); the best fit uses the sequence's common binning (`FocusSequence.binning`, from `FocusSequence.binnings`). |
| `gui/views/focus_control_panel.py` | Tabbed sequence configuration -- Slew (target entry, "Move to Target", below it "Offset by ..." -- `slew_offset_button`, labeled `Offset by —` and disabled until `show_pointing_offset(text)` gives it an offset, enabled only with one pending and nothing running, emitting `applyPointingOffsetRequested` -- and "Find nearest object/pointing/focus star") plus Single (with "Use current focus": `useCurrentFocusRequested`, answered by `Controller.use_current_focus` from the latest `POCSECPA` broadcast, rounded, via `set_single_focus`; Acquire is unchanged and so skips the move when the commanded focus already matches; below it, spanning the row, `single_fwhm_label`, set by `set_single_fwhm(pixels, arcsec)` after each Single exposure or its reanalysis: `FWHM: 3.21 (pix); 1.07 (arcsec)`, with `—` for a value that is unknown)/Grid/Auto/Replay -- and Log/Help tabs; emits the signals that request an action. `set_hardware_tabs_enabled` grays out the four ktl-driven tabs (and switches to Replay) on the Controller's say-so. |

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
  `self.window.control_panel.show_best_focus(best_focus)`) -- views expose a
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
  buttons, while anything is running.
- **KTL → Controller (status bar, current position, exposure fields)**: no
  polling. The Controller subscribes (`KTLService.subscribe`) to the
  keywords behind the status indicators (`POCSTOP`, `POCTRCK`, `POCOT`,
  `POCSECLK` plus the focus value `POCSECPA`, `EXPSTATE`), the Slew tab's
  live position display (`POCORAA`, `POCODECA`), and the exposure fields
  (`EXPOSURE`, `AMPCONF`, `BINNING`; see above). `ktl` calls back on its
  own thread with each broadcast (and each completed read, which also
  delivers the current values at startup);
  `gui/ktl_bridge.py`'s `KTLBridge.relay` re-emits it as
  `keywordChanged(service, keyword, value)`, connected with a *queued*
  connection to `Controller._on_keyword_changed`, which runs on the GUI
  thread. That records the value and updates the affected indicator --
  each mapped to a color by a small pure function in `controller.py` and
  pushed to `StatusBar.set_indicator` -- or, for the position, starts a
  single-shot `QTimer` so redraws happen at most every
  `Controller.POSITION_REDRAW_MS` (200 ms). Changes made elsewhere (e.g. by
  another GUI) therefore show up as soon as they're broadcast, and no
  keyword read runs on the GUI thread except finding the nearest target.
  An indicator with no value yet -- its service isn't connected, or the
  subscription failed (logged as a warning) -- is red; a position that
  can't be parsed shows "—" and is logged once per run of bad values.

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

### Known issues

- **Only Slew-tab moves clear a marked star.** `Controller.move_to_target`
  resets the photometry method to `'brightest'` when it starts a slew, but
  a slew started outside the GUI isn't detected, so a star marked with 'm'
  stays in effect (the new field is measured with the source nearest the
  old pixel position). Detecting it would mean watching a `nickelpoco`
  keyword that reliably signals a new slew; not planned unless it proves
  troublesome for observers. Workaround: mark again, or restart the GUI.

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
  a real display. Its autouse `_settle_qt` fixture runs any pending Qt
  events after each GUI test and then frees that test's widgets, so a
  redraw a window scheduled can't fire on it after it's deleted during a
  later test.
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
| `6227274` | 2026-11-17 | Services monitor their status/display keywords and register heartbeats at construction; checks distinguish "condition false" from "no reply" (`check`/`require`); live heartbeat test. |
| `a37e42c` | 2026-11-17 | Status indicators and current position follow keyword broadcasts (`KTLService.subscribe` → `gui/ktl_bridge.py` → queued signal), replacing the 1 Hz poll; position redraws limited to every 200 ms. |
| `50b703f` | 2026-11-17 | Exposure fields follow the camera both ways: broadcasts update all three tabs (outside changes logged); edits write to the camera at once, with the exposure time light red until Enter; unknown speed/binning values added to the options. |
| `8691767` | 2026-11-17 | Log tab repeats the console -- same level (`nickel_focus_gui` now applies `-v`/`--log_file`/`--log_level`), format, and colors, in a fixed-width font sized to ~80 columns -- keeps 5000 lines, is never cleared, and has no status/step lines; KTL's log included. |
| `894c444` | 2026-11-17 | Speed and binning are rows of radio buttons (`RadioChoice`) instead of drop-downs; an unrecognized camera value gets its own button. |
| `79166ad` | 2026-11-17 | Every exposure from a sequence or the Single tab sets `OBJECT` to the Slew tab's new, editable target Name (or `unknown`, with a warning if the telescope is >2' from the target) and the focus; Exposure drop-down entries show file and object names, at a fixed width, shortened in the middle when long (full text in the list and tooltip); `OBJECT` capped at 68 characters; Recenter button and Stretch label removed, 'r' recenters. |
| `19566b8` | 2026-11-18 | Focus curve: right axis in arcsec (0.1665"/unbinned pixel x binning from `CD1_1C`/`CD2_2C`; pixels if unknown), limits from the measured points only, best focus an open black star; logged FWHMs (each step, single exposures, best fit) in pixels and arcsec when the binning is known. |
| `282eea6` | 2026-11-18 | Single tab: "Use current focus" button fills the focus value with the telescope's current focus. |
| `c11a711` | 2026-11-18 | Image panel shows exposures North up (East left). |
| `c11a711` | 2026-11-18 | Image panel shows the pixel (0-based) and its raw value beside the mouse pointer; `StepResult.raw` added, in the file's own (16-bit) type. |
| `c11a711` | 2026-11-18 | Starting a slew from the Slew tab clears the marked star (back to the brightest); slews from elsewhere listed as a known issue. |
| `c11a711` | 2026-11-18 | GUI tests run pending Qt events and free each test's widgets after it (`_settle_qt`), fixing intermittent "already deleted" errors. |
| `8dfb0f9` | 2026-11-18 | Pointing offsets: 'p' marks the star that should be centered; the offset is drawn on the image and applied with the Slew tab's "Offset by ..." button (new `pointing.py`, `PointingOffsetWorker`). |
| `8dfb0f9` | 2026-11-18 | An exposure whose photometry fails is still displayed (a sequence then stops); the Single tab shows the latest exposure's FWHM. |
| `b276f8c` | 2026-11-18 | Several stars on the focus curve: 'a' adds (or re-matches) and 'd' removes a star, and the pooled data is refit (`selection.StarSelection`); photometry split from cutouts, which only the CLI makes; the image quality is now a true FWHM (2.355 × the moment width), where it was the width itself; the focus curve's limits are fixed when a sequence finishes. |
| *pending* | 2026-11-18 | The GUI records every exposure it takes, restoring the camera's recording setting afterward. |
