# KTL Interface Refactor -- Plan

**Status:** proposed, not started (2026-11-17).

A plan to restructure how `nickel_focus` uses the `ktl` package, so that the
focus GUI stays in step with the telescope and camera when observers change
them from other tools, and so that keyword access is organized by KTL service
instead of being spread across device classes -- and, alongside it, to
remove the GUI's Options tab and settings persistence, which the refactor
makes largely redundant. Background on `ktl` and on
the keywords we use is in `claude/KTL_REFERENCE.md` (cited below as "REF
§n"); proposals for `ktl` itself are in `claude/KTL_SUGGESTIONS.md`.

## 1. Goals

1. **Reflect external changes.** Observers run other GUIs (e.g. the
   data-acquisition GUI) alongside the focus GUI. Any change they make to a
   keyword the focus GUI displays must show up in the focus GUI promptly.
2. **Exposure settings follow the camera, both ways.** The focus GUI's
   exposure time, read speed, and binning fields always show the camera's
   current values, and editing them in the focus GUI changes the camera
   -- so the data-acquisition GUI reflects that change too, provided it
   tracks the same keywords (not something we control).
3. **Organize keyword access by service.** Replace per-device keyword
   handling with one object per KTL service (`nickelpoco`, `nscicam`) that
   applies our `ktl` usage rules in one place, and make the device classes'
   own logic testable.
4. **Remove the Options tab.** Its one setting, "Remember settings between
   sessions", saw little use in testing, and with the exposure fields
   following the camera, most of what it saved no longer needs saving.
   Remove the tab and settings persistence altogether (§3.8).

## 2. Constraints

- **Named `ktl` methods only** (REF intro, REF §3.8): `ktl.cache`, and
  `Keyword.read/write/monitor/waitFor/callback`, `Service.heartbeat`,
  `ktl.logger`/`loglevel`. No `keyword[...]` access, no internals. Anything
  missing is proposed in `KTL_SUGGESTIONS.md`.
- **No `ktl` in the normal test suite.** The suite must keep passing, and
  never touch hardware, with or without `ktl` installed
  (`GUI_REFERENCE.md` §5). The only exception is the small set of live
  tests (§3.9), which run only when explicitly requested and never change
  a keyword value.
- **Docs track the code.** Docstrings describe current code only;
  `GUI_REFERENCE.md` and `KTL_REFERENCE.md` are updated with each phase;
  `GUI_IMPLEMENTATION.md` gets an entry per phase.
- **Behavior-preserving first.** The structural change (phase 1) should
  not change behavior, so it can be verified on its own before behavior
  changes land on top of it.

## 3. Design

### 3.1 Service classes

A new module (proposed: `nickel_focus/ktl_service.py`) with:

```python
class KTLService:
    """One KTL service: its keyword handles and our ktl usage rules."""
    service = None      # e.g. 'nickelpoco'
    monitored = ()      # keywords monitored, with a blocking priming read, at construction
    heartbeat = None    # keyword registered via Service.heartbeat

    def __init__(self): ...         # KTLUnavailableError if no ktl (§3.2); monitor; heartbeat
    def _keyword(self, name): ...   # ktl.cache(self.service, name); private (§3.2)
    def read(self, name, binary=False, timeout=DEFAULT_TIMEOUT): ...
    def write(self, name, value, binary=False, timeout=DEFAULT_TIMEOUT): ...
    def check(self, name, expression, timeout):
        """waitFor; on failure, a read(timeout=...) to tell 'false' from
        'no reply' (REF §3.4). Returns True, False, or None (no reply)."""
    def subscribe(self, names, function): ...   # Keyword.callback; see §3.5

class NickelPoco(KTLService):   # service = 'nickelpoco'
    # named queries, e.g. movement_allowed(), tracking(), on_target(),
    # pointing(), secondary_position(), secondary_locked()

class NSciCam(KTLService):      # service = 'nscicam'
    # named queries, e.g. exposure_state(), exposure_settings();
    # file-path helpers (today's ExposurePath) and settings (ExposureConfig)
```

All reads and writes go through `KTLService` with an explicit timeout, so
nothing in `nickel_focus` can wait on a service indefinitely (REF §3.1).
Our services honor timeouts (REF §2.1, §3.1).

### 3.2 Importing `ktl` and handling its absence

**Today.** `pkg/ktl.py` imports `ktl` once, warning and setting it to `None`
if the import raises `ModuleNotFoundError`; `nickel_focus/__init__.py`
re-exports it as `nickel_focus.ktl`. Then `focus.py`, `exposure.py`, and
`slew.py` each do `from nickel_focus import ktl` and check `ktl is None` in
their constructors; `FocusSequence` checks `ktl is not None`; the GUI
Controller checks `focus.ktl is not None`; and the tests' `no_ktl` fixture
patches `focus.ktl`, `exposure.ktl`, and `slew.ktl` separately.

**Proposed: `ktl_service.py` is the only module that uses `ktl`.**

- **One import.** `ktl_service.py` does `from nickel_focus.pkg.ktl import
  ktl`. `pkg/ktl.py` keeps its role as the soft-failing import shim, but
  nothing else imports it, and the package-level re-export in
  `nickel_focus/__init__.py` is removed -- so `from nickel_focus import ktl`
  stops working, and any new direct use of `ktl` fails loudly instead of
  quietly bypassing the service layer. `focus.py`, `exposure.py`, and
  `slew.py` no longer import `ktl` at all; they receive service objects
  (§3.3).
- **Availability.** A module-level `ktl_service.ktl_available()` replaces
  every `... ktl is not None` check (Controller, `FocusSequence`, CLI
  scripts).
- **Construction without `ktl`.** `KTLService.__init__` raises a new
  `KTLUnavailableError`, a subclass of `RuntimeError` -- matching today's
  convention, so existing `except RuntimeError` handling keeps working
  while callers can be more specific. The Controller keeps its current
  pattern: `try: poco = NickelPoco() except KTLUnavailableError: poco =
  None`.
- **Exceptions.** `ktl`'s own exceptions (`ktl.ktlError`,
  `ktl.TimeoutException`) are attributes of a module that may be `None`, so
  code outside `ktl_service.py` can't safely name them in an `except`.
  `KTLService` catches them at its boundary and re-raises exceptions
  defined in `ktl_service.py` (e.g. `KTLError`, `KTLTimeoutError`),
  chained to the original (`raise ... from e`).
