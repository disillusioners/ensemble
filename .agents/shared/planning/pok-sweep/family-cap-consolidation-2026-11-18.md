# POK Family-Cap Consolidation Wave — 2026-11-18

Point-in-time record of the family-cap consolidation wave executed in the live POK store on 2026-11-18 — the follow-up to the 2026-11-18 POK sweep (which closed at 273 active rows). Sibling artifacts in this directory (`census-active-2026-11-18.json` / `.md`, `phase4-recensus-2026-11-18.md`, `cluster-proposals-2026-11-18.md`) are point-in-time records and were NOT modified by this wave.

## Provenance & Method
- **Curator:** project-manager (curator authority) — history entry `b8b4647a`
- **Timestamp:** 2026-11-18 ~ 18:14–18:36 UTC
- **Basis:** `census-active-2026-11-18.json` + live `pok_query` re-verification + `phase4-recensus-2026-11-18.md`
- **Status of the facts below:** already executed in the live POK store at adjudication time — do NOT re-execute.

## 1. snapshot-l3 family — merged 4→3 ACTIVE
- Survivor `snapshot-l3-01-malformed-json-empty-collapse` superseded `7f83772d`→`08580dbd` (union body of former rows 01+05, 229/240 chars).
- Absorbed member `snapshot-l3-05-decline-byte-identical` (`eae94324`) ARCHIVED.
- The LEG-B stage2_score fact remains covered by ACTIVE `verify-02-snapshot-l3-red-set` (`d57f710c`).

## 2. prompt-assent family — adjudicated CAP-BLOCKED (kept at cap)
- Numbered members 01/02/03/04 are distinct facts.
- Tightest faithful union (01+04) = 311 chars vs the 240 body cap.
- Prefix count = 5 (incl. unnumbered `prompt-assent-flip-on-gate` `6db7e0fd`).

## 3. git family — adjudicated CAP-BLOCKED (kept at cap, prefix count 4)
- `git-01-canonical-detached-diff-trap` `cb7dea97`
- `git-01-commit-msg-before-dashdash` `fb0cc19f`
- `git-sha-transcription-discipline` `48b2c876`
- `git-update-ref-worktree-index-stale` `b02c4f39`

Tightest faithful union = 250 chars. A union under a NEW `git-NN` slug is mechanically impossible: the new-slug admission path is family-gated at 4 — only same-slug supersede is exempt. git is the ONLY one of the three families with a REAL live `pok_family_full` refusal (anomaly `463b2b7c`).

> **[LANDED 2026-11-18 — fix/pok-family-key]** The branch makes the INVERSE true: multi-token families ARE live-gated now — `prompt-assent` (prefix count 6) and `snapshot-l3` (prefix count 3) DO refuse with `pok_family_full`; phase-4's "would refuse" inference was REAL, not a census-regex artifact. §3's "git is the ONLY one of the three families with a REAL live `pok_family_full` refusal" and §4's "only git's refusal is real" are therefore SUPERSEDED. §5's PENDING-USER is RESOLVED (Ari's ruling: option (b) — fix the family key), NOT "widen the cap". §4's source anchors and pattern literal describe the PRE-landing state — the landed pattern is `^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$` (`daemon/repositories/pok/models.py:132`).

## 4. Gate mechanics — verified from source (read-only)
- `daemon/repositories/pok/models.py:131` — `POK_FAMILY_SLUG_PATTERN = ^([a-z][a-z0-9]*)-(\d{2})-(.+)$` (single-token family only — multi-token families like `prompt-assent-NN` / `snapshot-l3-NN` are NOT live-gated).
- `daemon/repositories/pok/repository.py:114-122` — `count_active_in_family` uses `slug.startswith(family-)` so unnumbered rows count.
- `daemon/services/pok_service.py:553-558` — supersede path is count-preserving and `pok_family_full`-exempt.
- **Consequence:** phase-4 recensus §5's "would refuse" inference for prompt-assent/snapshot-l3 was an artifact of its looser census regex — only git's refusal is real.

## 5. Store counts after wave
| Counter | Before | After |
|---|---|---|
| active | 273 | 272 |
| superseded | 74 | 75 |
| archived | 33 | 34 |
| total | 380 | 381 |

## Family-cap headroom — both derivations
| Family | Numbered-regex census view | Daemon prefix view |
|---|---|---|
| prompt-assent | 4 | 5 |
| snapshot-l3 | 3 | 3 |
| git | 2 | 4 |

## Decision — PENDING-USER
Framed, NOT made (owner: user/Ari):
- widen `POK_FAMILY_MAX` 3→4, **vs**
- fix the family key to the documented multi-token intent.

No gate change has been made; both cap-blocked families remain at cap pending this decision.
