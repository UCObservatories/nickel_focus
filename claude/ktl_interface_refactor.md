# KTL Interface Refactor -- Plan

**Status:** phases 0-4 complete (2026-11-17), followed by the review of
`KTL_SUGGESTIONS.md` against the refactored code. Phases 1-4 as built:
§4.1-§4.4. Issues that remain in the code after the refactor: §5.

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
  changes land on top of it. Its only intended differences: reads gain
  timeouts (§3.1), a broken `ktl` install degrades instead of failing to
  import (§3.2), and `nickel_focus.ktl` no longer exists (§3.2).

## 3. Design

### 3.1 Service classes

A new package, `nickel_focus/services/`:

```
services/
├── __init__.py
├── base.py         KTLService, our exceptions, ktl_available() -- the only module that imports ktl
├── nickelpoco.py   NickelPoco(KTLService)
└── nscicam.py      NSciCam(KTLService), with its file-path and settings helpers
```

`base.py` holds everything generic; each service module holds that
service's own knowledge (keyword names and meanings, named queries). In
outline:

```python
class KTLService:
    """One KTL service: its keyword handles and our ktl usage rules."""
    service = None      # e.g. 'nickelpoco'
    monitored = ()      # keywords monitored at construction, primed with a timed read
    heartbeat = None    # keyword registered via Service.heartbeat
    heartbeat_period = None

    def __init__(self, read_only=False): ...
        # KTLUnavailableError if no ktl (§3.2); monitor and prime the
        # monitored keywords; register the heartbeat. read_only: §3.9.
    def _keyword(self, name): ...   # ktl.cache(self.service, name); private (§3.2)
    def read(self, name, binary=False, timeout=DEFAULT_READ_TIMEOUT): ...
    def write(self, name, value, binary=False): ...
        # KTLReadOnlyError if read_only (§3.9); no timeout (below)
    def check(self, name, expression, timeout):
        """waitFor; on failure, a read(timeout=...) to tell 'false' from
        'no reply' (REF §3.4). Returns True, False, or None (no reply)."""
    def heartbeat_alive(self, timeout): ...     # §3.9
    def subscribe(self, names, function): ...   # Keyword.callback; see §3.5

# services/nickelpoco.py
class NickelPoco(KTLService):   # service = 'nickelpoco'
    # named queries, e.g. movement_allowed(), tracking(), on_target(),
    # pointing(), secondary_position(), secondary_locked()

# services/nscicam.py
class NSciCam(KTLService):      # service = 'nscicam'
    # named queries, e.g. exposure_state(), exposure_settings();
    # file-path helpers (today's ExposurePath) and settings (ExposureConfig)
```

All reads go through `KTLService` with an explicit timeout -- by default
`DEFAULT_READ_TIMEOUT`, 1 s -- so nothing in `nickel_focus` waits on a
reply indefinitely (REF §3.1); our services honor timeouts (REF §2.1,
§3.1). Writes also go through `KTLService`, but **without a timeout**: they
wait for the service to report completion, because what "completion" means
for each written keyword -- e.g. whether a `POCSECPD` write completes when
accepted or when the move finishes -- isn't known yet, and a timeout could
abort a legitimately long write. Both are to be confirmed at the telescope
(§5.2).

**Priming without hanging.** `Keyword.monitor()`'s own priming read has no
timeout (REF §3.3), so construction instead calls
`monitor(prime=False)` for each monitored keyword and then primes it with
`read(timeout=...)`. A service whose dispatcher doesn't answer therefore
fails construction with `KTLTimeoutError` instead of hanging.

### 3.2 Importing `ktl` and handling its absence

**Today.** `pkg/ktl.py` imports `ktl` once, warning and setting it to `None`
if the import raises `ModuleNotFoundError`; `nickel_focus/__init__.py`
re-exports it as `nickel_focus.ktl`. Then `focus.py`, `exposure.py`, and
`slew.py` each do `from nickel_focus import ktl` and check `ktl is None` in
their constructors; `FocusSequence` checks `ktl is not None`; the GUI
Controller checks `focus.ktl is not None`; and the tests' `no_ktl` fixture
patches `focus.ktl`, `exposure.ktl`, and `slew.ktl` separately.

**Proposed: `services/base.py` is the only module that uses `ktl`.**

