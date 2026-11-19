# Pointing offset: development plan

## 1. Goal

Correct the telescope pointing from a Single exposure:

1. The observer takes a Single exposure, hovers over the star that *should*
   be at the center of the image, and presses **p**.
2. The GUI finds that star's centroid and works out the offsets that would
   move it to the image center. It draws a line from the star to the image
   center on the image panel and shows the offsets.
3. The observer presses **Offset by …** on the Slew tab. The GUI
   applies the offsets through `nickelpoco` and then marks the new position
   as on target. There is no further confirmation step.
4. The observer takes another Single exposure to check. If the star still
   isn't centered, they repeat steps 1–3.

Detailed testing needs telescope time. The aim now is to have all the code
in place, tested against the fake `ktl`. Anything we're unsure of is a named
constant (§4), so changing it at the telescope doesn't touch the logic.

## 2. Keywords

From `ktl_python/ktl_nickelpoco.txt` and Will Deich's description. All are
`nickelpoco` keywords:

| Keyword | Type | Use |
|---|---|---|
| `POCGDFIN` | int, read/write | Guider move finished. Writing any value sets it to 1. It becomes 0 when the move completes, or -1 if the move fails. |
| `POCGDIX` | double (rad), write | Increase the guider offset in X (RA when the guider rotation `POCGDRO` is 0). |
| `POCGDIY` | double (rad), write | Increase the guider offset in Y (Dec when `POCGDRO` is 0). |
| `POCGDOT` | int, write | Tell poco "the current position matches the desired position, and the guide offsets should be set accordingly"; that is, mark the new position as on target. |
| `POCOT` | int, read-only | On target: 1 not yet, 0 on target and stable, -1 failed. Already subscribed to for the status bar. |
| `POCSTOP`, `POCTRCK` | | The existing "movement allowed" and "tracking on" checks. |

Offsets are written as binary radians, like the slew coordinates.

## 3. Offset sequence

`NickelPoco.apply_guider_offset(east, north, timeout)`, where `east` and
`north` are on-sky offsets in arcseconds:

1. **Checks.** Movement must be allowed and tracking must be on (as before a
   slew). If not, raise with a clear message.
2. **Arm.** Write `POCGDFIN` (any value), then wait for `POCGDFIN == 1`. The
   wait makes sure an old 0 left over from an earlier move can't be taken as
   this move's completion.
3. **Offset.** Write `POCGDIX` and `POCGDIY`.
4. **Wait.** Wait for `POCGDFIN` to be 0 or -1 (`POCGDFIN != 1`), with a
   timeout. A -1 or a timeout is an error, and the sequence stops there.
5. **Mark on target.** Write `POCGDOT`, then wait for `POCOT == 0`, with a
   timeout. If `POCOT` doesn't reach 0, log a warning but don't treat it as a
   failure: the offset itself succeeded.

By default, step 3 writes both offsets and step 4 waits once. If the
telescope needs them one at a time, `GUIDER_OFFSET_IN_SERIES = True` repeats
steps 2–4 for each axis: X first, then Y. An axis whose offset is zero is
skipped.

Each step is logged, e.g. "Applying pointing offset: 12.3 arcsec E, 4.5
arcsec N", "Pointing offset applied; marked on target."

## 4. Constants to confirm at the telescope

These go at the top of `services/nickelpoco.py` (keywords and behaviour) and
`pointing.py` (geometry). Each has a comment saying it's unverified.

| Constant | Default | What could change |
|---|---|---|
| `GUIDER_OFFSET_IN_SERIES` | `False` | Whether X and Y must each be armed, written and waited for separately. |
| `GUIDER_OFFSET_SIGN` | `(+1, +1)` | Whether a positive `POCGDIX`/`POCGDIY` moves the pointing East/North or the opposite way. |
| `GUIDER_OFFSET_COS_DEC` | `False` | Whether `POCGDIX` is in true RA (needs East offset ÷ cos Dec) or in plate units. The docs don't say for the guider keywords; the instrument keywords say plate units. |
| `GUIDER_MOVE_TIMEOUT` | 60 s | How long to wait for `POCGDFIN`. |
| `ON_TARGET_TIMEOUT` | 60 s | How long to wait for `POCOT == 0` after `POCGDOT`. |
| `MAX_POINTING_OFFSET` | 5 arcmin | Larger offsets are refused, since they're more likely a wrong star than a pointing error. |

