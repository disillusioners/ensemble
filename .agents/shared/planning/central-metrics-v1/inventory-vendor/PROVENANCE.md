# VENDORED EVIDENCE — provenance (AM-24)

Date: 2026-11-02 (UTC) · Lane: central-metrics-v1 · Amendment: **AM-24** (architect v2 §4 / §8 M-10)

## Why these files are vendored

The metrics-unification inventory is the pull-seam map that seeded this lane's research wave. It lives
**UNTRACKED** in the shared main checkout (`/home/nea/ensemble-src/.agents/shared/planning/metrics-unification/`),
so any committed document that cites it by path **dangles in every other checkout**. AM-24 requires the
evidence to be vendored (or SHA-cited) inside the lane directory. Vendoring was chosen over SHA-citing
because the plan cites specific sections of these files and a hash alone is not readable evidence.

## Provenance

| Field | Value |
|---|---|
| Source directory | `/home/nea/ensemble-src/.agents/shared/planning/metrics-unification/` (shared main checkout — **READ-ONLY**, never modified by this lane) |
| Source status | **UNTRACKED** in the main checkout (present on disk, absent from git history) |
| Evidence ref | `origin/latest@62627265f` (v0.28.4-era) — the inventory's own provenance line |
| Vendored on | 2026-11-02 (UTC), by the central-metrics-v1 plan set (planner) |
| Transformation | **prepend-only** — an 8-line HTML-comment provenance header; the content below is byte-identical to the source |
| Citation rule | committed docs cite **this directory**; the source path is cited only for provenance |

## Byte-identity (sha256)

| File | Source sha256 | Vendored-body sha256 | Verdict |
|---|---|---|---|
| `README.md` | `1bd508a730fd2fdff7f5156297cd778c13f42e5a1fbe535c41df59df30137b2c` | `1bd508a730fd2fdff7f5156297cd778c13f42e5a1fbe535c41df59df30137b2c` | IDENTICAL |
| `metrics-inventory-backend-producers.md` | `4101ac1c9d8269a6d6fe812927087fb09b3c5191eb6798f8cdcbe3c03b6ee85b` | `4101ac1c9d8269a6d6fe812927087fb09b3c5191eb6798f8cdcbe3c03b6ee85b` | IDENTICAL |
| `metrics-inventory-surfaces-fe-deps.md` | `f4cc5c4ffdb10028c21fe8ffa38b12855efc9736938b10e0137362b4c60373d8` | `f4cc5c4ffdb10028c21fe8ffa38b12855efc9736938b10e0137362b4c60373d8` | IDENTICAL |

## Verification recipe

```bash
cd <lane>/.agents/shared/planning/central-metrics-v1/inventory-vendor
for f in README.md metrics-inventory-backend-producers.md metrics-inventory-surfaces-fe-deps.md; do
  awk 'NR>8' "$f" | sha256sum
done
# each digest must equal the source digest in the table above
```

## Verification round 2 — the implementation lane's independent re-verification (2026-11-02T10:39Z)

Phase 6 task 6.1 requires the implementation lane to **verify and resolve** the vendored copies.
It did — **the vendored copies ARE the current sources; NO re-copy was needed and none was made.**

**The apparent discrepancy is the provenance header, not a content drift.** The vendored files are
**larger** than their sources (33 218 / 51 065 / 2 125 bytes vs 32 446 / 50 294 / 1 382). The
difference is **exactly the 8-line HTML-comment header** (7 comment lines + 1 blank separator),
which the plan row 6.1 mandates: "each with a provenance header … and byte-identical content below
the header". Stripping the header leaves a body that is **byte-identical** to the source.

| File | Vendored bytes | Source bytes | Delta | Delta explained by | Body verdict |
|---|---|---|---|---|---|
| `README.md` | 2 125 | 1 382 | **+743** | the 8-line header | **BYTE-IDENTICAL** |
| `metrics-inventory-backend-producers.md` | 33 218 | 32 446 | **+772** | the 8-line header | **BYTE-IDENTICAL** |
| `metrics-inventory-surfaces-fe-deps.md` | 51 065 | 50 294 | **+771** | the 8-line header | **BYTE-IDENTICAL** |

**Full-file sha256 of the vendored copies** (the file AS COMMITTED — header included; this is the
digest a reader can reproduce with a plain `sha256sum`):

| File | Vendored full-file sha256 |
|---|---|
| `README.md` | `0898ffe5a25101777145e639a769cb7d65503fef4240b756db9b89abe482d547` |
| `metrics-inventory-backend-producers.md` | `c250df508ab03cb00fae3d86aad96bf6c36981e1176e5d7036aec86cce5a4619` |
| `metrics-inventory-surfaces-fe-deps.md` | `8f8d3c9a8bd42577a00daf691c565f9a8bb47333531c883fe61d5b421f03aa08` |

**The source-side integrity was re-confirmed** (`git status --porcelain` + mtimes, 2026-11-02T10:39Z):

- All three source files are still **`??` (untracked)** in the shared main checkout — `git log --all
  -- .agents/shared/planning/metrics-unification/` returns **0 commits**, so they remain absent from
  git history (the dangling-citation problem AM-24 exists to solve is still live).
- The source mtimes are **unchanged** (README 2026-11-01 19:59:16Z · backend-producers 2026-11-01
  19:59:08Z · surfaces 2026-11-01 19:46:50Z) — i.e. **older than** the vendoring (2026-11-02
  00:39Z), so nothing in the source has moved since the copy was taken.
- **Nothing in the shared main checkout was created, modified or deleted** by this lane.

**Re-verification recipe used by the implementation lane** (the one recorded above, executed
verbatim against the live sources — it re-derives the body digest by stripping the header):

```bash
cd <lane>/.agents/shared/planning/central-metrics-v1/inventory-vendor
SRC=/home/nea/ensemble-src/.agents/shared/planning/metrics-unification
for f in README.md metrics-inventory-backend-producers.md metrics-inventory-surfaces-fe-deps.md; do
  tail -n +9 "$f" > /tmp/body_$f          # strip the 8-line header
  cmp /tmp/body_$f "$SRC/$f" && echo "$f: BODY BYTE-IDENTICAL"   # cmp is byte-exact
  sha256sum /tmp/body_$f "$SRC/$f"        # the two digests MUST be equal
done
```

**Verdict: PROVENANCED — no re-copy, no rewrite.** The vendored evidence is the current source
content, with a provenance header on top; the sha256 table above is authoritative.

## Source-side integrity

The shared main checkout was **read only** during vendoring: `git status --porcelain` on the source
directory still reports `?? .agents/shared/planning/metrics-unification/` (untracked, unchanged) and the
source files' mtimes are unchanged (2026-11-01 19:46–19:59). No file in the shared checkout was created,
modified or deleted by this lane.