- **One import.** `services/base.py` imports `ktl` itself, with the same
  soft-failing guard `pkg/ktl.py` uses today (warn once, set `ktl = None`),
  and `pkg/ktl.py` is removed, along with the package-level re-export in
  `nickel_focus/__init__.py`. So `from nickel_focus import ktl` stops
  working, and any new direct use of `ktl` fails loudly instead of quietly
  bypassing the service layer. `focus.py`, `exposure.py`, and `slew.py` no
  longer import `ktl` at all; they receive service objects (§3.3), and
  `NickelPoco`/`NSciCam` use only `KTLService` methods.
- **Availability.** A module-level `services.base.ktl_available()` replaces
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
  code outside `services/base.py` can't safely name them in an `except`.
  `KTLService` catches them at its boundary and re-raises exceptions
  defined in `services/base.py` (e.g. `KTLError`, `KTLTimeoutError`),
  chained to the original (`raise ... from e`). `KTLReadOnlyError` (§3.9)
  joins them.
- **Plain values across the boundary.** Service methods take and return
  plain Python values (`str`, `float`, `int`, `SkyCoord`, ...) and raise
  only our exceptions; `ktl.Keyword` objects never leave `services/base.py`.
  Callbacks (§3.5) likewise deliver `(service, keyword, value)`, not
  keyword objects.
- **Broken installs.** Today's guard in `pkg/ktl.py` catches only
  `ModuleNotFoundError` (REF §1). An installed `ktl` whose compiled `ktlc`
  extension or `py-util` dependency fails to load can raise other
  `ImportError`s, which would stop `nickel_focus` from importing at all.
  `services/base.py` catches `ImportError` instead, and includes the error
  in the warning, so a broken install degrades the same way a missing one
  does.
- **Import timing.** Importing `ktl` starts its background threads
  (REF §2.2). Today that happens on any `import nickel_focus` -- even for
  replay-only CLI runs -- because of the package-level re-export. With the
  re-export gone, `ktl` is imported when `services.base` loads -- i.e. only
  by code that works with services.