`POCGDOT`'s value, and whether `KTLService.write` already waits for the move
to finish when it writes `POCGDIX`, also need checking. If it does, step 4
still works; it just returns at once.

## 5. Offset calculation (`nickel_focus/pointing.py`, new)

No GUI or `ktl` code, so it can be used from a script later.

- **Center.** The geometric center of the image in 0-based pixel
  coordinates, where each pixel's center is at an integer: `x0 = (nx - 1)/2`,
  `y0 = (ny - 1)/2`. For an even size this falls on the edge between the two
  middle pixels, splitting the detector exactly in half (e.g. 1043.5 for 2088
  columns, the width of a full, unbinned frame). The binned frames follow from
  their own shapes. A reference pixel other than the center would only need this
  one function changed.
- **Scale.** `ARCSEC_PER_UNBINNED_PIXEL` (0.1665, from `focus.py`) times the
  binning from the header (`StepResult.binning`). If the binning is unknown,
  there's no offset, and a warning says why.
- **Direction.** RA and Dec both increase toward *decreasing* pixel number
  (East at low column, North at low row; the image panel shows North up,
  East left). The telescope must move to point at the star:

  ```
  east  = -(x_star - x0) * scale     # arcsec; positive = point further East
  north = -(y_star - y0) * scale     # arcsec; positive = point further North
  ```

- **Limit.** If `hypot(east, north)` exceeds `MAX_POINTING_OFFSET`, the
  offset is refused with a warning.

Returns a small dataclass: the star and center pixels, `east`, `north`, and
the exposure it came from.

## 6. Snapping to the star

The photometry already finds every source in each exposure
(`image_quality` returns its source table), but `StepResult` keeps only the
measured star. Add `StepResult.sources`, an (N, 2) array of the detected
centroids (`CENX`, `CENY`), so **p** needs no new source detection.

When **p** is pressed, the GUI takes the detected centroid nearest the
cursor, if one lies within `SNAP_RADIUS` (10 binned pixels). If none does,
it warns "No star found near the cursor", and nothing else changes.

## 7. GUI

**Image panel (`image_panel.py`):**
- **p** works while nothing is running, like **m**, using the same
  enable/disable switch. It emits `pointingStarSelected(x, y)` with the
  cursor's data coordinates. Snapping happens in the Controller, which has
  the result.
- `show_pointing_offset(offset)` and `clear_pointing_offset()`. The offset
  is drawn as a line from the star to the image center, a small circle on
  the star and a cross at the center. Next to it is a label with the offsets,
  e.g. `12.3″ E, 4.5″ N` (W/S for negative). It's tied to the exposure it
  was measured on, so it's drawn only while that exposure is displayed. The
  color (e.g. cyan) differs from the red and yellow measurement boxes.
- **m** and **r** are unchanged.

**Slew tab (`focus_control_panel.py`):**

