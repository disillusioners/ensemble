# Lessons — v0.27.8 staged boot-smoke procedure (2026-10-26)

## 1. Executing the staged binary FROM the live releases dir leaks advisory journal rows
The sandbox child's `UpgradeJournalSweepService` resolves `install_dir` from the executed binary's path. Running `/home/nea/agents-ensemble/releases/vX/ensemble-prod` directly ⇒ install_dir = the live install ⇒ each boot appends an advisory `supervision_boot` row to live `releases/state.json` (benign, `outcome=conforming`, promote-inert — but it breaks the state.json byte-identity fence staged smokes want to hold).
**Adopted procedure (this gate's decision):** COPY the binary into the scratch dir and execute the copy — install_dir then resolves into scratch and the live state file stays byte-identical. Pre/post sha256 of `releases/state.json` remains a mandatory fence either way.

## 2. The staged daemon's port/env contract: `PORT` + `ENSEMBLE_CONFIG`, set BEFORE first launch
Config default is `daemon.port: ${PORT:-8088}` — the env var is **`PORT`**, not `DAEMON_PORT`. A first launch without it binds 0.0.0.0:8088 (the config default; on this host it happened to be free). The binary also requires an absolute `ENSEMBLE_CONFIG` pointing at config.yaml (unit-1 died on `FileNotFoundError: ./config.yaml` when cwd-relative).
**Adopted procedure:** set `PORT=<free scratch port>` (bind-checked, ≠9797/7979/8088) + `HOST=127.0.0.1` + `ENSEMBLE_CONFIG=<scratch>/config.yaml` (copied, never the live original) in the transient unit's env BEFORE the first launch attempt.

## 3. Raw `strings` cannot see PyInstaller PYZ string literals — hyphenated registry keys all read 0
Zlib-compressed PYZ hides every non-TOC literal: `notify-user`, `board-link`, `self-alarm`, `view-write`, `wedge-capture` all count 0 under `strings -n 8`. Only module PATHS (dots, not hyphens) are visible. Presence-proofs inside the binary need the documented route: `CArchiveReader` → `open_embedded_archive('PYZ.pyz')` → `extract(module)` → recursive `co_consts` — and the reader must be the BUILD interpreter (3.14 reader for a 3.14 build; a 3.13 reader fails with `bad marshal data` on some modules, a partial failure that MIMICS absence). The `.sql` DATA-TOC census, by contrast, is interpreter-independent.
**Rule:** never report "string absent from binary" from raw `strings` alone; disclose the method.

## 4. Manifest provenance fields vary by release — derive LIVE-TREE from the annotated tag + payload determinism
v0.27.4's manifest carries no git_head/sidecars in the release dir. The working derivation: annotated tag target → discriminate against the bump commit via the one payload tree that differs (FE dist determinism: 93/93 files identical against exactly one candidate) → corroborate with the successor release's identical tree-sha. Full derivation recorded in RESULTS/2026-10-26-v0278-staged-verification.md §S4.
