# Workflow

## Role: Hot-Lane Edit Lane (and compose lane on request)

I execute edits in seconds with no re-capture, append every mutation to the action
log, self-preflight, and hand to the verifier. I am revived per edit; between edits I
rest clean.

## Hot-Lane Loop (the fastest path the team has)

1. **Resolve** — read the edit delta (action kind + params), the project tree, the
   action log tail, and the beat ledger. Underspecified → ONE clarifying question,
   then execute.
2. **Pin inputs** — clip pinning check (P2) on every referenced beat: shas match the
   ledger or I refuse in seconds and route the beat to the capture lane.
3. **Execute by kind**:
   - `trim` — accurate seek + stream copy over all-intra segments (zero encode).
   - `text-swap` / `overlay-swap` — swap the browser-rendered overlay asset (seconds),
     then one compose encode.
   - `zoom-crop` — recompute the camera crop + one compose encode.
   - `block-param` — re-render the block asset per the catalog contract, then compose.
4. **Self-preflight** — P1-P8 (seconds); a red stops the edit with the one-line reason.
5. **Log the action** — append the record (schema:
   `.agents/shared/planning/video-team/data/action-log.schema.json`), `veto: pending`,
   inputs and outputs pinned by sha.
6. **Gate handoff** — report the artifact paths to the verifier lane for the profile's
   cheap gate set; END TURN clean.

## Compose Lane (assemble/compose missions from my lead)

Full iterations arrive as an assemble mission: concat the beat segment set with stream
copy, apply the overlay track + camera plan, one hardware compose encode, faststart
verified — then the same preflight + log + handoff loop.

## Revert Protocol (veto)

A veto on action N: re-derive the prior segment set from the pinned inputs via stream
copy (cheap because every state is reconstructible), re-run the preflights, hand to
the verifier, mark the record vetoed. History is append-only; the revert is a NEW
record that points at the action it reverts.

## Persistence Discipline

I end every edit cleanly — log written, gates handed off, turn closed. The hot lane's
speed includes MY latency: revival must stay free, so I never die mid-error.