- **Plain values across the boundary.** Service methods take and return
  plain Python values (`str`, `float`, `int`, `SkyCoord`, ...) and raise
  only our exceptions; `ktl.Keyword` objects never leave `ktl_service.py`.
  Callbacks (§3.5) likewise deliver `(service, keyword, value)`, not
  keyword objects.
- **Broken installs.** `pkg/ktl.py` currently catches only
  `ModuleNotFoundError` (REF §1). An installed `ktl` whose compiled `ktlc`
  extension or `py-util` dependency fails to load can raise other
  `ImportError`s, which would stop `nickel_focus` from importing at all.
  Catch `ImportError` instead, and include the error in the warning, so a
  broken install degrades the same way a missing one does.
- **Import timing.** Importing `ktl` starts its background threads
  (REF §2.2). Today that happens on any `import nickel_focus` -- even for
  replay-only CLI runs -- because of the package-level re-export. With the
  re-export gone, `ktl` is imported only when `ktl_service` is. (Deferring
  it further, to the first `ktl_available()` call or service
  construction, is an open decision, §6.)
- **Tests: one seam.** `no_ktl` patches only `ktl_service.ktl` to `None`.
  The keyword-level fake (§3.7) is installed the same way: by patching
  `ktl_service.ktl` with a fake module object whose `cache(service,
  keyword)` returns fake keywords and which defines the `ktl` exception
  classes, so the real service and device classes run unchanged in tests.

