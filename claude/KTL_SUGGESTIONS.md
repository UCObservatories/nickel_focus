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
- **Interim approach:** call `keyword.monitor()` (blocking priming read)
  once, when our class is constructed, before any `waitFor` -- as
  `exposure.Exposure` already does for `EXPSTATE`.
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
  `waitFor` returning promptly at `T` (e.g. for user-facing timing).
- **Suggested change:** wait for the *remaining* time on each pass:
  `action.wait(remaining)` (with `remaining` computed before waiting), so
  the total never exceeds `timeout`.

## 3. Let `Keyword.waitFor` distinguish "false" from "no value"

- **Status:** draft
- **Need:** When a check like `waitFor('== allowed', timeout=0.5)` fails,
  we report it to the observer ("Telescope movement is disabled!"). That
  message is wrong if the real cause is that the service never replied.
- **Current behavior:** `waitFor` returns `False` both when the keyword had
  a value that never satisfied the expression and when the keyword never
  had a value at all (`Expression.evaluate` returns `None` for an
  unpopulated keyword, `Expression.py:546`; `Expression.wait` treats
  anything but `True` as unsatisfied).
- **Interim approach:** after a `False` result, follow up with a short
  `keyword.read(timeout=...)`. Both our services honor read timeouts
  (`KTL_REFERENCE.md` §3.1), so a `TimeoutException` means the service
  isn't replying, while a returned value means the condition really was
  false; report each accordingly. This costs one extra round trip, only on
  the failure path.
- **Suggested change:** either return a distinct value (e.g. `None`) when
  the timeout expires with no value ever received, or add a
  `raise_on_no_value=True` option that raises `ktl.TimeoutException` (or a
  new exception) in that case. Returning `None` would keep existing
  `if not kw.waitFor(...)` callers working unchanged.

## 4. A named method for a monitored keyword's cached value

- **Status:** draft
- **Need:** The GUI polls eight keywords once a second to drive its status
  indicators and position display. Each `read()` is a round trip to the
  service; for keywords we monitor anyway, the latest value is already
  held locally and could be shown with no I/O at all -- and so could never
  freeze the GUI.
- **Current behavior:** `Keyword.read()` (`Keyword.py:823`) always makes a
  round trip, even for a monitored keyword. The cached value is reachable
  only through dictionary-style access, `keyword['ascii']`/`['binary']`
  (`Keyword.__getitem__`, `Keyword.py:1638`), which we don't use: it falls
  back to a *blocking* read if no value has arrived yet and re-raises a
  stored error, behaviors that aren't visible at the call site. Whether a
  value has arrived (`['populated']`) and when (`['timestamp']`) are
  likewise only available that way.
- **Interim approach:** `keyword.read(timeout=...)` with a short timeout,
  treating a `TimeoutException` as "unavailable". Still a round trip per
  keyword per poll, but it can no longer hang the GUI.
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
