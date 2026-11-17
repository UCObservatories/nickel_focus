# KTL API Suggestions

Proposals for new or changed methods in the `ktl` package, collected while
developing `nickel_focus`.

**Why this document exists.** `nickel_focus` restricts itself to `ktl`'s
named public methods: `ktl.cache`, the `Keyword` methods (`read`, `write`,
`monitor`, `waitFor`, `callback`, ...), `Service.heartbeat`, and
`ktl.logger`/`ktl.loglevel`. We never work around a method's behavior by
reaching into lower-level objects (`keyword.ktlc`, the `Usher`,
`Expression` internals, private attributes), and we deliberately don't use
`ktl`'s dictionary-style keyword access (`keyword['ascii']`, `['populated']`,
...) either, because it hides blocking reads, round trips, and writes
behind one syntax (`KTL_REFERENCE.md` §3.8). When the named methods don't
provide what we need, the need is recorded here as a proposal instead,
along with how we cope in the meantime using only those methods.

Line references are to the read-only reference copy of KTL Python 5.2 in
`ktl_python/`; see `claude/KTL_REFERENCE.md` for background.

Each proposal gives the need, the current behavior, our interim approach
(named public methods only), and the suggested change. **Status** is one of *draft*
(not yet reviewed by us), *ready* (agreed, not yet sent), *sent*, *adopted*,
or *withdrawn*.

## 1. Let `Keyword.waitFor` wait for the keyword's first value

- **Status:** draft
- **Need:** A short check such as `kw.waitFor('== allowed', timeout=0.5)`
  should mean "is this condition true now (allowing up to 0.5 s)?". On a
  keyword that isn't monitored yet, it can instead return `False` simply
  because the keyword's first value hasn't arrived -- a false negative.
- **Current behavior:** `Keyword.waitFor` (`Keyword.py:1425`) builds an
  `Expression` with `wait=False` (`Keyword.py:1437`). That starts
  monitoring with a *non-blocking* priming read (`Expression.py:342`) and
  skips the up-to-one-second wait for keyword values that `Expression`
  otherwise performs (`Expression.py:418`). Until the priming value
  arrives, the expression evaluates to `None`, and if that takes longer
  than `timeout`, `waitFor` returns `False`.
- **Interim approach (implemented):** every keyword we `waitFor` is
  monitored and primed when its service object is constructed, before any
  check runs: `KTLService` calls `keyword.monitor(prime=False)` and then
  `keyword.read(timeout=1.0)` for each keyword in the service's `monitored`
  list (`services/base.py`; `NickelPoco` monitors `POCSTOP`, `POCTRCK`,
  `POCOT`, `POCSECLK`, ...; `NSciCam` monitors `EXPSTATE`, ...). Priming
  with our own timed read, rather than `monitor()`'s untimed one, also means
  an unresponsive service fails construction instead of hanging it. The
  cost is that every keyword we might check has to be listed up front, and
  the false negative remains for any keyword that isn't.
- **Suggested change:** add a `prime` argument,
  `waitFor(expression, timeout=None, case=False, prime=True)`, which, when
  the keyword has no value yet, waits for the priming read (within
  `timeout`) before evaluating. Defaulting to `True` makes the common case
  correct; `prime=False` keeps today's behavior.

## 2. Bound `waitFor`'s total wait by its `timeout`

- **Status:** draft
- **Need:** `waitFor(..., timeout=T)` should return within about `T`
  seconds.
- **Current behavior:** `Expression.wait` (`Expression.py:690`) waits up to
  the *full* `timeout` after every update that doesn't satisfy the
  expression (`action.wait(timeout)`, `Expression.py:718`), checking the
  elapsed time only when it wakes. A keyword that keeps updating without
  satisfying the expression can stretch the wait to nearly `2T`.
- **Interim approach:** none needed for correctness; we just don't rely on
  `waitFor` returning promptly at `T`. All our waits go through
  `KTLService.wait_for`/`check` (`services/base.py`). The short checks
  (0.5 s) are on keywords that rarely update; the long waits -- `POCOT`
  for up to 300 s after a slew, `EXPSTATE` for up to the exposure time plus
  90 s -- are on keywords that update repeatedly while waiting, so they're
  the ones that can overshoot, by up to their full timeout.
- **Suggested change:** wait for the *remaining* time on each pass:
  `action.wait(remaining)` (with `remaining` computed before waiting), so
  the total never exceeds `timeout`.

## 3. Let `Keyword.waitFor` distinguish "false" from "no value"

- **Status:** draft
- **Need:** When a check like `waitFor('== allowed', timeout=0.5)` fails,
  we report it to the observer ("Telescope movement is disabled!"). That
  message is wrong if the real cause is that the service never replied, so
  we need to tell the two apart.
- **Current behavior:** `waitFor` returns `False` both when the keyword had
  a value that never satisfied the expression and when the keyword never
  had a value at all (`Expression.evaluate` returns `None` for an
  unpopulated keyword, `Expression.py:546`; `Expression.wait` treats
  anything but `True` as unsatisfied).
- **Interim approach (implemented):** `KTLService.check(name, expression,
  timeout)` (`services/base.py`) calls `waitFor` and, on `False`, follows
  up with `keyword.read(timeout=1.0)`. Both our services honor read
  timeouts (`KTL_REFERENCE.md` §3.1), so a `TimeoutException` means the
  service isn't replying (`check` returns `None`), while a returned value
  means the condition really was false (`False`). `services.require` turns
  these into a `KTLTimeoutError` ("No reply from nickelpoco.POCSTOP; ...")
  or the usual `ValueError`. Every check in `Focus.set_to`,
  `FocusSequence.set_focus`, `NickelTelescopePointing.slew_to`, and
  `Exposure.expose` goes through it. This costs one extra round trip -- up to
  1 s more -- only on the failure path.