### 3.3 Device classes

Composition, not inheritance: there is one `nickelpoco` service and
several things that use it, so one shared service object is the natural
owner of its callbacks, heartbeat, and monitored state.

- `focus.Focus(poco)` and `slew.NickelTelescopePointing(poco)` take a shared
  `NickelPoco`; `exposure.Exposure(cam)` takes an `NSciCam`.
  `ExposurePath`/`ExposureConfig` become helpers of `NSciCam` (they share
  `RECORD`).
- The duplicated "movement allowed" check (`POCSTOP`, now in both
  `Focus.set_to` and `slew_to`) becomes one `NickelPoco` query.
- `Focus` stops reading the camera's `EXPSTATE`. The "camera idle before a
  focus move" interlock moves to `FocusSequence` (which holds both devices),
  using `NSciCam`.
- `FocusSequence` and the GUI receive the service objects instead of
  constructing their own hardware handles: the Controller creates one
  `NickelPoco` and one `NSciCam` and passes them in; the CLI scripts do the
  same for their own run.

### 3.4 What to monitor

Monitoring (with a blocking priming read at construction) replaces the
GUI's 1 Hz polling and fixes the first-use `waitFor` false negatives
(Appendix A, item 4).

| Keyword | Monitor | Purpose |
| --- | --- | --- |
| `POCSTOP`, `POCTRCK`, `POCOT`, `POCSECLK`, `EXPSTATE` | Yes | Status indicators; `waitFor` checks before moves. |
| `POCSECPA` | Yes | Focus indicator text; current focus. |
| `POCORAA`, `POCODECA` | Yes, display rate-limited | Slew tab's current position. Their broadcast rate is unknown and may be high; redraw at most a few times a second. |
| `EXPOSURE`, `AMPCONF`, `BINNING` | Yes | Exposure fields follow the camera (§3.6). |
| `POCHEART`, `DISP0CLK` | Heartbeats | `NickelPoco.heartbeat = 'POCHEART'` ("poco's heartbeat"); `NSciCam.heartbeat = 'DISP0CLK'` (dispatcher runtime clock). Lets `ktl` detect a dispatcher restart and restore monitoring (Appendix A, item 11). Periods to be set from their observed broadcast rates. |
| `POCSECPD`, `POCORAD`, `POCODECD`, `RECORD`, `OBSNUM`, `EXPRESULT`, `RECORDDIR`, `SCRATCHDIR`, `FITSPREFIX`, `FITSSUFFIX`, `AMPCONFLIST` | No | Written by us or not displayed; read (with timeout) at the moment of use. |

### 3.5 GUI binding: callbacks instead of polling

- `KTLService.subscribe(names, function)` registers `function` with
  `Keyword.callback` for each keyword. `ktl` calls it on its own Courier
  thread with a frozen copy of the keyword, whose `read()` returns the
  broadcast value without contacting the service (`Keyword.clone`;
  `Keyword.read`, `Keyword.py:846`) -- a named method, so within our rules.
- The Controller owns a small `QObject` bridge whose callback emits a Qt
  signal `(service, keyword, value)`; Qt queues it onto the GUI thread (the
  same pattern as `QtLogHandler`). The bridge must be kept alive by the
  Controller: `ktl` stores callbacks as weak references (REF §3.3).
- The Controller maps each signal to a view update (status indicator,
  position display, exposure fields) using the existing pure mapping
  functions. The 1 Hz `_poll_timer` and `_update_status_indicators()`
  polling go away; current values come from the priming reads at
  construction and then from broadcasts.
- Position updates are coalesced (e.g. a short `QTimer` that redraws the
  latest value), so a fast-broadcasting `POCORAA`/`POCODECA` can't flood
  the GUI.
- If a service is unavailable (no `ktl`, construction failed), its
  indicators stay red as today.