- **Tests: one seam.** `no_ktl` patches only `services.base.ktl` to `None`.
  The keyword-level fake (§3.7) is installed the same way: by patching
  `services.base.ktl` with a fake module object whose `cache(service,
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

Monitoring at construction (phase 2) fixes `waitFor`'s first-use false
negatives (a check on a keyword whose first value hasn't arrived yet fails
even when the condition holds; REF §3.4); together with callbacks (§3.5,
phase 3) it replaces the GUI's 1 Hz polling.

| Keyword | Monitor | Purpose |
| --- | --- | --- |
| `POCSTOP`, `POCTRCK`, `POCOT`, `POCSECLK`, `EXPSTATE` | Yes | Status indicators; `waitFor` checks before moves. |
| `POCSECPA` | Yes | Focus indicator text; current focus. |
| `POCORAA`, `POCODECA` | Yes, display rate-limited | Slew tab's current position. Their broadcast rate is unknown and may be high; redraw at most a few times a second. |
| `EXPOSURE`, `AMPCONF`, `BINNING` | Yes | Exposure fields follow the camera (§3.6). |
| `POCHEART`, `DISP0CLK` | Heartbeats | `NickelPoco.heartbeat = 'POCHEART'` ("poco's heartbeat"); `NSciCam.heartbeat = 'DISP0CLK'` (dispatcher runtime clock). Lets `ktl` detect a dispatcher restart and restore monitoring, rather than leaving monitored values silently stale (REF §3.6). Both use a provisional 30 s `heartbeat_period`, generous because too short a period makes `ktl` reconnect needlessly; to be set from their observed broadcast rates (§5.2). |
| `FITSFILE` | Yes | The finished exposure's path; `Exposure.expose` waits for it to change. |
| `POCSECPD`, `POCORAD`, `POCODECD`, `RECORD`, `OBSNUM`, `EXPRESULT`, `RECORDDIR`, `SCRATCHDIR`, `FITSPREFIX`, `FITSSUFFIX`, `AMPCONFLIST` | No | Written by us or not displayed; read (with timeout) at the moment of use. |
| `EXPOSE` | No | Written to start an exposure; never read. |

### 3.5 GUI binding: callbacks instead of polling

- `KTLService.subscribe(names, function)` registers `function` with
  `Keyword.callback` for each keyword. `ktl` calls it on its own Courier
  thread with a frozen copy of the keyword, whose `read()` returns the
  broadcast value without contacting the service (`Keyword.clone`;
  `Keyword.read`, `Keyword.py:846`) -- a named method, so within our rules.
- `ktl` stores callbacks as **weak references** (REF §3.3), so a callback
  that nothing else references is silently dropped. `subscribe` wraps
  `function` (to read the value and call `function(service, keyword,
  value)`), and the service object must keep a strong reference to each
  wrapper for as long as the subscription should last; likewise, the
  Controller must keep its bridge alive.
- The Controller owns a small `QObject` bridge whose callback emits a Qt
  signal `(service, keyword, value)`; Qt queues it onto the GUI thread (the
  same pattern as `QtLogHandler`).
- The Controller maps each signal to a view update (status indicator,
  position display, exposure fields) using the existing pure mapping
  functions. The 1 Hz `_poll_timer` and `_update_status_indicators()`
  polling go away; current values come from the priming reads at
  construction and then from broadcasts.
- Position updates are coalesced (e.g. a short `QTimer` that redraws the
  latest value), so a fast-broadcasting `POCORAA`/`POCODECA` can't flood
  the GUI.
- If a service is unavailable (no `ktl`, construction failed) or a
  subscription fails, its indicators stay red.

### 3.6 Exposure fields follow the camera, both ways

- **Camera → GUI:** the Controller subscribes to `EXPOSURE`, `AMPCONF`, and
  `BINNING` (§3.5). A value that differs from what the fields show is
  applied to all three exposure tabs and logged -- the first as the
  camera's setting, later ones as changes made on the camera (e.g. from
  another GUI, or the camera adjusting a requested exposure time). The
  broadcast that confirms the user's own edit equals what's already shown,
  so it's neither applied again nor logged. A speed or binning that isn't
  one of the field's options is added to them (on all three tabs), with a
  warning, so the field always shows the camera's actual setting.
- **GUI → camera:** a committed edit is written to the camera immediately
  via `NSciCam`, rather than waiting for Acquire. A speed or binning choice
  commits at once (the fields are rows of radio buttons, whose `chosen`
  signal fires only for the user's own clicks). The
  exposure time commits only on Enter, as in the data-acquisition GUI: an
  edit turns the field light red until then, and Acquire uses the last
  committed value. The write runs **on the GUI thread**: settings changes
  are normally quick, and blocking the GUI while a slow camera finishes is
  acceptable. A rejected write is reported, and the fields go back to the
  camera's value.
- **Consequences:**
  - Single/Grid/Auto each keep their own exposure fields, kept in sync:
    with the camera as the single source of truth they always show the
    same values, and an edit on one tab appears on all three. Merging them
    into one shared group would be a separate layout change (§5.3).
  - Nothing about the exposure fields is saved between sessions: the
    camera defines them. (Settings persistence is removed altogether, in
    phase 0; §3.8.)
  - Acquire still applies the fields via `configure()` before exposing, as
    a safeguard; with the fields in sync this is normally a no-op.
  - Fields are disabled while anything runs (`set_running`), so user edits
    can't change the camera mid-sequence. Broadcasts still update them.
  - Without a camera connection (no `ktl`, e.g. under `--test`), the
    fields are plain inputs with their built-in defaults; an edit only
    syncs the three tabs.

### 3.7 Testing

- Replace today's whole-class fakes (`FakeFocus`, `FakeExposure`,
  `FakeTelescopePointing`) with a **keyword-level fake** (`read`, `write`,
  `waitFor`, `monitor`, `callback`, honoring timeouts) underneath the
  service classes, so the real logic in `Focus.set_to`,
  `NickelTelescopePointing.slew_to`, and `Exposure.expose` runs under test
  for the first time. Today's simulated camera/telescope behavior
  (synthesized frames, focus and pointing that follow what's commanded)
  moves into the fake keywords' responses to writes (e.g. writing
  `EXPOSE = Go` steps `EXPSTATE` and produces a new frame and file name).
- The autouse `no_ktl` fixture keeps `services.base.ktl` set to `None`;
  tests that need hardware install the fake `ktl` module there instead
  (§3.2), and then construct the real service classes.
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
  `main_window.py` description) and the user manual,
  `docs/html/focusGUI.html` (its Options paragraph; its screenshots also
  show the old tab bar).

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
never run them. If `--ktl-live` *is* given but `services.base.ktl_available()`
is false, the live tests **fail** with a clear message rather than skip:
the run was explicitly requested, so a missing `ktl` is an error, not a
reason to report success. The live tests live in their own module (e.g.
`tests/test_ktl_live.py`) and never construct the GUI.

```
pytest nickel_focus/tests --ktl-live            # normal suite plus the live tests
pytest nickel_focus/tests --ktl-live -m ktl_live   # live tests only
```

**Documentation.** How to run the live tests -- the commands above, that
they need a live `ktl` connection (they fail, not skip, without one), and
that they never change a keyword value -- goes in both the README's
testing section and `GUI_REFERENCE.md` §5 (Testing).

**Isolation from the rest of the suite.** The autouse `no_ktl` fixture
(§3.2) stays in force for every other test. It leaves `services.base.ktl`
alone only for tests carrying the `ktl_live` marker -- and those only run
under `--ktl-live` -- so the guarantee that the ordinary suite never
touches hardware is unchanged.

**No writes, enforced rather than promised.** Because every keyword write
in `nickel_focus` goes through `KTLService.write` (keyword objects never
leave `services/base.py`, §3.2), the service classes take a
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
- **Each service has a viable heartbeat:** `KTLService.heartbeat_alive(timeout)`
  reads the heartbeat keyword, then waits with
  `waitFor('!= <that value>', timeout)` for it to change -- a named-method
  check that the dispatcher is broadcasting. The test asserts it returns
  true within two heartbeat periods (provisional, like the periods
  themselves; §5.2).

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
   `NickelPoco`, `NSciCam` in `services/` (§3.1); make `services/base.py`
   the only user of `ktl` (§3.2: move and broaden `pkg/ktl.py`'s import
   guard, remove `pkg/ktl.py` and the package-level re-export, add
   `ktl_available()` and our exception types); move the device
   classes onto the service objects (composition, §3.3), including the
   `EXPSTATE` interlock move; add the keyword-level fake and port the
   tests. Add the `read_only` service mode and the live-test
   infrastructure (`--ktl-live`, the `ktl_live` marker, `no_ktl`'s
   exemption) with the construction tests, and document how to run them in
   the README and `GUI_REFERENCE.md` (§3.9). Reads keep today's semantics
   except for the added timeouts. The GUI keeps polling, through the
   service objects.
2. **Monitoring and checks.** Monitor the §3.4 keywords at construction;
   switch `Focus`/`NickelTelescopePointing` checks to `KTLService.check`,
   with messages that distinguish "no reply" from "condition false".
   Register heartbeats, and add `heartbeat_alive()` with its live test
   (§3.9).
3. **Callback-driven GUI.** The Qt bridge, callback-driven status
   indicators and position display, removal of the 1 Hz poll, whose reads
   ran on the GUI thread (§3.5).
4. **Two-way exposure fields** (§3.6), including the Help tab text that
   describes them (§3.8). Afterwards, review `KTL_SUGGESTIONS.md` for text
   that describes functionality the refactor has since changed (e.g. §4's
   reference to the GUI's former 1 Hz poll).

Each phase ends with the full suite passing, updated `GUI_REFERENCE.md`/
`KTL_REFERENCE.md` (and the README where it describes something the phase
changes), and a `GUI_IMPLEMENTATION.md` entry.

### 4.1 Phase 1 as built

Done as described above, with these details and small departures:

- **`KTLService.wait_for(name, expression, timeout)`** wraps `waitFor` with
  today's semantics (`True`/`False`); every check uses it, through named
  `NickelPoco`/`NSciCam` methods. Phase 2's `check` builds on it.
- **Monitoring.** `EXPSTATE` and `EXPOSE`, which `Exposure` monitored
  before, are now `NSciCam.monitored`, primed with a timed read at
  construction (§3.1's approach, applied early). `NickelPoco` monitors
  nothing yet. Phase 2 extends both lists to §3.4 (and drops `EXPOSE`).
- **Exposure helpers.** `ExposurePath`/`ExposureConfig` moved into
  `services/nscicam.py` as `NSciCam.path`/`.cfg`; `Exposure(cam)` keeps
  `.path`/`.cfg` as references to them, and `exposure.py` holds only
  `Exposure`. `ExposureConfig` reads `AMPCONFLIST` when `NSciCam` is
  constructed.
- **The `EXPSTATE` interlock** is `FocusSequence.set_focus(value)`, which
  every focus move in a sequence goes through. It now runs before
  `Focus.set_to`'s own range and `POCSTOP` checks, so when several fail,
  a different message may be reported first.
- **`FocusSequence(poco=None, cam=None)`** replaces `connect=`; hardware
  handles are built only when both are given.
- **Services keep their `ktl` module.** A service holds the `ktl` it was
  constructed with, so patching `services.base.ktl` afterward (as tests do)
  doesn't affect it.
- **`slew_to_nearest --dry_run`** constructs its `NickelPoco` with
  `read_only=True`.
- **Without `ktl`,** the CLI's live modes now fail at once with
  `KTLUnavailableError`, rather than failing later on a `None` handle.
- **Live tests** (`tests/test_ktl_live.py`): construction of both services
  and the device classes, plus plain reads of each service's values. Without
  `ktl`, `--ktl-live` reports them as errors with a clear message.

### 4.2 Phase 2 as built

- **Monitored at construction** (§3.4): `NickelPoco` -- `POCSTOP`,
  `POCTRCK`, `POCOT`, `POCSECLK`, `POCSECPA`, `POCORAA`, `POCODECA`;
  `NSciCam` -- `EXPSTATE`, `EXPOSURE`, `AMPCONF`, `BINNING` (`EXPOSE` is no
  longer monitored). Each is primed with a 1 s read, so a service that
  doesn't answer fails construction (the GUI logs it and leaves that
  service unconnected). `POCORAA`/`POCODECA` are monitored now; the display
  rate limit belongs with the callbacks (phase 3).
- **Heartbeats:** `KTLService.heartbeat`/`heartbeat_period`; the keyword is
  monitored and primed first, then registered with
  `ktl.cache(service).heartbeat(keyword, period)`. `POCHEART` and
  `DISP0CLK`, both with a **provisional 30 s period** -- deliberately
  generous, since a period shorter than the real broadcast interval makes
  `ktl` reconnect needlessly (§5.2).
- **`check`** returns `True`/`False`/`None` (no reply), as designed in
  §3.1; its follow-up read uses the 1 s default timeout. All of
  `NickelPoco`'s conditions and `NSciCam.exposure_state_is` use it.
- **`services.require(result, false_message, no_reply_message)`** turns a
  `check` result into an exception: `ValueError` (the condition is false,
  as before, with the same messages) or `KTLTimeoutError` ("No reply from
  <service>.<keyword>; cannot tell whether ..."). Used for every check in
  `Focus.set_to`, `FocusSequence.set_focus`, `NickelTelescopePointing.slew_to`,
  and `Exposure.expose` (the plan named only the first and third; the
  others had the same ambiguity).
- **`heartbeat_alive(timeout)`** and a live test per service, with a
  timeout of two heartbeat periods.
- Fake `ktl`: `cache(service)` returns a `FakeService` that records
  `heartbeat()` registrations; `POCHEART` and `DISP0CLK` keywords added.

### 4.3 Phase 3 as built

- **`KTLService.subscribe(names, function)`** registers, per keyword, a
  `relay(clone)` that calls `function(service, keyword, clone.read())`, and
  keeps it in the service's `_subscriptions` list (strong references; §3.5).
  It then monitors and primes each keyword: `ktl` runs callbacks after every
  completed read as well as every broadcast (`Keyword.propagate`), so the
  priming read delivers the current value -- no separate seeding step.
  Reading the clone inside the callback was confirmed safe in the source: a
  clone's `read()` returns its frozen value before any lock or I/O
  (`KTL_REFERENCE.md` §3.1).
- **`gui/ktl_bridge.py`'s `KTLBridge`** re-emits `relay`'s arguments as
  `keywordChanged(service, keyword, value)`. The Controller connects it with
  an explicit `QueuedConnection`, so its slot always runs on the GUI thread,
  even when the value arrives on the GUI thread itself (as it can in tests).
- **Controller:** subscribes `poco` to `POCSTOP`, `POCTRCK`, `POCOT`,
  `POCSECLK`, `POCSECPA`, `POCORAA`, `POCODECA` and `cam` to `EXPSTATE`;
  `_on_keyword_changed` keeps the latest value of each and updates the
  affected indicator (`_show_indicator`), or starts a single-shot timer for
  the position (`_show_current_position`, at most every
  `POSITION_REDRAW_MS` = 200 ms). `_poll_timer`, `_poll_hardware`,
  `_update_status_indicators`, `_update_current_position`, and the
  "logged once" read-failure tracking are gone. A failed subscription is
  logged, and its indicators stay red. Finding the nearest target still
  reads the position directly (one timed read, on demand).
- **Fake `ktl`:** keyword callbacks are held weakly (as `ktl` does, so a
  dropped subscription would show up in tests), receive a frozen copy, run
  after each read and -- while monitored -- each value change, and have
  their exceptions swallowed. Unlike `ktl`, they run on the thread that
  changed the value; the queued connection makes this irrelevant to the
  Controller.
- **Known limitation:** an indicator shows the last value received. If a
  service stops broadcasting altogether (as opposed to restarting, which
  the heartbeat handles), the indicators don't turn red; they just stop
  changing. `ktl` tracks this in its heartbeat state, which the named
  method `Keyword.isAlive()` reports, but only by polling (its first call
  also does a blocking lookup), with no notification of changes. Whether
  this matters in practice is unknown; `KTL_SUGGESTIONS.md` §5 proposes a
  heartbeat-state callback, marked as of unconfirmed usefulness.

### 4.4 Phase 4 as built

Design choices made with the user (2026-11-17):

- **Writes run on the GUI thread** (not a worker): settings changes are
  normally quick, and blocking the GUI while a slow or unresponsive camera
  finishes is acceptable -- arguably useful, since nothing else should
  happen meanwhile. Writes still have no timeout (§3.1).
- **An unrecognized speed/binning from the camera is added to that
  drop-down's options** (on all three tabs), with a warning, so the field
  always shows the camera's actual setting.
- **Only a broadcast that differs from what the fields show is applied and
  logged** -- the first value as the camera's setting, later ones as changes
  on the camera. The broadcast confirming the user's own edit therefore
  isn't logged, with no tracking of writes in flight. (The camera may
  adjust a requested exposure time; that shows up, correctly, as a change.)
- **The exposure time commits only on Enter**, mimicking the
  data-acquisition GUI: an edit (typing, or an arrow-key/wheel step) turns
  the field light red until Enter; `get_exposure_config` reports the last
  committed value, so Acquire never uses an unentered one. A drop-down
  choice takes effect at once.
- **Acquire keeps the `configure()` safeguard.**

As built:

- `focus_control_panel.ExposureTimeSpinBox` (light red `#ffcdd2` while
  pending; `valueCommitted`, `committed_value`, `set_committed_value`), 3
  decimals to match `EXPOSURE`'s `%.3f`, so the camera's value round-trips
  exactly. New `FocusControlPanel.exposureSettingEdited(key, value)`;
  `set_exposure_config` now commits the exposure time and returns the keys
  whose value was *added* to a drop-down (it used to skip them). Help tab
  and user manual describe the behavior.
