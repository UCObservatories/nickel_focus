# KTL Python Reference

Developer reference for the `ktl` package as `nickel_focus` uses it: how the
package is put together, what each call we make actually does, and what that
implies for our code. Written from a read-only copy of the package's Python
layer and the services' exported keyword help (see §1); it should be updated
if either is refreshed or if behavior at the telescope contradicts anything
here.

**Named public methods only.** `nickel_focus` uses only `ktl`'s named
public methods (`ktl.cache` and the calls in §3). It never reaches into
lower-level objects such as `keyword.ktlc` or the Usher, and it
deliberately avoids `ktl`'s dictionary-style keyword access
(`keyword['ascii']` etc.) as well, even though `ktl` treats that as public
-- see §3.8 for why. Needs the named methods don't meet are proposed as
new methods in `claude/KTL_SUGGESTIONS.md`; planned changes to how we use
`ktl` are in `claude/ktl_interface_refactor.md`.

**Uncertainty markers.** The Python layer is a wrapper around a compiled C
extension, `ktlc`, which in turn calls the KTL C library and a per-service
client library. Anything decided below the Python layer is marked
**[C-layer]** -- the Python code shows that the behavior *depends* on it, but
not what it does. Where the services' keyword help (§1, §4.1) answers such a
question, it's marked **[help]** instead; those answers come from the
services' own documentation rather than from code, so they're likely but
still worth confirming once at the telescope.

## 1. Provenance

- **Source:** `ktl_python/` in the repo root -- an untracked, reference-only
  copy (never import or run it), rsync'd from
  `/opt/kroot/rel/default/Versions/ktl/keyword/python/5.2/` on
  `nandor.ucolick.org`. Its `History` file shows installs of version 5.2
  from Oct 2024 through May 2026.
- **Contents:** pure-Python modules (`Keyword.py`, `Service.py`,
  `Procedural.py`, `Expression.py`, `Usher.py`, ...) plus the compiled
  `ktlc.cpython-312-x86_64-linux-gnu.so`, which was not examined.
- **External dependencies** (not in the copy): `Compat`, `ThreadPrimitives`,
  and `WeakRef` from `kroot/util/py-util`; the `RELDIR` environment variable
  (used by `ktl.services()` to find `lib<service>_keyword*` client
  libraries).
- **Keyword help:** `ktl_python/ktl_nickelpoco.txt` (311 keywords) and
  `ktl_python/ktl_nscicam.txt` (95 keywords), exported from the two services'
  help text. Each entry gives the keyword's long name, type, enumerated
  values, units, display format, read/write/notify/broadcast capabilities,
  and a description. §4.1 summarizes the entries for the keywords we use.
  Each entry's `Read/Write` line starts with a short access code (`c2`,
  `c-`, `C2`) whose meaning isn't documented in the files; the explicit
  `read=… ; broadcast=… ; write=…` part that follows is what this document
  relies on.
- **How we import it:** only in `nickel_focus/services/base.py`, which sets
  `ktl = None` (with one warning) if the import raises any `ImportError` --
  including a broken install, e.g. a missing `py-util` module or client
  library -- so `nickel_focus` still imports without it.

## 2. Architecture

### 2.1 Objects

- **`ktl.cache(service, keyword)`** (`Procedural.py:31`) is the entry point we
  use. It returns a **process-wide singleton** `Keyword` for that
  service/keyword: every `ktl.cache('nickelpoco', 'POCSTOP')` call -- from
  any `NickelPoco` instance, any thread -- returns the
  *same* object, with the same cached value, monitoring state, and callbacks.
  The first `Service` instance for a name is cached forever
  (`Service.cached`, `Service.py:881`).
- **`Service`** (`Service.py:38`): one per KTL service (`nickelpoco`,
  `nscicam`). Creating one opens the service via `ktlc.Service(name)` and
  starts two threads for it (§2.2). Keywords are created on first access.
  Service names are case-sensitive (they name a shared library); keyword
  names are case-insensitive and stored upper-case.
