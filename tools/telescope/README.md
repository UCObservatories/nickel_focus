# Telescope checks

Short scripts for checking, at the telescope, behavior that `nickel_focus`
assumes but that can only be confirmed against the real services. Each
targets items in `claude/ktl_interface_refactor.md` §5.1/§5.2 and
`claude/pointing_offset_plan.md` §4/§10.

They use the installed `nickel_focus` package (its `NickelPoco`/`NSciCam`
services, so `ktl` only through its public API), and must run where `ktl` is
available. Run them from anywhere, e.g. `python tools/telescope/show_state.py`;
`--help` describes each.

Each run logs to the terminal and to `logs/<script>-<date>-<time>.log` here
(not tracked by git), timestamped to the millisecond.

## Read only (safe at any time)

| Script | Checks |
|---|---|
| `show_state.py` | Every keyword we use, as ascii and binary. The Dec sign (§5.1 #1): run it pointed between 0 and −1° Dec. Scratch naming: `OBSNUM`, `OBSNUMMAX`, `FILENAME`, `FITSFILE`. |
| `check_read_timing.py` | Connection (priming) time; read times against the 1 s timeout (run idle, during a readout, and during a slew); 1 ms-timeout reads, for the notify flags. |
| `watch_keywords.py` | Logs every broadcast of a set of keywords (`SERVICE.KEYWORD` arguments, or a default set) until Ctrl-C or `--seconds`, then the intervals. Heartbeat periods, the position broadcast rate, callbacks on the real `ktl`, `EXPSTATE`/`FITSFILE` during an exposure, `POCGDFIN`/`POCOT` during an offset. Run it in a second terminal alongside anything else. |
| `check_precession.py` | A catalog target's J2000 and apparent coordinates (§5.1 #4). With `--go`, it slews there as the GUI does and compares where the telescope ends up. |

## Actions (nothing happens without `--go`)

Without `--go`, each says what it would do and stops. Each restores what it
changes.

| Script | Does | Checks |
|---|---|---|
| `check_precession.py --go` | Slews to the target. | As above. |
| `check_exposure.py --go` | Takes a short exposure (1 s, Fast, 4×4 by default), timing each settings write and each step. `--settings` changes each setting in turn, pausing so you can look at the data-acquisition GUI. `--unrecorded N` takes N exposures with `RECORD = No`. | Exposure completion and `FITSFILE` timing; whether `Start` is seen; write-completion times; whether the data-acquisition GUI follows our settings; scratch naming. |
| `check_secondary.py --go` | Moves the focus by `--step` (2) and back. | Write completion for `POCSECPD` against the relock. |
| `check_guider_offset.py --go` | Applies a small offset (10″ E by default; `--east`, `--north`) with the GUI's sequence; `--series`/`--together`, `--sign`, `--cos-dec`, `--timeout` override the unverified constants for the run; `--mark` writes `POCGDOT`; `--undo` pauses for an image and offsets back. | The pointing-offset constants (pointing plan §4, §10). Take an image before and after to see the direction. |

## Not covered

- `EXPSTATE == Error` and its `EXPRESULT`: there's no safe way to cause a
  camera failure. If one happens, `watch_keywords.py` captures it.
- Stale indicators when a dispatcher stops broadcasting (§5.1 #5): stopping
  one is a staff action.