- `ExposureConfig.keywords` and `ExposureConfig.setting_value` (used by
  `current_settings` too).
- Controller: subscribes `cam` to `EXPOSURE`, `AMPCONF`, `BINNING` as well;
  `_on_exposure_keyword`, `_on_exposure_setting_edited`,
  `_show_exposure_settings`; `_initialize_exposure_config` is gone. Every
  edit syncs the three tabs before writing; without the camera service,
  that's all it does.
- Tests: `tests/gui/keyboard.py` sends real key events (typing, Enter,
  arrows). New tests for the pending/commit behavior, drop-down edits,
  following camera changes, writes, rejected writes, unknown and unreadable
  values, and `setting_value`.

## 5. Remaining issues

Issues in the code as it stands after the refactor. References are to
`KTL_REFERENCE.md` ("REF") unless noted. Several depend on facts that can
only be checked at the telescope; the live tests (§3.9,
`pytest nickel_focus/tests --ktl-live -m ktl_live`) are the first thing to
run there.

### 5.1 Possible defects

1. **The current Dec is parsed from ascii.** `NickelPoco.pointing()` (used
   by "Find nearest ...") reads `POCODECA`'s ascii text, and the
   Controller's position display parses the ascii values its subscription
   delivers. The format has no explicit sign field, so whether a Dec
   between 0° and −1° keeps its minus sign is a C-layer question (REF §4.1).
   *To confirm:* read `POCODECA` while pointed between 0° and −1°.
   *Option:* use the binary values (radians) for `POCORAA`/`POCODECA`;
   for the display, `KTLService.subscribe` would need a way to deliver
   binary values.