### 3.6 Exposure fields follow the camera, both ways

- **Camera → GUI:** a broadcast of `EXPOSURE`, `AMPCONF`, or `BINNING`
  updates the fields (replacing today's one-time
  `_initialize_exposure_config()`), with signals blocked while setting them
  so the update isn't mistaken for a user edit. A change that didn't come
  from the focus GUI is noted in the Log.
- **GUI → camera:** a user edit writes the keyword immediately
  (`editingFinished` for the exposure time, `activated` for the combo
  boxes) via `NSciCam`, rather than waiting for Acquire. The resulting
  broadcast confirms it in every view, including other GUIs that track the
  keyword. A rejected write is logged, and the field is put back to the
  camera's value.
- **Consequences to accept:**
  - Single/Grid/Auto each have their own exposure fields today. With the
    camera as the single source of truth they always show the same
    values, so an edit on one tab appears on all three. (Optionally, they
    could later be merged into one shared set of fields.)
  - Nothing about the exposure fields is saved between sessions: the
    camera defines them. (Settings persistence is removed altogether, in
    phase 0; §3.8.)
  - Acquire still applies the fields via `configure()` before exposing, as
    a safeguard; with the fields in sync this is normally a no-op.
  - Fields are already disabled while anything runs (`set_running`), so
    user edits can't change the camera mid-sequence. Broadcasts still
    update them.

### 3.7 Testing

- Replace today's whole-class fakes (`FakeFocus`, `FakeExposure`,
  `FakeTelescopePointing`) with a **keyword-level fake** (`read`, `write`,
  `waitFor`, `monitor`, `callback`, honoring timeouts) underneath the
  service classes, so the real logic in `Focus.set_to`,
  `NickelTelescopePointing.slew_to`, and `Exposure.expose` runs under test
  for the first time. The fake hardware's simulated camera/telescope
  behavior moves into fake *service* state driven by the fake keywords.
- The autouse `no_ktl` fixture keeps forcing every `ktl` reference to
  `None`; tests that need hardware inject fake services explicitly.
- New tests: service classes (monitoring at construction, `check`
  tri-state, timeouts), the Qt bridge (callback on a non-GUI thread reaches
  the GUI thread), callback-driven indicators and position display,
  two-way exposure-field sync (camera change updates fields; edit writes
  keyword; no feedback loop; rejected write reverts).

### 3.8 Removing the Options tab and settings persistence

The Options tab holds a single checkbox, "Remember settings between
sessions", which opts into saving every tab's configuration and the image
stretch via `QSettings`. With the tab gone there is no way to opt in, and
persistence is opt-in by design, so all of it goes:

- **`FocusControlPanel`:** `options_tab`, `_build_options_tab`,
  `remember_settings_checkbox`; `get_settings_state`,
  `set_settings_state`, `_settings_fields`; `options_tab` in
  `preferred_height_excluding_help`'s list of tabs; and every docstring
  that names the Options tab (the module docstring, the class docstring's
  Options paragraph and its list of tabs without action buttons, and the
  lists of tabs in `get_focus_sequence_type`/`get_focus_sequence_config`).
- **Help tab text** (`_build_help_tab`) needs editing, not just a line
  deleted: remove the `<b>Options</b> — app-wide preferences.` entry, and
  move its paragraph break (`<br><br>`) onto the `Log` entry before it, so
  the list of tabs still ends its paragraph. Re-read the whole text
  afterwards for anything else that implies settings are remembered.
  (Phase 4 needs a further Help edit of its own: the Single/Grid/Auto
  descriptions should say the exposure fields show the camera's current
  settings and that changing them changes the camera immediately.)
- **`ImagePanel`:** `get_settings_state`/`set_settings_state` (the stretch
  choice).
- **`MainWindow`:** `_load_settings`, `_save_settings`, `_settings`,
  `_SETTINGS_ORG`/`_SETTINGS_APP`, the `closeEvent` override (it exists only
  to save settings), and the persistence paragraphs in the class
  docstring.
