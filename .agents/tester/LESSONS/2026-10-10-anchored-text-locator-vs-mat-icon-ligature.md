# Anchored text locators vs mat-icon ligature textContent — `/^text$/` never matches a label containing an icon

Date: 2026-10-10 · Run: RESULTS/2026-10-10-snapshots-v2-daemon-lane-e2e-final-gate.md (leg #3, (d) first-chip click)

## Pattern
Material widgets commonly render a checkbox/menu-item LABEL whose textContent is **icon ligature + option text + whitespace** (e.g. `LABEL.status-menu-item` = checkbox + `SPAN` with `MAT-ICON` ligature `check_circle` + `" active "` → textContent `check_circle active `). A Playwright `hasText` with an **anchored** regex (`/^active$/`) can never match that string — the locator logs "waiting for…" forever, the click never lands, and the *downstream* network wait times out 20s later. The failure surfaces far from the cause: ours manifested as a `waitForResponse` TimeoutError at spec :264 while the real defect was the click locator at :414.

## Diagnosis signature
- Trace action log: the click action never completes (still "waiting" at timeout) — an unresolved LOCATOR, not a slow response.
- Daemon/API census shows ZERO requests of the shape the wait expects — the interaction never happened.
- DOM snapshot: read the element's actual textContent (icon ligature visible) and compare against the regex anchors.

## Fixes
1. Unanchored regex `/active/` — acceptable when no sibling option contains the substring (check the option list!).
2. Better: scope to the text-bearing child (`SPAN.status-chip-inline` / the option-label span) or target the checkbox role/input directly.
3. General rule for Angular Material: never anchor `^…$` text matchers on container labels that can host `mat-icon` ligatures or trimming-unsafe whitespace.

## Meta-lesson (the whack-a-mole of a never-fully-executed leg)
A long test leg that fails early for its whole life hides every defect downstream of the failure site. Each fix extends execution reach and exposes the NEXT latent defect (our leg #3: whole-leg v1 rot → (c) arming race → (d) locator). When gating on such a leg, prefer a cheap focused pre-gate (`--grep "<leg>" --trace on`, CLI knobs only) after each fix before spending a full-pack commission — it walks the leg to its end in ~100s and names the next blocker with trace evidence.