2. **`EXPSTATE == Start` may be too brief to see.** `Exposure.expose` writes
   `EXPOSE = Go`, then waits up to 30 s for `EXPSTATE == Start` before
   waiting for `Ready`. `Start` is probably short; if the service doesn't
   broadcast it, or the exposure moves on before the broadcast is
   processed, the wait times out and the exposure is reported as failed
   even though it ran (REF §3.4). *To confirm:* whether `Start` is reliably
   broadcast. *Option:* wait for `EXPSTATE != Ready` (any of `Start`/
   `Exposing`/`Readout`) instead, then for `Ready`.
3. **The Exp indicator shows `Error` as yellow.** The Controller's
   `_expstate_state` maps any `EXPSTATE` other than `Ready` to yellow,
   including `Error` (and `Unknown`). *Option:* show `Error` (and perhaps
   `Unknown`) red.
4. **Indicators go stale, not red, if a service stops broadcasting
   entirely** (as opposed to restarting, which the heartbeat handles; §4.3).
   They just stop changing. `ktl`'s heartbeat state would tell, but only by
   polling `Keyword.isAlive()` (REF §3.6); `KTL_SUGGESTIONS.md` §5 proposes
   a notification. Whether it matters in practice is unknown.

### 5.2 Settings and assumptions to confirm at the telescope