- **Controller:** the note in `_initialize_exposure_config`'s docstring
  that it runs after saved settings are restored (the method itself is
  replaced in phase 4).
- **Tests:** the `isolate_qsettings` autouse fixture
  (`tests/gui/conftest.py`); the settings tests in `test_main.py` (4),
  `test_focus_control_panel.py` (3), and `test_image_panel.py` (2); and the
  `options_tab` entries in the tab-order, button-set, and tab-list tests.
- **Docs:** `GUI_REFERENCE.md` (persistence, the Options tab, the
  `main_window.py` description).

**What observers lose:** remembered Grid/Auto/Replay values (start, step,
number of steps), Replay's data directory/prefix/suffix/obsnum, the Slew
tab's starlist file and search pattern, and the image stretch. Each launch
starts from the built-in defaults; the exposure fields start from the
camera.

**Existing settings files** from observers who opted in are left in place:
nothing reads them after this change, so they're harmless, and deleting
them automatically would need the `QSettings` code being removed. They can
be deleted by hand (on Linux, typically
`~/.config/LickObservatory/NickelFocusGUI.conf`; on macOS,
`~/Library/Preferences/com.lickobservatory.NickelFocusGUI.plist`).

### 3.9 Live tests against real `ktl`

A small set of tests that run against the real services, for use at the
telescope (e.g. after a `ktl` or dispatcher upgrade). Two rules:

1. **They run only when explicitly requested** -- never just because
   `ktl` happens to be importable, so the normal suite behaves identically
   on every machine (`GUI_REFERENCE.md` §5).
2. **They never change any keyword value.**

**Requesting them.** A pytest command-line option, `--ktl-live`,
registered in `tests/conftest.py` (`pytest_addoption`), together with a
`ktl_live` marker on each live test. A collection hook skips every
`ktl_live` test unless `--ktl-live` is given, so `pytest`, `tox`, and CI
never run them. If `--ktl-live` *is* given but `ktl_service.ktl_available()`
is false, the live tests **fail** with a clear message rather than skip:
the run was explicitly requested, so a missing `ktl` is an error, not a
reason to report success. The live tests live in their own module (e.g.
`tests/test_ktl_live.py`) and never construct the GUI.

```
pytest nickel_focus/tests --ktl-live            # normal suite plus the live tests
pytest nickel_focus/tests --ktl-live -m ktl_live   # live tests only
```

**Isolation from the rest of the suite.** The autouse `no_ktl` fixture
(§3.2) stays in force for every other test. It leaves `ktl_service.ktl`
alone only for tests carrying the `ktl_live` marker -- and those only run
under `--ktl-live` -- so the guarantee that the ordinary suite never
touches hardware is unchanged.

**No writes, enforced rather than promised.** Because every keyword write
in `nickel_focus` goes through `KTLService.write` (keyword objects never
leave `ktl_service.py`, §3.2), the service classes take a
`read_only=False` constructor argument: when `True`, `write` raises a new
`KTLReadOnlyError` *before* touching `ktl`. Every live test constructs its
services with `read_only=True`, so even a device method that writes (e.g.
`Focus.set_to`, `NickelTelescopePointing.slew_to`, `Exposure.expose`,
`ExposureConfig.configure`) would fail harmlessly instead of commanding
hardware. Construction itself only reads, monitors, and registers
heartbeats -- none of which change a keyword. (A read-only service could
later also back a "monitor only" mode of the GUI, but that isn't part of
this plan.)

**The tests** (deliberately few):

- `NickelPoco(read_only=True)` and `NSciCam(read_only=True)` construct
  successfully -- i.e. every monitored keyword exists and its priming read
  succeeds.
- The device classes construct on those read-only services:
  `Focus`, `NickelTelescopePointing`, `Exposure` (and the `NSciCam` path and
  settings helpers).
