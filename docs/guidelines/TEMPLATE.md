# Guideline TEMPLATE

> Distilled from ENSEMBLE-30 ("prompt-assent enablement", hand-crafted by
> Emperor + Ari, 2026-11-12) — the first guideline. Copy this file, rename
> to `guideline-<feature-slug>.md`, fill every section IN ORDER. A section
> with nothing to say says "none" — it is never deleted.

---

# guideline-<feature-slug>

**Status:** draft | reviewed | canonical
**Feature:** <one line — what shipped, in which release(s)>
**Audience:** operator / agent / both

## 1. Purpose & doctrine

What the feature IS and the ruling it embodies — one short paragraph plus
the doctrine sentence carried verbatim where one exists (a user ruling, a
decree, an ADR call). If the feature has a named design owner or board
item, it is named HERE, once.

## 2. States

The feature's own state model — e.g. disarmed / tentative / operitative,
or OFF / armed / ratified, or default-ON / disabled. For each state:
what turns it on, what it looks like from outside, and what is INERT in
that state (what refuses, what never runs, what stays untouched).

## 3. EXACT enablement steps

The exact env keys, commands, and restart requirements — copy-verbatim,
no paraphrase:

- every env key with its prefix, default, and the truth-table for absent /
  empty / garbage values (fail-open vs fail-closed, clamp vs raise);
- every command with its full form, and the ORDER that must not be
  reordered;
- restart requirements (which knob is read at call time vs boot time);
- what NOT to do (e.g. a hand-added config.yaml block that is silently
  ignored; an idiom that silently no-ops).

## 4. The rules that bite

Stone-rule-class gotchas: what silently breaks, what has NO signal. This
section is the reason the guideline exists — every entry should name
(a) the wrong move, (b) the silent consequence, (c) the signal, if any.
If an entry has no signal at all, say "no signal — mitigation is
loudness/prevention discipline", never leave it implied.

## 5. Verification & probe

How to PROVE the feature works in its current state: the exact probe
command(s), the expected outcome(s), and what an unrun check MEANS
(a skipped probe is an open risk, not a pass). If arming is gated on a
probe (e.g. a six-leg live procedure), the gate is stated here with its
state list.

## 6. Evidence discipline

What evidence the feature's operation leaves behind and where: audit
tables, journal files, refusal envelopes, log lines with their exact
tokens, and what is deliberately NOT recorded (content-free provenance,
hashes-not-values). Include the "nothing happened" troubleshooting path
when the feature ships inert.

## 7. Rollback

What rolling back costs: migration delta (zero vs additive), whether the
old binary coexists with the schema, what data survives, what is lost
(fix-loss-only is the norm — say it plainly). If rollback needs an
operator act beyond re-deploying the old release, the act is stated here.

## 8. First real use

The first genuine production exercise of the feature: when it happened
(or "has not happened yet — the lane must not be relied upon until it
does"), what it proved, and what surprised.

## 9. Status

draft / reviewed / canonical + the date + who ratified. Plus the honest
gap line: which sections carry UNVERIFIED marks and why.

## 10. Source-of-truth pointers

The durable pointers, one line each: runbook path(s), planning dir path(s),
critical note ref(s), CHANGELOG section, board item id (e.g. ENSEMBLE-30),
primary source files (`daemon/...`). The board links HERE; this file never
duplicates board content.