- **The 1 s default read timeout** (`DEFAULT_READ_TIMEOUT`, §3.1): that
  every service answers a normal read well within it, including while busy
  (e.g. during readout or a slew).
- **Startup time:** constructing the services primes about a dozen
  monitored keywords, and the Controller's subscriptions read each
  subscribed keyword once more; an unanswered read fails only after 1 s.
- **Heartbeat periods** (`POCHEART`, `DISP0CLK`; provisional 30 s, §3.4)
  and the live heartbeat test's timeout (two periods, §3.9): set them from
  the keywords' observed broadcast intervals.
- **Exposure completion** (`Exposure.expose`): when `FITSFILE` updates
  relative to `EXPSTATE == Ready` (within `Exposure.FILE_TIMEOUT`, 30 s?);
  that it names a different file for every exposure, including with
  `RECORD = No` (if a scratch file name were reused, the wait for a new
  `FITSFILE` would time out); that `EXPSTATE` passes through `Error` when an
  exposure fails, and what `EXPRESULT` holds then.
- **`POCORAA`/`POCODECA` broadcast rate**, against the position display's
  200 ms redraw limit (`Controller.POSITION_REDRAW_MS`).
- **What write "completion" means** for `POCSECPD`, `POCORAD`/`POCODECD`,
  `EXPOSE`, and the exposure settings (REF §3.2), to set per-keyword write
  timeouts (§3.1). Until then a write waits however long completion takes;
  for the exposure settings that wait is on the GUI thread (§3.6).