- Add a button below the **Move to Target** button.  It is labeled
  **Offset by —** (an em dash, like the tab's other "no value" fields) and
  inactive when no pointing offset is ready.  The label
  changes to, e.g., **Offset by 12.3″ E, 4.5″ N** and the button is enabled
  when an offset is pending and nothing is running.  When clicked, it emits
  `applyPointingOffsetRequested`.  Whenever the pending offset is cleared
  (see the Controller below), the label returns to **Offset by —**.
- Offsets are always in arcseconds, with one decimal, in both the button and
  the image-panel label.  The longest label allowed by the limit, **Offset by
  300.0″ W, 300.0″ S**, was checked (Fusion, cocoa and offscreen): it needs
  about 190 px, against about 310 px for the button at the default window
  size (225 px at the window's minimum), and adding the button changes
  neither the window's minimum width nor the control panel's height.  If
  the label becomes a problem, revisit the units.

**Controller:**
- `_on_pointing_star_selected(x, y)`: snap (§6) and calculate (§5) using the
  displayed exposure. Then store the pending offset, show it in the panel and
  the Slew tab, and log it.
- `apply_pointing_offset()`: run `NickelPoco.apply_guider_offset` on a
  `PointingOffsetWorker` thread, like `SlewWorker`, with the same
  one-operation-at-a-time rule and the Quit/Interrupt behaviour of a slew.
  Afterwards, log success or the error. Either way, clear the pending offset
  and the line, and remind the observer to take a Single exposure to check.
- The pending offset is also cleared by a new **p**, a slew, a new sequence
  or Replay, and resetting the image panel.
- Without a `ktl` connection, **p** still calculates and draws the offset
  (useful with Replay), but the button stays disabled.

## 8. Tests

All on the fake `ktl`; every assert has a message.

- **Fake `ktl`:** `POCGDFIN` goes to 1 when written, and to 0 (or -1, on
  request) once the offsets arrive. `POCGDOT` sets `POCOT` to 0. Writes are
  recorded, so tests can check the order, the values and the binary flag.
- **Service:** the order (arm, offsets, wait, `POCGDOT`); the values in
  radians, with the sign and cos(Dec) constants applied; series mode; a zero
  axis skipped; -1 and timeout raise; `POCOT` not reaching 0 warns; checks
  refuse when movement isn't allowed or tracking is off; read-only services
  refuse.
- **Calculation:** the direction (a star up and left of center gives an East
  and North offset); binning scaling; unknown binning; the limit; the
  center for odd and even sizes.
- **Snapping:** nearest source within the radius; none within the radius.
- **GUI:** **p** emits only while enabled. The line and label are drawn on
  their own exposure only and cleared correctly. The button is enabled only
  with a pending offset and nothing running. Apply runs the sequence and
  clears the offset. A failure is logged and still clears the offset.

## 9. Docs

- `GUI_REFERENCE.md` (§3 modules, version history), `KTL_REFERENCE.md` (the
  new keywords and sequence), `GUI_IMPLEMENTATION.md`.
- User manual (`docs/html/focusGUI.html`) and the in-app Help tab: the
  workflow in §1.
- `ktl_interface_refactor.md` §5.2 (checks at the telescope): the constants
  in §4.

## 10. At the telescope

`tools/telescope/check_guider_offset.py` runs the offset sequence with these
settings given on the command line, pausing for images before and after.

1. Confirm the keywords and sequence with Will (in particular `POCGDOT`'s
   value, and whether X/Y must be done in series).
2. Apply a small known offset (e.g. 10″ E, then 10″ N) and check, in the
   next Single exposure, that the star moved the expected direction and
   distance. Set `GUIDER_OFFSET_SIGN` and `GUIDER_OFFSET_COS_DEC` from that.
   Do the second check at a high Dec, where cos(Dec) makes a difference.
3. Check that `POCOT` reads 0 after `POCGDOT`, and that the Slew tab's
   current RA/Dec read as expected afterwards.
4. Run the full workflow in §1 on a pointing star.

## 11. As built (2026-11-18)

Implemented as planned, with these details:

- **Where the sequence lives.** `NickelPoco` has the keyword steps:
  `apply_guider_offset(east, north)` (§3 steps 2–4, including series mode
  and skipping a zero axis), `mark_guider_on_target()` (step 5: writes
  `POCGDOT = 1`, then `on_target`), and `guider_offset_radians` (sign and
  cos(Dec)). `slew.NickelTelescopePointing.apply_offset(east, north)` adds the
  step 1 checks and the logging, like `slew_to`. The §4 constants are at the
  top of `services/nickelpoco.py`; `MAX_POINTING_OFFSET` and `SNAP_RADIUS`
  are in `pointing.py`.
- **Waits.** Arming and completion use `check`/`require`, so a value that
  never arrives (`ValueError`) is told apart from a service that doesn't
  answer (`KTLTimeoutError`). After `POCGDFIN != 1`, it's read once more: any
  value other than 0 is a failure.
- **Clearing.** The pending offset (and its line) stays while the offset
  is being applied, and is cleared when the worker finishes, whether or not
  it succeeded. A failed snap, unknown binning, or too large an offset
  leaves any earlier pending offset in place.
- **Drawing.** The offset is drawn on every displayed exposure the same
  size as the one it was measured on (the same image center), not only on
  that exposure, since a sequence's exposures share the field. The label is
  above the star, or below it for a star south of the center, so it never
  crosses the line.
- **Fake `ktl`.** `Simulator` has `guider_offsets`, `guider_arms`,
  `guider_move_succeeds`, `guider_move_finishes`, `on_target_marks`, and
  `marks_on_target`.
- **Tests.** `tests/test_pointing.py` (calculation, snapping, keyword
  sequence, `apply_offset`) and new tests in `tests/gui/` (image panel,
  Slew-tab button, Controller).
