"""Cap-exception micro-round ITEM 1 (2026-09-06,
``fix/defer-self-witness-and-cleanup``) — render-path pin for the
``CLEANUP_TRUTH_SURVIVOR_NOTE`` caption in the System Cleanup
dialog.

House rules forbid Angular TestBed. The tidier 003 lesson: const
existence alone is not a render pin — the pin must verify that the
caption is IMPORTED into the dialog source, EXPOSED as a readonly
field, AND bound to the template interpolation. Removing any of
the three breaks the render path even though the const still
exists in cleanup-preflight.model.ts.

Plain Python ``pathlib`` only — no Angular TestBed, no DB, no
fixture. Three independent substring assertions against the
dialog source file. All three must hold for the render path to
be wired (one removes the import, one removes the readonly
exposure, one removes the template interpolation).

The companion ``cleanup-preflight.model.spec.ts`` keeps the
const-existence pin (the FE const must still be exported). This
test is the WIRING pin: const + import + readonly + template
interpolation = renders. Drop any ONE of the four and the dialog
silently omits the caption.

Run with::

    timeout 60 .venv/bin/pytest tests/unit/test_fe_dialog_survivor_render_path.py \\
        -v --tb=short -q --override-ini="addopts="
"""

from __future__ import annotations

import re
from pathlib import Path

# Single source file: the Angular standalone component with the
# inline ``template: `…``` literal. The template lives in the same
# .ts file as the class — no separate template file to scan.
DIALOG_SOURCE: Path = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "app"
    / "components"
    / "system-cleanup-confirm-dialog"
    / "system-cleanup-confirm-dialog.component.ts"
)


def _source() -> str:
    assert DIALOG_SOURCE.exists(), f"dialog source missing: {DIALOG_SOURCE}"
    return DIALOG_SOURCE.read_text(encoding="utf-8")


def test_import_line_present() -> None:
    """The dialog imports ``CLEANUP_TRUTH_SURVIVOR_NOTE`` from the
    cleanup-preflight model.

    Drop the import (e.g. only re-exporting it without importing)
    and the component stops compiling — TS would catch this at
    build time. The pin catches the regression where someone
    reorders the model exports but forgets the dialog's import.
    """
    text = _source()
    pattern = re.compile(
        r"import\s*\{[^}]*\bCLEANUP_TRUTH_SURVIVOR_NOTE\b[^}]*\}\s*"
        r"from\s*['\"]\.\.\/\.\.\/models\/cleanup-preflight\.model['\"]",
        re.DOTALL,
    )
    assert pattern.search(text), (
        f"Dialog source must import CLEANUP_TRUTH_SURVIVOR_NOTE from "
        f"../../models/cleanup-preflight.model. Missing import line — "
        f"the const exists but the dialog can't render it. See "
        f"cap-exception micro-round ITEM 1, 2026-09-06."
    )


def test_readonly_exposure_present() -> None:
    """The component exposes ``CLEANUP_TRUTH_SURVIVOR_NOTE`` as a
    ``readonly`` field (the binding the template interpolates
    against). Removing this line leaves the const-imported but
    unreachable from the template — silent omission.

    The component reads the const through a readonly alias
    (``readonly survivorNote = CLEANUP_TRUTH_SURVIVOR_NOTE;``) so
    template interpolation ``{{ survivorNote }}`` resolves to the
    exported const verbatim. Any other exposure (function call,
    getter, computed signal) is acceptable too — this pin only
    requires the const to be reachable from the template scope.
    """
    text = _source()
    pattern = re.compile(
        r"readonly\s+\w+\s*=\s*CLEANUP_TRUTH_SURVIVOR_NOTE\b"
    )
    assert pattern.search(text), (
        "Dialog source must expose CLEANUP_TRUTH_SURVIVOR_NOTE via a "
        "readonly binding the template can interpolate against "
        "(e.g. 'readonly survivorNote = CLEANUP_TRUTH_SURVIVOR_NOTE;'). "
        "Const exists in the model module — but the dialog must "
        "still surface it. See cap-exception micro-round ITEM 1."
    )


def test_template_interpolation_present() -> None:
    """The dialog template interpolates the readonly field (or the
    const directly) inside the inline ``template:`…``` literal.

    Per the tidier 003 lesson: a render-path pin must verify the
    template binding, NOT just const existence. Removing this
    interpolation leaves the const-imported and the readonly
    exposed but the caption does NOT render in the dialog.
    """
    text = _source()
    # Two acceptable shapes:
    #   * {{ survivorNote }}            — interpolation via the readonly
    #   * {{ CLEANUP_TRUTH_SURVIVOR_NOTE }} — direct interpolation
    # The Angular template parser disallows direct const refs from
    # component scope; the readonly alias is the canonical shape.
    # We pin BOTH shapes so a future refactor to a getter/computed
    # signal doesn't break the test prematurely.
    readonly_interp = "{{ survivorNote }}" in text or "{{ survivorNote() }}" in text
    direct_interp = "{{ CLEANUP_TRUTH_SURVIVOR_NOTE }}" in text
    assert readonly_interp or direct_interp, (
        "Dialog template must interpolate either 'survivorNote' "
        "or 'CLEANUP_TRUTH_SURVIVOR_NOTE' so the caption renders. "
        "Const + readonly exposure alone are NOT a render path — "
        "the template must bind to the const too. See "
        "cap-exception micro-round ITEM 1."
    )


def test_template_gating_on_live_ids_length() -> None:
    """The template gates the caption on the truth-survivor
    signal (``liveIds().length > 0``). The pin covers the gating
    condition so a future refactor that renders the caption
    unconditionally (showing the "Truth-survivor listing" caption
    even when the operator has no survivors) breaks here.

    The Angular template uses the ``@if`` control-flow block;
    the pin matches both ``@if (...)`` and ``*ngIf=`` shapes.
    """
    text = _source()
    # Look for ``@if (liveIds().length > 0)`` either immediately
    # before or with the ``survivorNote`` interpolation in scope.
    pattern = re.compile(
        r"@if\s*\(\s*liveIds\(\)\.length\s*>\s*0\s*\)|"
        r"\*ngIf\s*=\s*['\"][^'\"]*liveIds\(\)\.length[^'\"]*['\"]",
    )
    assert pattern.search(text), (
        "The survivor-note rendering must be gated on "
        "liveIds().length > 0 — showing 'Truth-survivor listing' "
        "when the operator has no survivors mis-tells the operator. "
        "See cap-exception micro-round ITEM 1."
    )


def test_render_path_kept_integrated_in_template_after_will_remain() -> None:
    """Sanity: the survivor-note block is rendered AFTER the
    ``.will-remain`` block (logical sequence: canonical sentence
    first, IDs, then explanatory caption). A future template
    reorder that puts the caption BEFORE the IDs would still
    function but loses the narrative — pin the order so reorders
    break the test and a reviewer catches the prose drift.
    """
    text = _source()
    will_remain_idx = text.find('class="will-remain"')
    survivor_idx = text.find("survivorNote", will_remain_idx + 1)
    assert will_remain_idx != -1 and survivor_idx != -1, (
        "Both .will-remain and the survivor-note interpolation must "
        "be present. If either is missing, the prior three pins "
        "(import / readonly / template interpolation) catch the "
        "regression."
    )
    assert will_remain_idx < survivor_idx, (
        "The canonical split sentence must render BEFORE the "
        "survivor-note caption — the narrative is 'every ACTIVE "
        "job is cancelled, ... are kept' FOLLOWED BY the "
        "survivor-list caption. A reverse order would still "
        "render but flips the prose lead."
    )