- **Notify flags as the Python layer sees them** (the read timeouts depend
  on them, REF §3.1): in an interactive session (a diagnostic, not
  `nickel_focus` code), check `keyword['notify_reads']`/`['notify_writes']`
  for one keyword per service -- or that `read(timeout=0.001)` raises
  `TimeoutException` rather than blocking.
- **Callbacks on the real `ktl`:** that a subscription delivers values (the
  `ktl_live` subscription test), since the fake `ktl` can't check `ktl`'s
  own callback thread and clones.
- **Whether the data-acquisition GUI tracks `EXPOSURE`/`AMPCONF`/
  `BINNING`**, so that an edit made in the focus GUI shows up there
  (Goal 2).

### 5.3 Possible later improvements

- **One shared group of exposure fields** instead of three synchronized
  copies on Single/Grid/Auto (§3.6) -- a layout change.
- **A "monitor only" GUI mode** backed by read-only services (§3.9).
- **Simplifications that depend on `ktl` changes** proposed in
  `KTL_SUGGESTIONS.md`: waiting for a keyword's first value in `waitFor`
  (§1) would make listing every checked keyword in `monitored`
  unnecessary; a distinct "no value" result (§3) would replace `check`'s
  follow-up read; a cached-value method (§4) would replace the
  Controller's own copy of subscribed values and some round trips.
