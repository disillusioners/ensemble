# LESSONS — v0.26.3 doc-fix release verification (2026-10-21)

## 1. `co_consts` is the only honest way to prove a doc string in a shipped PyInstaller binary
`strings` cannot see compressed module constants (it found 0 hits for text that is definitely in the source). The working chain: build-venv `CArchiveReader` → `extract('PYZ.pyz')` → `ZlibArchiveReader` → `extract('daemon.tools.self_alarm')` → real code object → recursive `co_consts` walk. Two disciplines that made it a proof, not a probe:
- **Derive needles from git with `ast`** so concatenation and embedded `\n` match the compiled constant byte-for-byte. A flat needle like `no memory of this request` matches NEITHER binary (the compiled `_full_doc_` holds a literal newline) — "absent in both" reads as proof and is uninformative.
- **Controls**: a version anchor (the `daemon` pool yields 0.26.3 vs 0.26.2 → the PYZ belongs to the right binary) and a **negative-control module** (`daemon.sources.adapters.scheduler` pools identical → the observed 2+2 delta is a property of the change, not of the extractor).
- **Scope honesty**: a source COMMENT half of a fix is unreachable by this method (comments never compile). Say so instead of implying coverage.

## 2. Doc-surface fixes need doc-surface pins — and the pin must be mutation-verified
The D-V2 fix added `TestDocSurfaceWakeTheCaller` (6 nodes). Spot-verifying ONE cycle (plant the old string at one location → expect RED with named nodes → `git checkout --` restore → prove sha + `git diff --quiet` → GREEN) proved the pin bites in ~3 s. The RED leg matched the fix commit's own predicted failure set — that is the strongest cheap evidence a new pin is meaningful.

## 3. `systemd-run --user` units inherit the USER-MANAGER env, not your shell's
A boot smoke that exports env inside its own wrapper command but does NOT source `.env` **inside the unit** boots with a different environment than intended (observed: `MCP_DISABLE_BUILT_IN_WEBFETCH` and `SYSTEM_ENCRYPTION_KEY` absent → the webfetch built-in was created and tried to exec `uvx`, which is not on the daemon PATH → 4 extra ERROR lines). Source `.env` inside the unit's command, then re-unset the two vars the dev `.env` re-sets.

## 4. Export BOTH `PORT` and `DAEMON_PORT`
The staged `config.yaml` carries `port: ${PORT:-8088}`. A PORT-only override silently falls back to 8088 — a forbidden port in this project's conventions. Set both and prove the bind with `ss -ltnp` pre-bind + post-teardown.

## 5. Staging worktrees get REUSED under stale names
The v0.26.3 sidecars were not in `wt-stage-v0263` (nonexistent) but in `wt-stage-v0262` (reused, re-pointed). **Attribute sidecars by content** (`artifact_sha256 == manifest.binary_sha256`, `git_head` == expected rev), never by path — and record the ACTUAL path in the commission record so the next verifier doesn't chase a phantom directory.

## 6. Promote-as-is is the correct path when tip drift is docs-only
`origin/latest` advanced past the staged build tree (my own docs merge + one more docs commit) while every shipped tree-object stayed identical. A re-stage would have been refused (exit 78 `non-tip-tree`). Precedent (v0.26.1) + the tree-object proof ⇒ promote the staged artifact AS-IS; never use an override to force a re-stage.

## 7. A terminated worker can still be the author of the answer
The GT-b worker I terminated for watchdog silence had actually completed both legs; its report arrived late, and a read-only harvest of its `/tmp` artifacts independently reproduced the same conclusion (net-new = 0). Two paths, one verdict — but only because the artifacts were preserved with incremental persistence. Harvest before you re-dispatch; a re-dispatch may have produced a WORSE answer.