- **Each service has a viable heartbeat:** a `KTLService` method (proposed:
  `heartbeat_alive(timeout)`) reads the heartbeat keyword, then waits with
  `waitFor('!= <that value>', timeout)` for it to change -- a named-method
  check that the dispatcher is broadcasting. The test asserts it returns
  true within a timeout of a few heartbeat periods. (`ktl`'s own
  heartbeat state is only reachable through `Keyword.isAlive()` after its
  background checker has run, which makes for a slower and less direct
  test.)

**Guarding the guard.** The ordinary suite (no `ktl` needed) gains tests
that a read-only service raises `KTLReadOnlyError` from `write` without
calling the fake keyword's `write`, and that a device method which writes
fails the same way on a read-only service.

## 4. Phases

0. **Remove the Options tab and settings persistence** (§3.8). Independent
   of `ktl`, so it can land first; doing so means the later phases --
   especially phase 4, which changes the exposure fields -- don't have to
   keep the settings code and its tests working.
1. **Service classes, no behavior change.** Add `KTLService`,
   `NickelPoco`, `NSciCam`; make `ktl_service.py` the only user of `ktl`
   (§3.2: remove the package-level re-export, add `ktl_available()` and our
   exception types, broaden `pkg/ktl.py`'s import guard); move the device
   classes onto the service objects (composition, §3.3), including the
   `EXPSTATE` interlock move; add the keyword-level fake and port the
   tests. Add the `read_only` service mode and the live-test
   infrastructure (`--ktl-live`, the `ktl_live` marker, `no_ktl`'s
   exemption) with the construction tests (§3.9). Reads keep today's semantics
   except for the added timeouts. The GUI keeps polling, through the
   service objects.
2. **Monitoring and checks.** Monitor the §3.4 keywords at construction;
   switch `Focus`/`NickelTelescopePointing` checks to `KTLService.check`,
   with messages that distinguish "no reply" from "condition false".
   Register heartbeats, and add `heartbeat_alive()` with its live test
   (§3.9). (Resolves Appendix A, items 4 and 11.)
3. **Callback-driven GUI.** The Qt bridge, callback-driven status
   indicators and position display, removal of the 1 Hz poll (§3.5).
   (Resolves Appendix A, item 6.)
4. **Two-way exposure fields** (§3.6), including the Help tab text that
   describes them (§3.8).

Each phase ends with the full suite passing, updated `GUI_REFERENCE.md`/
`KTL_REFERENCE.md`, and a `GUI_IMPLEMENTATION.md` entry.

## 5. Not addressed by this plan

These issues from Appendix A are outside the scope above. The refactor
gives each an obvious home (noted in brackets), and most are small, but
they need their own decisions and, in several cases, confirmation at the
telescope first.

1. **Slew targets written as ascii decimal numbers** (Appendix A, item 1)
   [`NickelTelescopePointing.slew_to`].
2. **`EXPRESULT` as the exposure's file path; `FITSFILE`** (item 2)
   [`NSciCam` path helpers; `FITSFILE` would join the monitored set].
3. **Current Dec read from ascii** (item 3) [`NickelPoco.pointing()`;
   phase 3's callback code reads the position, so choosing `binary=True`
   there would be cheap].
4. **`EXPSTATE == Start` may be too brief to see** (item 5)
   [`Exposure.expose`].
5. **Exp indicator shows `Error` as yellow** (item 7) [the GUI's
   `_expstate_state` mapping].
6. **Never write `POCSTOP`** (item 8) [a docstring/comment on
   `NickelPoco`].
7. **Read-speed names `Fast`/`Slow`** (item 9) [`NSciCam` settings;
   needs `AMPCONFLIST`'s values from the telescope].
8. **Hidden KTL warnings** (item 10) [could be wired once, at
   `KTLService`/startup, via `ktl.logger(...)`].

### 5.1 Facts to confirm at the telescope

- **Ascii writes of decimal RA/Dec** (§5 item 1): what `POCORAD`/
  `POCODECD` do with a value like `5.5`.
- **`EXPRESULT` contents** (§5 item 2): full path or bare filename, and
  the exact error form; when `FITSFILE` updates relative to
  `EXPSTATE == Ready`.
- **Negative Dec in ascii** (§5 item 3): read `POCODECA` while pointed
  between 0° and −1°.
- **Whether `EXPSTATE == Start` is reliably broadcast** (§5 item 4).
- **`AMPCONFLIST`'s values** (§5 item 7).
- **Broadcast rates** of `POCORAA`/`POCODECA` (display rate limit, §3.5)
  and of `POCHEART`/`DISP0CLK` (heartbeat periods, §3.4).
- **What write "completion" means** for `POCSECPD`, `POCORAD`/`POCODECD`,
  `EXPOSE`, and the exposure settings (REF §3.2).
- **Notify flags as the Python layer sees them**: in an interactive
  session (a diagnostic, not `nickel_focus` code), check
  `keyword['notify_reads']`/`['notify_writes']` for one keyword per
  service -- or that `read(timeout=0.001)` raises `TimeoutException`
  rather than blocking.
- **Whether the data-acquisition GUI tracks `EXPOSURE`/`AMPCONF`/
  `BINNING`**, so Goal 2's "both ways" is visible there.

## 6. Open decisions

- Module name and location for the service classes
  (`nickel_focus/ktl_service.py` proposed).
- Exception names (`KTLUnavailableError`, `KTLError`, `KTLTimeoutError`
  proposed, §3.2).
- When to import `ktl` (§3.2): when `ktl_service` is first imported
  (proposed, simplest), or lazily on the first `ktl_available()` call or
  service construction, so code paths that never touch hardware never
  start `ktl`'s threads.
- Default read/write timeout for `KTLService`.
- Live-test details (§3.9): the option and marker names (`--ktl-live`,
  `ktl_live` proposed), the heartbeat timeout (pending the observed
  heartbeat periods, §5.1), and whether to document how to run them in the
  README as well as in `GUI_REFERENCE.md`.
- Whether to merge the Single/Grid/Auto exposure fields into one shared
  set now (§3.6) or keep three synchronized copies.

## Appendix A. Issues extracted from `KTL_REFERENCE.md` §5

Moved here from `KTL_REFERENCE.md` (2026-11-17), where they were recorded as
"Implications and open questions". Section and item references below are
to `KTL_REFERENCE.md` ("REF") unless noted. Resolution: items 4, 6, 11 by
this plan (§4); the rest are in §5 above.

1. **Slew targets are written as ascii decimal numbers to sexagesimal
   keywords.** `slew_to` writes `ra.to('hourangle').value` and
   `dec.to('deg').value` (e.g. `5.5`) to `POCORAD`/`POCODECD` as ascii,
   but those keywords' ascii form is sexagesimal (`05:30:00.00`), and how
   the service parses a decimal ascii value is a C-layer question
   (REF §3.2). If it misreads it, the telescope slews to the wrong place.
   *Option:* write the binary value in radians
   (`write(ra.radian, binary=True)`), which is the keywords' native unit
   (REF §4.1).
2. **`EXPRESULT` isn't a reliable file path.** `ExposurePath.previous`
   (used by `FocusSequence.take_exposure` for every exposure) is
   `Path(EXPRESULT.read())`. On an error, `EXPRESULT` holds the filename
   *plus a message*, which would silently become a bogus path; and on
   success the help doesn't say whether it's a full path. Reading it as
   soon as `EXPSTATE` returns to `Ready` may also be before the dispatcher
   has finished the FITS header. *Option:* use `FITSFILE` -- the full path,
   set only once the file is complete, and the keyword the camera's help
   says applications should track (REF §4.1) -- e.g. wait for it to update
   after the exposure, and treat `EXPSTATE == Error` as a failure.
3. **Reading the current Dec from ascii.** `NickelTelescopePointing.current`
   parses `POCODECA`'s ascii text. Its format has no explicit sign field,
   so whether a Dec between 0° and −1° keeps its minus sign is a C-layer
   question (REF §4.1). *Option:* read `binary=True` (radians) for both
   `POCORAA` and `POCODECA`.
4. **First-use false negatives from short `waitFor`s.** `Focus.set_to` and
   `NickelTelescopePointing.slew_to` use 0.5 s `waitFor`s on `POCSTOP`,
   `EXPSTATE`, `POCTRCK`, `POCOT`. If a keyword isn't monitored yet, the
   first call must receive its priming value within 0.5 s or the check
   fails ("Telescope movement is disabled!") even when the condition is
   true (REF §3.4). *Option:* `monitor()` (blocking priming read) these
   keywords at construction; and, when a check does fail, follow up with a
   short `read(timeout=...)` so a non-responding service is reported as
   such. (`KTL_SUGGESTIONS.md` §1 and §3 propose fixing both in `waitFor`
   itself.) **→ Phase 2.**
5. **`EXPSTATE == Start` may be too brief to see.** `Exposure.expose` writes
   `EXPOSE = Go`, then waits up to 30 s for `EXPSTATE == Start` before
   waiting for `Ready`. `Start` corresponds to the acquisition's starting
   phase and is probably short; if the service doesn't broadcast it, or
   the exposure moves on before the broadcast is processed, the wait times
   out and the exposure is reported as failed even though it ran
   (REF §3.4). *Option:* wait for `EXPSTATE != Ready` (any of `Start`/
   `Exposing`/`Readout`) instead, then for `Ready`.
6. **GUI polling waits on the service from the GUI thread.** Every 1 Hz
   poll makes 8 `read()`s from the GUI thread, each waiting for the
   service's reply with no timeout (REF §3.1), so a slow or unresponsive
   service freezes the GUI. *Options:* a short `timeout`; or reading
   monitored keywords' values without a round trip. **→ Phase 3** (callbacks
   deliver values with no round trip; all remaining reads get timeouts in
   phase 1).
7. **The Exp indicator shows `Error` as yellow.** Any `EXPSTATE` other than
   `Ready` is currently yellow, including `Error` (and `Unknown`).
   *Option:* show `Error` (and perhaps `Unknown`) red.
8. **Never write `POCSTOP` from our code.** Writing it stops all motion,
   and a negative value cuts motor power (REF §4.1). Our code only reads
   it and should stay that way; worth a comment where it's read.
9. **Read-speed names.** Our `Fast`/`Slow` aliases map to `1.0MHz`/
   `0.05MHz`, but the help doesn't list `AMPCONFLIST`'s actual values, so
   these are unconfirmed. If they're wrong, `configure(speed=...)` raises
   (it checks against `AMPCONFLIST`), and the GUI keeps its default speed
   with a warning.
10. **Hidden KTL warnings.** KTL's own log level is `ERROR` (REF §3.7), so
    its warnings never appear. Raising it (or routing it into
    `nickel_focus.log` via `ktl.logger(...)`, which accepts any object with
    a `.write()` method) during commissioning would surface them.
11. **Stale data after a dispatcher restart.** No heartbeats are configured
    (REF §3.6). With monitoring, register `service.heartbeat(...)` on a
    regularly broadcasting keyword, so a dispatcher restart is detected and
    monitoring restored. **→ Phase 2** (`POCHEART`, `DISP0CLK`).

Also recorded in the former §5, and true of the current code: the help
files confirmed that `POCOT` is an integer (so the On Target indicator
works as written), that the text values `allowed`/`disabled`, `on`/`off`,
`Ready`, and `Yes`/`No` match our comparisons exactly, that the binning
values match the code and GUI, and that every keyword we use supports
notify reads/writes and broadcasts (REF §4.1).
