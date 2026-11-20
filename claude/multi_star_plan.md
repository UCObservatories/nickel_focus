# Multiple stars on the focus curve: development plan

## 1. Goal

Let the observer choose which stars feed the focus curve after a sequence
(or Single exposure), without re-running the photometry each time:

- **m** (unchanged): mark the star nearest the mouse and remeasure every
  exposure from its file. As for any measurement, every detected star is
  measured (and, after this change, kept); the plotted and fitted data
  becomes just the star nearest the mark in each exposure. The mark lasts
  until a slew, and seeds later sequences.
- **a** (new): add the star nearest the mouse to the plotted data, matched
  by position in every exposure, and refit. If that star is already in the
  data, it's matched again across the exposures instead of added twice.
- **d** (new): remove the star nearest the mouse from the plotted data and
  refit.

**a** and **d** use photometry already in memory, so they're quick. To see
which points belong to a star, the observer can press **a** and **d** on it
in turn ("blinking" its points on and off the curve). If that turns out to be
too slow, it will be reassessed.

Along the way, the photometry is separated from making cutouts, the GUI
stops making cutouts, and the CLI makes them only when it plots.

## 2. Decisions

| Question | Decision |
|---|---|
| Fit | One quadratic through every selected star's points, pooled. |
| Colors | Every star the same color. The boxes keep their current meaning: red normally, yellow (with "Outlier centroid") for a position outlier (§5.4). No fit-outlier rejection (§10). |
| Matching across exposures | Within 10 binned pixels (`MATCH_RADIUS`); an exposure with no source that close gives that star no point. |
| **a** on a star already selected | That star's points are replaced by a new match from its position in the displayed exposure. E.g. **a** on the brightest star re-matches it by position, without remeasuring the way **m** does. |
| **d** | May remove any star, including the first (the brightest, or the one marked with **m**). Removing it doesn't change what seeds the next sequence: still the **m** mark (until a slew), otherwise the brightest. |
| Nothing left | **d** may remove every star. Then the curve is empty, a warning says there's nothing to fit, and **a** can add a star again. |
| Scope | Added and removed stars apply only to the loaded sequence or Single exposure. A new sequence, Replay, or Single exposure starts again from the first star alone. |
| Single tab FWHM | The mean over the selected stars in that exposure. |
| After a refit | Log the new best focus and set it as the Single tab's default (as at the end of a sequence). |
| `weighted` method | Left as it is. |

## 3. Photometry and cutouts (`photometry.py`, `focus.py`)

`image_quality` currently detects and measures all sources, chooses one, and
cuts its stamp. Split into:

- `measure_sources(fits_file)` → `raw, bkg, src_data`: read, background,
  detection, and every source's measurements (the existing `find_sources`
  plus `evaluate_sources`).
- `select_source(src_data, method)` → the chosen row: `'brightest'`
  (largest `CNTS`) or an `(x, y)` tuple (nearest). `'weighted'` stays in
  `image_quality` as it is.
- `source_quality(src_data, row)` → the FWHM, 2.355 × `(SIGX + SIGY)/2` (as fixed afterward), the image-quality
  value used everywhere.
- `extract_stamp` is unchanged, and is no longer called by the photometry.
- `image_quality(fits_file, method)` remains, built from the above, without
  the stamp: `raw, bkg, src_data, img_quality, coords`.

`focus.py`:

- `StepResult.sources` becomes the full source table (an `astropy` `Table`:
  `CENX`, `CENY`, `SIGX`, `SIGY`, `CNTS`, ...), empty if the exposure couldn't
  be measured. `source_centroids` gives the `(N, 2)` positions that **p**
  uses. A new `StepResult.source_index` is the row of the first star (None if
  not measured).
- `StepResult.stamp` and `FocusSequence.source_stamps` are removed; the GUI
  never makes a cutout.
- `FocusSequence.execute` (CLI) cuts each stamp from `result.frame` with
  `extract_stamp(frame, centroid, int(fwhm*10))` just before
  `FocusPlot.add_stamp`, and only when plotting.
- Everything the Auto search and the CLI use (`img_quality`, `centroids`,
  `fit_best_focus`) stays as it is, for the first star only.

## 4. Selected stars (`nickel_focus/selection.py`, new; no GUI)

`StarSelection` holds the plotted data for a list of exposures
(`StepResult`s, in acquisition order). Each selected star is a list with one
entry per exposure: the row of its source in that exposure's table, or None
for no match.

- `StarSelection.first_star(results)`: one star from each result's
  `source_index`. This is the initial selection, and what a sequence builds
  while it runs.
- `add(exposure, x, y)`: find the source nearest `(x, y)` in that exposure
  (within `pointing.SNAP_RADIUS`, as for **p**; otherwise warn, nothing
  changes). Match it in every other exposure: the nearest source within
  `MATCH_RADIUS` of its position. If any selected star already uses that
  source in that exposure, replace that star; otherwise append. Returns
  whether it added or replaced, and in how many exposures it matched.
- `remove(exposure, x, y)`: the star using the source nearest `(x, y)` in
  that exposure (same snap). If none, warn "not in the plotted data".
- `points()`: the pooled `(focus, fwhm, centroid, is_outlier, exposure)` for
  every selected star and matched exposure (§5.4 for `is_outlier`).
- `stars_in(exposure)`: each selected source in one exposure (centroid,
  FWHM, outlier flag) for the image panel's boxes.