- **`Keyword`** (`Keyword.py:41`) holds the latest known value in two forms:
  - `binary` -- the native value: a number, or for an enumerated/boolean
    keyword its integer index (the help lists enumerators as
    `0 allowed 1 disabled`, etc. **[help]**); for the coordinate keywords,
    radians (§4.1);
  - `ascii` -- the string form: an enumerator's name, or a number formatted
    with the keyword's display format (e.g. `%.8f`, or sexagesimal
    `%02d:%02d:%05.2f` for RA) **[help]**.

  It's subclassed by KTL type (`Keyword.py:2548`): `Numeric` (FLOAT/DOUBLE),
  `Integer` (INT/INT64), `String`, `Enumerated` (ENUM/ENUMM), `Boolean`,
  `Mask`, and array types. Each keyword's type is defined by its service;
  §4.1 lists the types of the keywords we use **[help]**.
- Per-keyword capabilities, fixed when the keyword is created
  (`Keyword.__getInit`, `Keyword.py:1606`): `broadcasts` (can be
  monitored), `reads`, `writes`, `notify_reads` (KTL_NOTIFY reads),
  `notify_writes` (KTL_NOTIFY writes). Several behaviors below branch on
  these. **For every keyword we use, the help reports reads (where
  readable) and writes (where writable) with notify, and broadcasts
  [help]** -- in both services, all 406 keywords broadcast and every
  readable/writable keyword supports notify.

### 2.2 Threads

Importing `ktl` starts background daemon threads immediately
(`StartStop.initialize`), and registers an `atexit` cleanup:

| Thread | Count | Role |
| --- | --- | --- |
| **Usher** (`Usher.py`) | 1 per process | Executes **every** low-level `ktlc` call, one at a time, in FIFO order (some client libraries require all KTL calls from one thread). All `read`/`write`/`monitor` calls are queued to it. |
| **Dispatcher** (`Service.py:1054`) | 1 per service | `poll()`s the service's file descriptors; when events arrive, queues `ktl_dispatch` on the Usher, which updates `Keyword` values. |
| **Courier** (`Service.py:946`) | 1 per service | Runs user callbacks for keyword updates, outside the Usher, so callbacks may make KTL calls. |
| **Lumberjack**, **Janitor** | 1 each | KTL's own logging; cleanup of closed services. |
| **Collector** (`Keyword.py:2804`) | on demand | Polls keywords that can't broadcast (§3.3). |
| **FirstResponder/Paramedic** | on demand | Heartbeat monitoring and reconnection (§3.6). |

