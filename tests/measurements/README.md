# measurements/ — Deliberate Before/After Evidence Artifacts

This directory holds **deliberate before/after evidence artifacts** for the
upgrade-speed-overhaul mission. Every committed `.txt` file here is a
**captured measurement** (not a runtime scratch output) and is named by the
**code SHA it measured** so the artifact is self-locating.

## Filename convention

```
baseline-pre-change-<sha>-<UTC-stamp>.txt
journal_history_append-post-migration-<sha>-<UTC-stamp>.txt
splice-post-change-<sha>-<UTC-stamp>.txt
```

- `<sha>` is the short SHA of the code state the measurement was taken
  against. Pre-change files document the **before** state; post-change
  files document the **after** state.
- `<UTC-stamp>` is `YYYYMMDDTHHMMSSZ` (sortable, unambiguous).
- The prefix (`baseline-pre-change`, `journal_history_append-post-migration`,
  `splice-post-change`, ...) names the operation/ceremony the measurement
  documents.

## What belongs here

✅ Committed:

- **Pre-change baselines** captured against a known SHA before the
  implementation lands — the "before" half of a before/after pair.
- **Post-change measurements** captured against the new SHA — the "after"
  half of the same pair.
- A measurement pair always covers the same operation/ceremony on the
  same code path; the SHA in the filename is the variable.

❌ **NOT** committed here — do not commit, do not generate in-tree:

- **Runtime scratch** from interactive debugging, mid-fix iteration
  probes, one-off `bash` investigations, or `git status`-moment captures.
  These go to `$TMPDIR/upgrade-overhaul-measurements/` (see
  `tests/measurements/.gitignore` notes below) so they never enter the
  tree.
- **Re-runs of the same measurement** under the same SHA — pick one
  canonical capture per SHA, per ceremony.

## Post-F2 hygiene

After F2 lands (commit `74ff8d152` and forward), nothing regenerates
in-tree. All new runtime measurements go to the `$TMPDIR` scratch path
above. The only writes that touch this directory are **deliberate
before/after evidence captures** for a specific SHA, named per the
convention above.

## Why this split exists

- The commit log must read cleanly: a glance at `git log --stat` on this
  directory should show only the **evidence** captures the team agreed
  to retain, never the noise of a debug session.
- The artifacts here are the **proof** for plan decisions (D-P3.x) and
  the test-strategy T-G* cells: a reviewer should be able to walk from
  a planning-doc claim straight to a pair of `.txt` files that prove the
  before and the after.
- The runtime scratch dir is the **scratch space** the worker uses to
  iterate; it is allowed to be noisy, ephemeral, and unmourned.

## Adding a new capture

1. Decide which SHA you are measuring against.
2. Run the measurement.
3. Save the output to a file named per the convention above.
4. `git add` only the file you just wrote — never the parent directory
   blanket.

## Removing / rotating

Old captures may be rotated out when a new measurement is taken for the
same ceremony on a newer SHA. Do not delete captures until the new pair
is in place and reviewed.