- `mean_fwhm(exposure)`: for the Single tab (None if none).
- `fit()`: `quadratic` over `points()`, giving the vertex, or None with a
  reason (no points; fewer than 3).

`MATCH_RADIUS = 10.` (binned pixels), noted as probably generous.

## 5. GUI

### 5.1 Image panel

- **a** and **d**, gated like **m** and **p** (only while nothing runs),
  emit `starAdded(x, y)` and `starRemoved(x, y)`.
- The displayed exposure gets a box for every selected star in it, not just
  one: 10 × that star's FWHM, red or yellow (with "Outlier centroid") as
  now. The panel takes the boxes from the Controller (`set_selection` or
  similar), rather than reading them from the `StepResult`.

### 5.2 Focus curve panel

- Plots `StarSelection.points()`: blue dots, gold × for position outliers,
  as now, all stars alike.
- The fit and best-focus star come from the pooled fit. With no points, or
  fewer than 3, it draws what it has with no curve.

### 5.3 Controller

- Owns the `StarSelection` for whatever's loaded (sequence or Single
  exposure), built from the first star as results arrive.
  `start_focus_sequence`, `take_single_exposure`, and a reanalysis (**m**)
  reset it.
- `_on_star_added`/`_on_star_removed`: update the selection, redraw the
  image and curve panels, and log, e.g. "Added the star at (412.3, 230.8):
  matched in 5 of 5 exposures", "Re-matched the star at ...", "Removed the
  star at ...". Then refit: log the best focus and set the Single tab's
  default; with nothing to fit, warn "No data to fit; press 'a' on a star
  to add one." (fewer than 3 points: "Too few points to fit").
- Single exposure: the Single tab's FWHM label shows
  `StarSelection.mean_fwhm` after every change.
- `Controller.method` (the **m** mark) is untouched by **a**/**d**.
- At the end of a sequence, the logged best focus is still
  `fit_best_focus` on the first star (the selection is just that star at
  that point, so the two agree).

### 5.4 Position outliers

The existing check (`FocusPlot.is_outlier`: the distance from the median
position, beyond mean + 2σ) is applied to each star's own positions. While
a sequence runs, the first star's flags are computed as now, each against
the exposures so far. A star built or re-matched by **a** is flagged against
all its matched exposures at once.

## 6. CLI

Only §3: cutouts are cut when plotted. No **a**/**d**.

## 7. Tests

All asserts with messages.

- **Photometry:** `measure_sources`/`select_source`/`source_quality` match
  the old `image_quality` results; `image_quality` has no stamp.
- **`StarSelection`:** first star; add (match within the radius, an exposure
  with no match, a star already selected is replaced, not duplicated, e.g.
  the brightest); remove (any star, including the first; a star not in the
  data warns); all removed then added; pooled points and fit; mean FWHM;
  the position-outlier flags.
- **CLI:** `execute` still plots stamps (cut at plot time); no stamps
  anywhere else.
- **GUI:** **a**/**d** emit only while enabled; boxes for every selected
  star; the curve shows the pooled points and fit. Through the Controller,
  on a Replay sequence and a Single exposure: add, re-match, remove,
  remove-all-then-add, the logs and Single-tab default, the Single tab's
  mean FWHM, and a new sequence resets the selection while the **m** mark
  still seeds it.

## 8. Docs

`GUI_REFERENCE.md` (modules, version history), `GUI_IMPLEMENTATION.md`, the
user manual and Help tab (**a**/**d**, and blinking a star with them).

## 9. Order of work

1. §3 (photometry split, cutouts at plot time) with its tests; full suite
   green.
2. §4 `StarSelection` with its tests.
3. §5 GUI, then docs.

## 10. Possible later enhancements

- **Fit-outlier rejection:** e.g. iterative clipping of points far from the
  pooled quadratic, with excluded points shown differently (the
  distinction first intended for red vs. orange). Likely not needed.
- **Telling stars apart on the curve** (per-star colors), if blinking with
  **a**/**d** isn't enough.

## 11. As built (2026-11-18)

Implemented as planned, with these differences:

- **Two selections.** The Controller keeps one `StarSelection` for the
  loaded sequence (plotted and refit) and one for the latest Single
  exposure (its mean FWHM on the Single tab). The plan's single selection
  let a Single exposure taken with a sequence loaded replace the sequence's
  curve. 'a'/'d' act on whichever covers the displayed exposure; a Single
  exposure is never refit.
- **Boxes.** An exposure that no selection covers (e.g. an earlier Single
  exposure) boxes its own measured source, as before.
- **The first star's points** are each exposure's own measurement
  (`StepResult.fwhm`/`centroid`), so they're right for any method,
  including `weighted`.
- **`StepResult.stamp()`** is a method that cuts the stamp on demand (only
  the CLI's plot calls it), not a field.
- **`measure_quality(src_data, method)`** was added beside
  `select_source`/`source_quality`, so `measure_exposure` and
  `image_quality` share the method handling (including `weighted`).
- **`extract_stamp`** now clips at the low edges.
- **Fixture.** `three_star_sweep` (and `three_star_frame`) live in
  `tests/conftest.py`, shared by the selection and GUI tests.
- **Fixed curve limits** (added afterward): blinking a star moved the
  autoscaled limits, so they're fixed when a sequence's run or
  remeasurement finishes and recalculated by 'm' or a new sequence
  (`StarSelection.plot_limits`: the fitted curve's full range ±30% of that
  range, not below 0; the focus values with a 5% margin). A first version,
  from 95% of every source's FWHM, needed too much tuning.