The single Usher is the most important fact for our code: **a ktlc call
that blocks holds up every other KTL call in the process, from every
thread** (`Usher.call`, `Usher.py:227` -- "If the invoked function never
returns ... all subsequent blocking invocations of call() will never
return"). For our services, notify support (§2.1) means reads and writes
hand off quickly and wait *outside* the Usher (§3.1, §3.2), so in normal
operation the Usher is held only briefly per call; a hung client library
call is still the failure mode to keep in mind.

## 3. The calls we use

### 3.1 `keyword.read()` -- `Keyword.read`, `Keyword.py:823`

```python
read(binary=False, both=False, wait=True, timeout=None)
```

- **Always a fresh round trip** to the service via the Usher, even if the
  keyword is monitored. It does *not* return a cached value.
- Returns `ascii` by default (a `str`), `binary` with `binary=True`, or
  `(binary, ascii)` with `both=True`.
- **Timeouts:** `timeout` is honored only if the service supports
  KTL_NOTIFY reads; otherwise it is **silently ignored** (a warning goes to
  KTL's own log, §3.7) and the read blocks inside the Usher. With notify
  reads -- which every keyword we use supports **[help]** -- the read waits
  outside the Usher, and a timeout raises `ktl.TimeoutException`. **Without
  a `timeout` argument, a read waits indefinitely** for the service's reply;
  all our reads pass one (`KTLService.read`, default 1 s, §4).
- Errors raise `ktl.ktlError` (or the original exception if it isn't a KTL
  error).
- **No named method returns the cached value.** A monitored keyword keeps
  its latest broadcast value locally, but the only way `ktl` offers to get
  at it without a round trip is dictionary-style access (§3.8), which we
  don't use. `KTL_SUGGESTIONS.md` §4 proposes a named method for it.
- `ktl` warns against calling `read()` on a keyword from inside that
  keyword's own callback (`Keyword.py:1869`). That applies to the original
  keyword object, not to what callbacks receive: `ktl` calls each callback
  with a **clone** (`Keyword.clone`, `Keyword.py:771`, queued to the Courier
  at `Keyword.py:1938`), and a clone's `read()` returns its frozen value
  before any locking or I/O (`Keyword.py:846`). Our callbacks read only the
  clone (§3.3).

### 3.2 `keyword.write(value)` -- `Keyword.write`, `Keyword.py:961`

```python
write(value, wait=True, binary=False, timeout=None)
```

- `value` is cast by keyword type first: `Numeric` → `float`, written as
  `repr()` (full precision); `Integer` → `int`; `Enumerated` → `str` (ascii
  writes accept an enumerator name); `Boolean` → a Python `bool` forces a
  binary write.
- **Does not update the local `Keyword` value.** The local value only
  changes from a `read()` or a broadcast (if monitored).
- **What "wait" waits for** depends on `notify_writes`:
  - *With* KTL_NOTIFY writes -- the case for every writable keyword we use
    **[help]**: the C call returns quickly; Python then waits (off the
    Usher) for the service's "second-stage" completion notification.
    `timeout` works and raises `TimeoutException`; a negative completion
    status raises `ktlError`.
  - *Without* KTL_NOTIFY writes: the C call itself blocks until the write
    completes, **inside the Usher thread**, and `timeout` is silently
    ignored. Every other KTL call in the process waits meanwhile.
- **Ascii vs. binary writes.** By default `value` is sent as ascii and
  parsed by the service. For keywords whose ascii form differs from their
  binary form -- notably the coordinate keywords, displayed sexagesimal but
  stored in radians (§4.1) -- **how the service parses an ascii write (e.g.
  whether `'5.5'` means 5.5 hours or something else) is [C-layer]**.
  `binary=True` sends the native value (radians) and avoids the question.
- **[C-layer]** What "complete" means is defined by each service's
  dispatcher -- e.g. whether a `POCSECPD` write completes when the value
  is accepted or when the secondary has finished moving. Our code doesn't
  rely on either: `Focus.set_to` waits for `POCSECLK` instead. Our writes
  (`KTLService.write`) pass no `timeout`, so they wait for completion
  however long it takes.
- Returns a positive sequence number (usable with `keyword.wait(sequence=...)`
  only for notify writes).

### 3.3 `keyword.monitor()` -- `Keyword.monitor`, `Keyword.py:1181`

```python
monitor(start=True, prime=True, wait=True)
```

- Subscribes to the service's broadcasts; each broadcast updates `binary`/
  `ascii` and runs callbacks on the Courier thread.
- `prime=True` performs a separate priming `read(wait=wait)` so the value is
  available immediately; `wait=False` makes that read non-blocking.
- If the keyword can't broadcast, `monitor()` falls back to **polling every
  1 s** via the Collector (`Keyword.poll`), which issues a non-blocking read
  per period.
- Monitoring state is per keyword and shared process-wide (singleton
  keywords): a second `monitor()` call on an already-monitored keyword does
  nothing. Once on, it stays on unless stopped with `monitor(start=False)`.
- `keyword.callback(function)` registers a callback (stored as a weak
  reference -- a bound method of an object that's garbage-collected silently
  stops being called). Exceptions in callbacks are logged, not raised.
- Callbacks run (on the Courier thread, with a clone of the keyword; §3.1)
  after every update: each broadcast received while monitored, **and each
  completed `read()`** (`Keyword.propagate`, `Keyword.py:1467`). So
  registering a callback and then reading the keyword delivers its current
  value to the callback.

### 3.4 `keyword.waitFor(expression, timeout)` -- `Keyword.py:1425`, `Expression.py`

```python
waitFor(expression, timeout=None, case=False)   # e.g. kw.waitFor('== allowed', timeout=0.5)
```

- Builds `"$service.KEYWORD <expression>"` and waits until it evaluates
  true, re-evaluating on every broadcast of the keyword.
- **Returns `False` on timeout -- it does not raise.** Returns `True` as
  soon as the expression is satisfied, immediately if it already is.
- **Side effect: starts monitoring.** If the keyword isn't monitored,
  `waitFor` calls `monitor(wait=False)`: the subscription starts, and the
  priming read is queued *without waiting for it*
  (`Expression.py:342`). Until that first value arrives, the expression
  evaluates to `None` (not true), so the wait continues.
  - **Consequence:** the first `waitFor` on an unmonitored keyword must
    receive its priming value *within the timeout*. With a short timeout
    (we use 0.5 s), a slow first read returns `False` -- a false
    negative -- even if the condition is actually true. Later calls on the
    now-monitored keyword are fine. Every keyword we `waitFor` is
    monitored and primed when its service object is constructed (§4), so
    our checks never hit this.
- **String comparisons are case-insensitive by default** (`case=False`):
  both sides are lower-cased before comparing (`Expression.py:633`), so
  `'== Ready'` matches `READY`. Plain Python comparisons in our own code
  (e.g. `read() == 'Yes'`) are **case-sensitive** -- not the same rule.
- **Which form is compared** (`Keyword._preCompare`, `Keyword.py:155`):
  - a number (`== 0`) against a keyword whose `binary` is numeric compares
    the `binary` value -- for an enumerated keyword, its index;
  - a word (`== allowed`) compares the `ascii` value (the enumerator name,
    for an enumerated or boolean keyword).
- **Transient values can be missed.** `waitFor` only sees values the
  service actually broadcasts, though it does evaluate every broadcast it
  receives, not just the latest. If a state passes too quickly to be
  broadcast, a wait for it never succeeds **[C-layer: whether the service
  broadcasts every transition]**. `Exposure.expose` waits for one such
  state, `EXPSTATE == Start` (§4.1).
- **Timeout can overshoot.** The loop waits up to the *full* timeout after
  each broadcast that doesn't satisfy the expression
  (`Expression.wait`, `Expression.py:690`), so a keyword that keeps
  updating can stretch a wait to nearly twice `timeout`.
- **`False` is ambiguous.** It means either "the keyword's value never
  satisfied the expression" or "the keyword never had a value" (e.g. the
  service didn't reply). A follow-up `read(timeout=...)` tells them apart:
  for our services a read timeout is honored (§3.1), so a
  `TimeoutException` means the service isn't replying, while a returned
  value means the condition really was false. See `KTL_SUGGESTIONS.md`
  §1-3 for proposed improvements to all three of these `waitFor`
  behaviors.
- Waiting happens on the calling thread (not the Usher), so a `waitFor` in
  a worker thread doesn't block other KTL calls.

### 3.5 Errors

| Exception | Raised when |
| --- | --- |
| `ktl.ktlError(description, code)` | A KTL call fails. Built from a `RuntimeError` whose text is `KTL::code::description` (`Exceptions.parse`); other `RuntimeError`s pass through unchanged. |
| `ktl.TimeoutException` | A read/write with a *honored* timeout expires (§3.1, §3.2). Not raised by `waitFor`. |
| `ktl.DeadlockError` | A blocking KTL call is made from the Usher thread itself (programming error). |
| `NotImplementedError` | Reading/writing a keyword that doesn't support it. |
| `KeyError` | Unknown keyword name for the service. |

### 3.6 Heartbeats and reconnection

`service.heartbeat(keyword, period)` designates a keyword expected to
broadcast at least every `period` seconds; a background FirstResponder
checks it and, if it stops while the service still answers a probe,
"resuscitates" the service (closes and reopens it, restarting monitoring).
`service.reconnect()` forces the same. Without a heartbeat, a dispatcher
restart may leave monitored keywords silently stale **[C-layer: whether
client libraries reconnect on their own]**. `heartbeat()` starts monitoring
the keyword if it isn't already, without waiting for its first value
(`Service.py`, `Heartbeat.__init__`), and registering the same keyword
twice is a no-op. A `period` shorter than the keyword's real broadcast
interval makes `ktl` resuscitate the service needlessly.

We register one heartbeat per service, after monitoring and priming it
ourselves (§4): `nickelpoco.POCHEART` ("poco's heartbeat") and
`nscicam.DISP0CLK` (the dispatcher's runtime clock, in seconds). The help
doesn't give their broadcast intervals, so both use a provisional, generous
30 s period until they're measured at the telescope. We don't call
`reconnect()`.

Heartbeat state: the FirstResponder marks a heartbeat *not beating* when it
misses its period (`Service.py:1474`); if the service then fails a probe,
it stays so until broadcasts resume (`Service.py:1771`). The named method
`keyword.isAlive()` (`Keyword.py:1150`) reports this for any keyword sharing
a server with a heartbeat keyword -- but it must be polled (there's no
notification), its first call does a blocking `servers` lookup, and it
raises `NoHeartbeatsError` if no heartbeat applies. We don't use it; see
`KTL_SUGGESTIONS.md` §5.

### 3.7 KTL's own logging

KTL logs through its own Lumberjack thread to `sys.stderr`, at level
`ERROR` by default -- separate from `nickel_focus.log`. Warnings such as
"timeout request ignored" are therefore invisible unless the level is raised
with `ktl.loglevel(ktl.LOG_WARN)` (or `LOG_DEBUG`).

### 3.8 Dictionary-style access -- not used by `nickel_focus`

`ktl` also exposes keyword state and metadata through `keyword[key]`
(`Keyword.__getitem__`, `Keyword.py:1638`; `keyword(key)` is an alias), and
writes and monitoring through assignment (`Keyword.__setitem__`,
`Keyword.py:1811`). `ktl` treats this as public: it uses it internally,
and its own comments recommend `keyword['ascii']` inside callbacks.
**`nickel_focus` deliberately doesn't use it**, because one syntax hides
very different costs and side effects:

| Keys | What a lookup does |
| --- | --- |
| `ascii`/`asc`, `binary`/`bin` | Returns the last value received -- current only if monitored. **If no value has arrived yet, does a blocking `read()`**; if the last update was an error, re-raises it. |
| `populated`, `monitored`/`monitor`, `timestamp`, `error`, `history`, `dictionary`/`dict` | Local state, recomputed on each lookup. |
| `alive` | Heartbeat check; raises `NoHeartbeatsError` if none is configured. |
| `name`, `service`, `reads`, `writes`, `broadcasts`, `notify_reads`, `notify_writes` | Fixed when the keyword is created. |
| `type`, `units`, `range`, `enumerators`/`enum`, `help`, `servers`, `keys` | **A blocking round trip to the service on first lookup**, cached afterward. |
| an integer or slice | Indexes into the `ascii` string. |

Assignment is worse: `keyword['ascii'] = v` (or `['binary']`) performs a
blocking **write**, and `keyword['monitored'] = True` **starts
monitoring**. The raw `.ascii`/`.binary` attributes are internals (no lazy
read, no error check) and are likewise off-limits.

Where we'd otherwise want this access, we use a named method instead
(`read(timeout=...)`, `write()`, `monitor()`) or, if none fits, propose one
in `KTL_SUGGESTIONS.md`.

## 4. How `nickel_focus` uses KTL

All `ktl` access goes through `nickel_focus/services/`. `services/base.py`
is the only module that imports `ktl`; its `KTLService` wraps the named
calls of §3 and every service class is built on it:

| `KTLService` method | `ktl` call | Notes |
| --- | --- | --- |
| `read(name, binary=False, timeout=1.0)` | `ktl.cache(...).read(binary=..., timeout=...)` | Every read has a timeout (`DEFAULT_READ_TIMEOUT`, 1 s, to be confirmed at the telescope); `TimeoutException` → `KTLTimeoutError`. |
| `write(name, value, binary=False)` | `.write(value, binary=...)` | No timeout (waits for completion). Raises `KTLReadOnlyError`, without calling `ktl`, on a `read_only=True` service. |
| `wait_for(name, expression, timeout)` | `.waitFor(expression, timeout=...)` | Returns `True`/`False`, as `waitFor` does (§3.4). |
| `check(name, expression, timeout)` | `wait_for`, then on `False` a `read()` (1 s) | `True`, `False` (the service answered), or `None` (it didn't). `services.require(result, false_message, no_reply_message)` turns `False` into `ValueError` and `None` into `KTLTimeoutError`. |
| `subscribe(names, function)` | `.callback(relay)`, then the monitor-and-prime step below | `relay(clone)` calls `function(service, keyword, clone.read())`; the service keeps a strong reference to each `relay` (§3.3). The priming read delivers the current value. |
| `heartbeat_alive(timeout)` | `read()`, then `waitFor('!= <value>', timeout)` | Whether the heartbeat keyword changes within `timeout`; used by the live tests. |
| construction, for each keyword in `monitored` | `.monitor(prime=False)`, then `read(timeout=1.0)` | Primes with a timed read instead of `monitor()`'s own (untimed) one, so an unresponsive service fails construction with `KTLTimeoutError` rather than hanging. |
| construction, for the `heartbeat` keyword | the same, then `ktl.cache(service).heartbeat(keyword, heartbeat_period)` | §3.6. |

`ktl.ktlError` (and `KeyError` from `ktl.cache`) become `KTLError`; each
exception is chained to `ktl`'s original. Values cross the boundary as
plain Python values; `Keyword` objects never leave `services/base.py`. A
service holds on to the `ktl` module it was constructed with.

The service classes and what uses them. The device classes take a service
object; the GUI `Controller` and the CLI scripts construct one `NickelPoco`
and one `NSciCam` and share them.

| Class | Keywords | Methods | Notes |
| --- | --- | --- | --- |
| `services.NickelPoco` | `POCSTOP`, `POCTRCK`, `POCOT`, `POCORAA`/`POCODECA`, `POCORAD`/`POCODECD`, `POCSECPA`/`POCSECPD`, `POCSECLK` | `movement_allowed`/`tracking`/`on_target`/`secondary_locked(timeout)` (`check`); `pointing()`, `secondary_position()`, `secondary_demand()` (`read`); `set_secondary_lock`, `set_secondary_demand`, `set_pointing_demand` (`write`) | Monitors `POCSTOP`, `POCTRCK`, `POCOT`, `POCSECLK`, `POCSECPA`, `POCORAA`, `POCODECA`; heartbeat `POCHEART`. Never writes `POCSTOP`. |
| `services.NSciCam` | `EXPSTATE`, `EXPOSE`, `DISP0CLK`; plus its helpers' keywords | `exposure_state()`, `exposure_state_is(state, timeout)` (`check`), `start_exposure()` (`EXPOSE = Go`) | Monitors `EXPSTATE`, `EXPOSURE`, `AMPCONF`, `BINNING`; heartbeat `DISP0CLK`. |
| `services.nscicam.ExposurePath` (`NSciCam.path`) | `RECORD`, `EXPRESULT`, `SCRATCHDIR`, `RECORDDIR`, `FITSPREFIX`, `OBSNUM`, `FITSSUFFIX` | `previous`, `next`, `for_obsnum` | `for_obsnum` compares `RECORD` with `== 'Yes'` (case-sensitive). |
| `services.nscicam.ExposureConfig` (`NSciCam.cfg`) | `RECORD`, `EXPOSURE`, `AMPCONF`, `AMPCONFLIST`, `BINNING` | `configure`, `exptime`, `current_settings`; `setting_value(key, ascii)` (class method) converts a broadcast value | Reads `AMPCONFLIST` at construction. `keywords` maps each setting to its keyword. |

| Where | Uses | Notes |
| --- | --- | --- |
| `focus.Focus` | `NickelPoco` | Each check goes through `require` (false → `ValueError`, no reply → `KTLTimeoutError`). `set_to` checks `movement_allowed(0.5)`, unlocks (`POCSECLK` → `off`), writes `POCSECPD`, then `secondary_locked(30)`. |
| `focus.FocusSequence.set_focus` | `NSciCam` | `exposure_state_is('Ready', 0.5)` before every focus move a sequence makes, then `Focus.set_to`. |
| `exposure.Exposure` | `NSciCam` | `expose` waits for `Ready` (15 s), configures, writes `Go`, then waits for `Start` (30 s) and `Ready` (exposure time + 90 s). |
| `slew.NickelTelescopePointing` | `NickelPoco` | `slew_to` checks `movement_allowed`/`tracking`/`on_target` (0.5 s each), writes the target (ascii decimal hours/degrees), then `on_target(300)`. |
| GUI `Controller` (subscriptions) | both | Status indicators and position display: `subscribe`s to `POCSTOP`, `POCTRCK`, `POCOT`, `POCSECLK`, `POCSECPA`, `POCORAA`, `POCODECA`, `EXPSTATE`; values reach the GUI thread through a queued Qt signal (`gui/ktl_bridge.py`). No polling. |
| GUI `Controller` (exposure fields) | `NSciCam` | `subscribe`s to `EXPOSURE`, `AMPCONF`, `BINNING`, so the Single/Grid/Auto fields follow the camera; a committed edit is written at once with `cfg.configure(...)`, on the GUI thread (no timeout). |

The same usage, by keyword. "Controller" means the GUI's subscriptions. Every
keyword that is checked with `waitFor` or displayed is also monitored (see
the service rows above). A
keyword used by more than one class is still a single shared `Keyword`
object (§2.1).

| Keyword | Used by | How |
| --- | --- | --- |
| `nickelpoco.POCSTOP` | `focus.Focus`, `slew.NickelTelescopePointing`, Controller | `waitFor('== allowed')` before moving the focus or slewing; subscribed to for the Slew indicator. |
| `nickelpoco.POCTRCK` | `slew.NickelTelescopePointing`, Controller | `waitFor('== on')` before slewing; subscribed to for the Track indicator. |
| `nickelpoco.POCOT` | `slew.NickelTelescopePointing`, Controller | `waitFor('== 0')` before and after slewing; subscribed to for the On Target indicator. |
| `nickelpoco.POCORAA` | `slew.NickelTelescopePointing`, Controller | Read for the current RA (`NickelPoco.pointing()`); subscribed to for the Slew tab's position display. |
| `nickelpoco.POCODECA` | `slew.NickelTelescopePointing`, Controller | Read for the current Dec, likewise; subscribed to likewise. |
| `nickelpoco.POCORAD` | `slew.NickelTelescopePointing` | Written with the target RA. |
| `nickelpoco.POCODECD` | `slew.NickelTelescopePointing` | Written with the target Dec. |
| `nickelpoco.POCSECPA` | `focus.Focus`, Controller | Read for the current focus; subscribed to for the Focus indicator's text. |
| `nickelpoco.POCSECPD` | `focus.Focus` | Read to skip a no-op move; written with the requested focus. |
| `nickelpoco.POCSECLK` | `focus.Focus`, Controller | Written `off` to unlock; `waitFor('== on')` for the move to finish; subscribed to for the Focus indicator's color. |
| `nscicam.EXPSTATE` | `focus.FocusSequence`, `exposure.Exposure`, Controller | `waitFor('== Ready')` before a focus move or exposure, `== Start`/`== Ready` during one; monitored by `NSciCam`; subscribed to for the Exp indicator. |
| `nscicam.EXPOSE` | `exposure.Exposure` | Written `Go` to start an exposure; not monitored. |
| `nickelpoco.POCHEART` | `NickelPoco` | Monitored; registered as the service's heartbeat (§3.6). |
| `nscicam.DISP0CLK` | `NSciCam` | Monitored; registered as the service's heartbeat (§3.6). |
| `nscicam.RECORD` | `ExposurePath`, `ExposureConfig` | Read to choose the record vs. scratch directory; written by `configure(record=...)`. |
| `nscicam.EXPRESULT` | `ExposurePath` | Read for the last exposure's file path (`.previous`). |
| `nscicam.OBSNUM` | `ExposurePath` | Read for the next observation number (`.next`). |
| `nscicam.RECORDDIR` | `ExposurePath` | Read to build a file path. |
| `nscicam.SCRATCHDIR` | `ExposurePath` | Read to build a file path. |
| `nscicam.FITSPREFIX` | `ExposurePath` | Read to build a file path. |
| `nscicam.FITSSUFFIX` | `ExposurePath` | Read to build a file path. |
| `nscicam.EXPOSURE` | `ExposureConfig`, Controller | Read/written as the exposure time; subscribed to for the exposure fields, and written when one is entered. |
| `nscicam.AMPCONF` | `ExposureConfig`, Controller | Read/written as the read speed; subscribed to for the exposure fields, and written when a speed is chosen. |
| `nscicam.AMPCONFLIST` | `ExposureConfig` | Read once, when `NSciCam` is constructed, for the valid read speeds. |
| `nscicam.BINNING` | `ExposureConfig`, Controller | Read/written as the binning; subscribed to for the exposure fields, and written when a binning is chosen. |

### 4.1 What the services' help says about these keywords

From `ktl_python/ktl_nickelpoco.txt` and `ktl_nscicam.txt` **[help]**. All
of these keywords broadcast, and every read and write listed supports
notify. "R" = readable, "W" = writable. Enumerated and boolean values are
listed as `index name`; the name is the `ascii` value and the index the
`binary` value.

| Keyword | Type | Values / format / units | Access | Notes |
| --- | --- | --- | --- | --- |
| `POCSTOP` | enum | `0 allowed`, `1 disabled` | R/W | "Stop motion." Writing any non-zero value **stops all motion**; a negative value **turns off motor power** (restored by writing 0). We only read it. |
| `POCTRCK` | bool | `0 off`, `1 on` | R/W | Tracking on/off. With tracking on, the desired RA/Dec position the telescope. |
| `POCOT` | int | `%d`: `0` on target and stable, `1` not yet reached, `-1` failed to reach target within the time limit | R | |
| `POCORAA` | double | ascii hours, `%02d:%02d:%05.2f`; binary radians | R | Actual observed RA, from the latest encoder values. |
| `POCODECA` | double | ascii degrees, `%02d:%02d:%04.1f`; binary radians | R | Actual observed Dec. The format has no explicit sign field; how a negative Dec -- especially between 0° and −1° -- appears in ascii is **[C-layer]**. |
| `POCORAD` | double | ascii hours, `%02d:%02d:%05.2f`; binary radians | R/W | Desired apparent RA ("POCHAD − sidereal time"). |
| `POCODECD` | double | ascii degrees, `%02d:%02d:%04.1f`; binary radians | R/W | Desired apparent Dec. |
| `POCSECPA` | double | `%.8f`, "made-up units" | R | Actual secondary-mirror position. |
| `POCSECPD` | double | `%.8f`, "made-up units" | R/W | Desired secondary-mirror position. |
| `POCSECLK` | bool | `0 off`, `1 on` | R/W | Secondary-mirror lock. |
| `EXPSTATE` | enum | `0 Ready`, `1 Error`, `2 Start`, `3 Exposing`, `4 Readout`, `5 Paused`, `6 Aborting`, `7 Unknown` | R | Derived from the acquisition phase: `Ready` = done, `Start` = starting (likely brief), then `Exposing`, then `Readout`. |
| `EXPOSE` | enum | `0 None`, `1 Abort`, `2 Stop`, `3 Start`, `4 StartX`, `5 Go` | R/W | `Go` is a "smart start": `StartX` or `Start` depending on the trigger mode. `Abort` discards the current exposure. |
| `RECORD` | bool | `0 No`, `1 Yes` | R/W | Whether files are recorded (vs. scratch). |
| `EXPRESULT` | string | | R | On success, "the FITS filename" (whether a full path isn't stated); **on error, the filename followed by a message** from the camera library. |
| `OBSNUM` | int | | R/W | Auto-incremented after each exposure. Separate values are kept for Guider and NonGuider modes. |
| `RECORDDIR`, `SCRATCHDIR` | string | | R/W | Directories for recorded/scratch files. |
| `FITSPREFIX`, `FITSSUFFIX` | string | | R/W | Filename prefix/suffix. Separate `FITSPREFIX` values are kept for `RECORD` = No/Yes. |
| `EXPOSURE` | double | `%.3f` s, range 0 .. ∞ | R/W | The camera may increase the requested time slightly to a valid value. |
| `AMPCONF` | string | | R/W | Amplifier and read speed; valid values come from `AMPCONFLIST`. The help doesn't list them, so our `Fast`/`Slow` aliases (`1.0MHz`/`0.05MHz`) are unconfirmed. |
| `AMPCONFLIST` | string | comma-separated | R | Valid `AMPCONF` values. |
| `BINNING` | enum | `0 1,1`, `1 2,2`, `2 4,4` | R/W | Matches `ExposureConfig.expbin_options` and the GUI's binning options. |

Not used by us, but relevant: `nscicam.FITSFILE` (string, R) -- "Full path
of latest FITS file", set only **after** the exposure completes *and* the
dispatcher has finished supplementing the FITS header; the help says it is
"the keyword that other applications should track and use".
`nscicam.FILENAME`, by contrast, is set when the exposure begins.

## 5. Version history

| Version | Date | Summary |
| --- | --- | --- |
| `a33f3dd` | 2026-11-17 | Initial reference, from KTL Python 5.2 (`ktl_python/`) and the `nickelpoco`/`nscicam` keyword help; dictionary-style access documented and excluded from our use. |
| `b247c45` | 2026-11-17 | Moved the former §5 ("Implications and open questions") into `claude/ktl_interface_refactor.md`; this document now covers only current use and reference material. |
| `4a21d56` | 2026-11-17 | §1, §3.1, §3.2, §4 updated for the `services/` layer: the only `ktl` import (now catching any `ImportError`), 1 s read timeouts, timed priming reads, and the per-class usage tables. |
| `6227274` | 2026-11-17 | §3.4, §3.6, §4: keywords monitored at construction, heartbeats (`POCHEART`, `DISP0CLK`, provisional 30 s), `check`/`require`/`heartbeat_alive`. |
| `a37e42c` | 2026-11-17 | §3.1, §3.3, §4: callbacks (clones, delivery after reads), `KTLService.subscribe`, and the GUI's subscriptions replacing its 1 Hz poll; §3.6: heartbeat state and `isAlive()`. |
| *pending* | 2026-11-17 | §4: the exposure fields' subscriptions and writes; `ExposureConfig.setting_value`/`keywords`. |