- **Suggested change:** either return a distinct value (e.g. `None`) when
  the timeout expires with no value ever received, or add a
  `raise_on_no_value=True` option that raises `ktl.TimeoutException` (or a
  new exception) in that case. Returning `None` would keep existing
  `if not kw.waitFor(...)` callers working unchanged.

## 4. A named method for a monitored keyword's cached value

- **Status:** draft -- **weaker need since the GUI moved to callbacks.**
  Originally motivated by the GUI's 1 Hz polling, which is gone; what
  remains is convenience and stale-data detection (below). Worth keeping
  as a low-priority suggestion.
- **Need:** For a keyword we monitor anyway, the latest value is already
  held locally, and getting it should need no round trip to the service.
  Today we work around the lack of such a method in two ways: the GUI keeps
  its own copy of every value its subscriptions deliver, and some code
  makes a round trip for a value `ktl` already has. Knowing *when* that
  value arrived would also let a caller notice stale data (see also §5).
- **Current behavior:** `Keyword.read()` (`Keyword.py:823`) always makes a
  round trip, even for a monitored keyword. The cached value is reachable
  only through dictionary-style access, `keyword['ascii']`/`['binary']`
  (`Keyword.__getitem__`, `Keyword.py:1638`), which we don't use: it falls
  back to a *blocking* read if no value has arrived yet and re-raises a
  stored error, behaviors that aren't visible at the call site. Whether a
  value has arrived (`['populated']`) and when (`['timestamp']`) are
  likewise only available that way. (Inside a callback, the clone `ktl`
  passes in does return its value from `read()` without I/O
  (`Keyword.py:846`) -- but only there.)
- **Interim approach (implemented):**
  - The GUI's status bar, position display, and exposure fields get their
    values from `KTLService.subscribe` callbacks, and the Controller keeps
    the latest value of each in its own `_latest` dictionary -- a
    duplicate of what `ktl` already caches.
  - Values needed on demand use `KTLService.read`, a round trip with a 1 s
    timeout, even when the keyword is monitored: e.g. the current pointing
    for "Find nearest ..." (on the GUI thread), `Focus.current`
    (`POCSECPA`), and the follow-up read in `KTLService.check` (§3).
  - Nothing detects stale data; an indicator just keeps the last value it
    received.
- **Suggested change:** a method that never does I/O, e.g.

  ```python
  Keyword.cached(binary=False, both=False)
  ```

  returning the last received value (in the same forms as `read()`), or
  `None` if none has arrived, without triggering a read. A companion
  `Keyword.updated()` (or a `timestamp=True` option) returning the time of
  that value would let callers detect stale data, e.g. after a dispatcher
  restart. Errors from the last update could be raised, as `read()` does,
  or exposed separately -- either way, documented on the method.

## 5. Notification when a service's heartbeat stops or resumes

- **Status:** draft -- **usefulness unconfirmed.** We don't yet know
  whether this matters in practice: it depends on how often a service
  stops broadcasting outright (rather than restarting, which `ktl`'s
  heartbeat handling already recovers from) and whether observers would act
  on a stale display. Worth revisiting after some time at the telescope;
  withdraw it if the case never comes up.
- **Need:** The GUI's status indicators follow keyword broadcasts
  (`Keyword.callback`). If a service stops broadcasting altogether, the
  indicators keep showing the last values received -- they stop changing
  rather than showing that the data is stale. We'd like to turn them red
  (or otherwise mark them stale) when the service's heartbeat stops, and
  restore them when it resumes.
- **Current behavior:** `ktl` already tracks this. Each registered
  heartbeat (`Service.heartbeat`) has a `beating` flag: the `FirstResponder`
  thread sets it to `False` when the keyword hasn't broadcast within its
  period (`Service.py:1474`), probes the service, and resuscitates it if
  the probe succeeds; if the service is truly gone, the flag stays `False`
  until broadcasts resume and `Heartbeat.beat` sets it back to `True`
  (`Service.py:1771`). The named method `Keyword.isAlive()`
  (`Keyword.py:1150`) reports it, but:
  - it must be **polled** -- nothing notifies a caller when the state
    changes;
  - its **first call does a blocking round trip** to associate the keyword
    with heartbeats (it looks up `self['servers']` for the keyword and each
    heartbeat keyword, `Keyword.py:1162-1165`), so a first call from a GUI
    thread can stall;
  - it raises `NoHeartbeatsError` if no heartbeat shares one of the
    keyword's servers (`Keyword.py:1172`);
  - the state only changes when the `FirstResponder` wakes, so detection
    lags by up to one heartbeat period.
- **Interim approach:** none implemented; the indicators simply stop
  changing (a known limitation, noted in `ktl_interface_refactor.md`
  §4.3). The heartbeats themselves are registered (`POCHEART`, `DISP0CLK`,
  provisional 30 s periods), so `ktl` already reconnects after a dispatcher
  restart. If needed before any `ktl` change, we could call `isAlive()` on
  one keyword per service, once at startup on a non-GUI thread (to absorb
  its first-call round trip), and then poll it from a slow timer --
  named methods only, though polling is what the callback-driven design
  set out to avoid.
- **Suggested change:** a callback for heartbeat state changes, e.g.

  ```python
  Service.heartbeatCallback(function, remove=False)
  ```

  calling `function(service_name, keyword_name, beating)` on the Courier
  thread whenever a heartbeat's `beating` flag changes (stops, or
  resumes). Alternatively, `Keyword.isAlive(wait=False)` that never does
  I/O (returning `None` if the association hasn't been made yet) would at
  least make polling safe from a GUI thread.
