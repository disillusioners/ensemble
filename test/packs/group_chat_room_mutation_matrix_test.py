#!/usr/bin/env python3
"""The agent group chat ROOM MUTATION MATRIX (P6, 2026-11-16).

Every mutation below is planted INDEPENDENTLY on the production
source, the intended pack is run, and the result must be RED — with
the INTENDED assertion named in the output. The file is then restored
BYTE-IDENTICALLY and the pack is re-run and must be GREEN.

The pack closes the "Unverified-by-execution" label on the room
surfaces: the H1 extraction (the shared gate helper), the H2 extraction
(the joined-room module + the historical import surface) and the P3
delivery lane (the at_boot guard + the mint-failure admission gate)
each get a leg whose RED proves the pin has TEETH.

The H2 extraction carries THREE legs of its own (GC-M07/GC-M08/GC-M09 —
the size criterion, the joined-room ownership, the shared-primitives
ownership): those three split pins had no mutation leg at all, so a
split that silently broke would have gone unnoticed. Each of the three
plants a break that leaves the module IMPORTABLE (the name stays
defined at runtime — ``if True:`` — so the leg isolates its OWN pin
instead of tripping the import-surface pin as collateral).

Restore discipline (NO ``git checkout --``):

  * each mutation is restored from an IN-MEMORY BYTE COPY of the file
    taken immediately before the mutation is planted;
  * BEFORE the first mutation the harness RECORDS a pre-run manifest —
    the HEAD sha + the sha256 of EVERY file the matrix will touch — to
    ``LOG_DIR/pre_run_manifest.json``, and journals the pre-mutation
    BYTES to ``LOG_DIR/recovery/<label>.blob`` while a mutation is in
    flight, so a run killed mid-mutation leaves the tree DIRTY but
    DETECTABLE and ATTRIBUTABLE:
      - ``--verify`` re-hashes every manifest file, reports each
        mismatch and names the mutation that was in flight (the
        ``inflight.json`` journal);
      - ``--recover`` rewrites the journaled bytes back (the pinned
        interrupted-run path);
  * after the LAST restore the whole manifest is re-verified — a
        post-run mismatch is a loud FAIL, never a silent pass.

The ``--selftest`` probe runs on a COPY (in the scratch directory
``LOG_DIR/selftest``): the working tree is never written, so an
interruption mid-selftest can never leave the production probe file
truncated. It exercises the SAME verifier over its OWN scratch
manifest — the selftest can neither corrupt the tree nor clobber the
matrix's manifest.

The working tree MUST be clean when the matrix starts (the pre-run
manifest shas are recorded against a committed tree, so a dirty tree is
attributable to the matrix alone) — the script refuses to run on a
dirty tree.

Invocation (from the repo root):

    .venv/bin/python test/packs/group_chat_room_mutation_matrix_test.py
    .venv/bin/python test/packs/group_chat_room_mutation_matrix_test.py --verify
    .venv/bin/python test/packs/group_chat_room_mutation_matrix_test.py --recover
    .venv/bin/python test/packs/group_chat_room_mutation_matrix_test.py --selftest
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = Path(
    os.environ.get(
        "GROUP_CHAT_ROOM_MUTATION_LOG_DIR", "/tmp/group-chat-room-mutation"
    )
)
RECOVERY_DIR = LOG_DIR / "recovery"
PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python")

#: The selftest's scratch directory. The selftest's probe runs on a
#: COPY of the probe file, HERE — the working tree is never written
#: (a SIGKILL mid-selftest could otherwise leave the production file
#: truncated, with no journal to restore it from).
SELFTEST_DIR = LOG_DIR / "selftest"

#: The per-variable scrub. TWO families, both scrubbed BY NAME:
#: the ``POSTGRES_*`` family (the glob form is a SILENT NO-OP — bash
#: does not glob env names) and the FULL ``ENSEMBLE_AUTH_*`` family
#: (a PARTIAL auth scrub leaves ``auth.enabled=true`` behind and
#: produces a dozen phantom reds in the router-manifest pins).
SCRUB = [
    "env",
    "-u", "POSTGRES_USER",
    "-u", "POSTGRES_DB",
    "-u", "POSTGRES_HOST",
    "-u", "POSTGRES_PORT",
    "-u", "POSTGRES_PASSWORD",
    "-u", "DATABASE_URL",
    "-u", "PORT",
    "-u", "ENSEMBLE_AUTH_ENABLED",
    "-u", "ENSEMBLE_AUTH_ISSUER",
    "-u", "ENSEMBLE_AUTH_DISCOVERY_URL",
    "-u", "ENSEMBLE_AUTH_CLIENT_ID",
    "-u", "ENSEMBLE_AUTH_CLIENT_SECRET",
    "-u", "ENSEMBLE_AUTH_ALLOWED_ORIGINS",
    "-u", "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE",
]

REGISTRATION_PACK = "tests/unit/tools/test_room_registration.py"
DIGEST_PACK = "tests/unit/services/test_joined_room_context.py"
SPLIT_PACK = "tests/unit/services/test_context_module_split.py"
DELIVERY_PACK = "tests/unit/services/test_room_delivery.py"

#: The mutation table. Every entry is
#: ``(label, relative path, find, replace, pack, red marker)``.
#:
#: The ``red marker`` is a substring that MUST appear in the RED run's
#: output — it proves the mutation was caught by the INTENDED
#: assertion and not by an unrelated crash.
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    # ── GC-M01 (H1 — the shared gate helper): the helper stops
    #    CHECKING the caller. The re-aimed structural pin (the helper's
    #    first logic must be the ``_is_authorized`` check) REDs, and so
    #    do the behavioural refusal pins.
    (
        "GC-M01-gate-helper-check-dropped",
        "daemon/tools/room.py",
        """    if not _is_authorized(caller_agent_id):
        return _refusal(
            label,
            TOKEN_UNKNOWN_AGENT,
            "the room tools require a resolved agent identity",
        )
""",
        """    if False:  # MUTATION: the authorization check was dropped
        return _refusal(
            label,
            TOKEN_UNKNOWN_AGENT,
            "the room tools require a resolved agent identity",
        )
""",
        REGISTRATION_PACK,
        "test_the_shared_gate_helper_checks_the_caller_first",
    ),
    # ── GC-M02 (H1 — the gate stays the body's FIRST logic): a body
    #    that does something before the gate REDs the delegation pin.
    (
        "GC-M02-gate-no-longer-first",
        "daemon/tools/room.py",
        """        gate_refusal = _gate("room_read", caller_agent_id, caller_instance_id)
        if gate_refusal is not None:
            return gate_refusal
""",
        """        _ = None  # MUTATION: the gate is no longer the first logic
        gate_refusal = _gate("room_read", caller_agent_id, caller_instance_id)
        if gate_refusal is not None:
            return gate_refusal
""",
        REGISTRATION_PACK,
        "test_the_gate_is_the_first_logic_in_each_body",
    ),
    # ── GC-M03 (H2 — the section-aware fit): the digest truncation is
    #    the elastic section — dropping it lets the digest crowd the
    #    mandatory sections out (the B-1 defect class).
    (
        "GC-M03-digest-truncation-dropped",
        "daemon/services/joined_room_context.py",
        """    if len(digest_body) > digest_budget:
        digest_body = _cut(digest_body, digest_budget)
""",
        """    if False:  # MUTATION: the digest truncation was dropped
        digest_body = _cut(digest_body, digest_budget)
""",
        DIGEST_PACK,
        "test_a_tight_budget_trims_the_digest_never_a_section",
    ),
    # ── GC-M04 (H2 — the import compatibility): a FORK of the moved
    #    builder inside context_messages REDs both the ownership pin
    #    and the identity pin.
    (
        "GC-M04-moved-builder-forked",
        "daemon/services/context_messages.py",
        """    build_joined_room_context_message,
)
""",
        """    build_joined_room_context_message,
)


def build_joined_room_context_message(*args, **kwargs):  # MUTATION: a fork
    return None
""",
        SPLIT_PACK,
        "test_the_historical_import_surface_resolves_the_same_objects",
    ),
    # ── GC-M05 (the P3 delivery lane): the at_boot guard is the
    #    PINNED PAIR #2 — proposals are NEVER parent-routed at boot.
    (
        "GC-M05-at-boot-guard-disabled",
        "daemon/services/room_delivery.py",
        """        if at_boot:
            # Pinned pair #2 (absolute wording, A-P3-21/24): proposals
            # are NEVER parent-routed at boot — mint-only, durable.
            return "proposal"
""",
        """        if False:  # MUTATION: the at_boot guard was disabled
            # Pinned pair #2 (absolute wording, A-P3-21/24): proposals
            # are NEVER parent-routed at boot — mint-only, durable.
            return "proposal"
""",
        DELIVERY_PACK,
        "test_boot_terminal_race_never_routes_parent",
    ),
    # ── GC-M06 (the P3 delivery lane): the mint-failure ADMISSION
    #    gate — a mint failure must defer (the cursor stays untouched)
    #    so the row re-derives.
    (
        "GC-M06-mint-failure-admission-dropped",
        "daemon/services/room_delivery.py",
        """            self._logger.warning(
                "proposal mint failed (room=%s seq=%s member=%s trigger=%s): %r",
                room_id, post["seq"], member_name, trigger, exc,
            )
            return False
""",
        """            self._logger.warning(
                "proposal mint failed (room=%s seq=%s member=%s trigger=%s): %r",
                room_id, post["seq"], member_name, trigger, exc,
            )
            return True  # MUTATION: the admission gate was dropped
""",
        DELIVERY_PACK,
        "test_mint_failure_route_time_defers_and_keeps_cursor",
    ),
    # ── GC-M07 (H2 — the SIZE criterion): the extraction's own
    #    criterion. The file sits at 2998 lines (two under the ceiling);
    #    padding it back over 3000 MUST red the size pin.
    (
        "GC-M07-size-ceiling-crossed",
        "daemon/services/context_messages.py",
        """    "assemble_context_messages",
]
""",
        """    "assemble_context_messages",
]
"""
        + "\n".join(f"# MUTATION: padding line {i}" for i in range(400))
        + "\n",
        SPLIT_PACK,
        "test_context_messages_is_under_three_thousand_lines",
    ),
    # ── GC-M08 (H2 — the JOINED-ROOM OWNERSHIP): a constant that
    #    is no longer a MODULE-LEVEL definition must red the ownership
    #    pin. The mutation wraps it in ``if True:`` — the name stays
    #    importable (no collateral import failure), only the module-level
    #    DEFINITION the pin looks for is gone.
    (
        "GC-M08-joined-room-name-left-its-module",
        "daemon/services/joined_room_context.py",
        """ROOM_DIGEST_MAX_CHARS = 2000
""",
        """if True:  # MUTATION: no longer a module-level definition
    ROOM_DIGEST_MAX_CHARS = 2000
""",
        SPLIT_PACK,
        "test_the_joined_room_builder_is_defined_in_its_own_module",
    ),
    # ── GC-M09 (H2 — the SHARED-PRIMITIVES OWNERSHIP): the same
    #    break in the shared-primitives module must red ITS pin.
    (
        "GC-M09-shared-primitive-left-its-module",
        "daemon/services/context_block.py",
        """CONTEXT_SUFFIX = "]\\n\\n"
""",
        """if True:  # MUTATION: no longer a module-level definition
    CONTEXT_SUFFIX = "]\\n\\n"
""",
        SPLIT_PACK,
        "test_the_shared_primitives_live_in_the_context_block_module",
    ),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(rel: str) -> Path:
    """Resolve a manifest path: repo-relative by default, absolute
    as-is (the selftest's manifest points at a COPY in its scratch
    directory, outside the repo)."""
    candidate = Path(rel)
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip()


def _touched_files() -> list[str]:
    seen: list[str] = []
    for _label, rel, *_rest in MUTATIONS:
        if rel not in seen:
            seen.append(rel)
    return seen


def _dirty_paths() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def _record_manifest() -> dict:
    manifest = {
        "head_sha": _git("rev-parse", "HEAD"),
        "files": {rel: _sha256(REPO_ROOT / rel) for rel in _touched_files()},
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "pre_run_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def _verify_manifest(manifest: dict) -> list[str]:
    mismatches: list[str] = []
    for rel, sha in manifest["files"].items():
        path = _resolve(rel)
        if not path.exists():
            mismatches.append(f"{rel}: MISSING")
            continue
        if _sha256(path) != sha:
            mismatches.append(f"{rel}: sha mismatch")
    return mismatches


def _run_pack(pack: str) -> tuple[int, str]:
    proc = subprocess.run(
        [
            *SCRUB,
            PYTHON,
            "-m",
            "pytest",
            pack,
            "-q",
            "--tb=line",
            "-p",
            "no:randomly",
            "--timeout=120",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _journal(label: str, rel: str, blob: bytes) -> None:
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    (RECOVERY_DIR / f"{label}.blob").write_bytes(blob)
    (LOG_DIR / "inflight.json").write_text(
        json.dumps({"label": label, "path": rel}, indent=2), encoding="utf-8"
    )


def _unjournal(label: str) -> None:
    (RECOVERY_DIR / f"{label}.blob").unlink(missing_ok=True)
    inflight = LOG_DIR / "inflight.json"
    if inflight.exists():
        try:
            data = json.loads(inflight.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("label") == label:
            inflight.unlink(missing_ok=True)


def run_matrix() -> int:
    dirty = _dirty_paths()
    if dirty:
        print("RESULT: FAIL (DIRTY TREE) — the matrix refuses to run:")
        for line in dirty:
            print(f"  {line}")
        print("  (commit the tree first; a dirty tree is not attributable)")
        return 1

    manifest = _record_manifest()
    print(
        f"pre-run manifest: {manifest['head_sha']} over "
        f"{len(manifest['files'])} files"
    )

    failures = 0
    for label, rel, find, replace, pack, marker in MUTATIONS:
        path = REPO_ROOT / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(find) != 1:
            print(
                f"{label}: SKIPPED (the find-string is not unique: "
                f"{text.count(find)})"
            )
            failures += 1
            continue
        _journal(label, rel, original)
        try:
            path.write_text(text.replace(find, replace), encoding="utf-8")
            rc, out = _run_pack(pack)
            if rc == 0:
                print(f"{label}: FAIL — the mutation did NOT turn the pack RED")
                failures += 1
            elif marker not in out:
                print(
                    f"{label}: FAIL — the pack went RED but the INTENDED "
                    f"assertion ({marker!r}) is not in the output"
                )
                failures += 1
            else:
                print(f"{label}: OK (RED via {marker})")
            path.write_bytes(original)
            rc2, out2 = _run_pack(pack)
            if rc2 != 0:
                print(f"{label}: FAIL — the restore did NOT turn the pack GREEN")
                failures += 1
            elif path.read_bytes() != original:
                print(f"{label}: FAIL — the restore is NOT byte-identical")
                failures += 1
            else:
                print(f"{label}: OK (restored byte-identically, pack GREEN)")
        finally:
            path.write_bytes(original)
            _unjournal(label)

    mismatches = _verify_manifest(manifest)
    if mismatches:
        print("RESULT: FAIL (post-run manifest mismatch)")
        for line in mismatches:
            print(f"  {line}")
        failures += 1
    else:
        print("post-run manifest: every touched file is byte-identical")

    if failures:
        print(f"RESULT: FAIL ({failures} leg(s) failed)")
        return 1
    print(f"RESULT: PASS ({len(MUTATIONS)} RED/GREEN legs)")
    return 0


def verify(manifest_path: Path | None = None) -> int:
    """Re-hash every file the recorded manifest names.

    ``manifest_path`` defaults to the MATRIX's own manifest (the
    ``--verify`` CLI mode). The selftest passes its OWN scratch
    manifest (over a COPY of the probe file) so the two never share
    state — and so the selftest can never delete the matrix's
    manifest.
    """
    if manifest_path is None:
        manifest_path = LOG_DIR / "pre_run_manifest.json"
    if not manifest_path.exists():
        print("no pre-run manifest — nothing to verify")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches = _verify_manifest(manifest)
    inflight = manifest_path.parent / "inflight.json"
    if inflight.exists():
        print(f"IN-FLIGHT mutation: {inflight.read_text(encoding='utf-8').strip()}")
    if mismatches:
        print("RESULT: FAIL (dirty tree)")
        for line in mismatches:
            print(f"  {line}")
        return 1
    print("RESULT: CLEAN (every manifest file is byte-identical)")
    return 0


def recover() -> int:
    inflight = LOG_DIR / "inflight.json"
    if not inflight.exists():
        print("no in-flight mutation — nothing to recover")
        return 0
    data = json.loads(inflight.read_text(encoding="utf-8"))
    label = data.get("label")
    rel = data.get("path")
    blob = RECOVERY_DIR / f"{label}.blob"
    if not blob.exists():
        print(f"RESULT: FAIL (no recovery blob for {label})")
        return 1
    shutil.copyfile(blob, REPO_ROOT / rel)
    _unjournal(label)
    print(f"RESULT: RECOVERED {rel} from the journaled bytes of {label}")
    return 0


def selftest() -> int:
    """Exercise the restore/interruption pins — ON A COPY.

    THE WORKING TREE IS NEVER WRITTEN. The probe copies the probe file
    into the selftest's scratch directory and plants the mutation byte
    in the COPY: the previous shape overwrote ``daemon/tools/room.py``
    itself, so a SIGKILL mid-selftest left the production file
    TRUNCATED — with no journal to restore it from (the matrix's
    journal only covers the matrix's own mutations).

    The manifest/journal/--verify/--recover/--selftest discipline is
    unchanged: the selftest still exercises the SAME verifier, over a
    manifest that records the PRE-PLANT sha (a manifest written from
    the mutated file would match itself and the selftest would be
    vacuous). The manifest lives in the SCRATCH directory, so the
    selftest can never clobber the matrix's own manifest.
    """
    SELFTEST_DIR.mkdir(parents=True, exist_ok=True)
    probe_source = REPO_ROOT / "daemon" / "tools" / "room.py"
    probe = SELFTEST_DIR / "room.py.probe"
    manifest_path = SELFTEST_DIR / "pre_run_manifest.json"
    source_sha = _sha256(probe_source)
    shutil.copyfile(probe_source, probe)
    original = probe.read_bytes()
    try:
        # The manifest records the PRE-PLANT sha — the verifier can then
        # DETECT the planted byte (a manifest written from the mutated
        # file would match itself and the selftest would be vacuous).
        manifest_path.write_text(
            json.dumps(
                {
                    "head_sha": "selftest",
                    "files": {str(probe): _sha256(probe)},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        probe.write_bytes(b"# MUTATION: the selftest byte\n")
        if verify(manifest_path=manifest_path) != 1:
            print("RESULT: FAIL (the verifier did not detect the planted byte)")
            return 1
        probe.write_bytes(original)
        if verify(manifest_path=manifest_path) != 0:
            print("RESULT: FAIL (the verifier did not confirm the restored byte)")
            return 1
        # The working tree was NEVER touched by the probe.
        if _sha256(probe_source) != source_sha:
            print("RESULT: FAIL (the selftest probe touched the working file)")
            return 1
    finally:
        probe.write_bytes(original)
        manifest_path.unlink(missing_ok=True)
        shutil.rmtree(SELFTEST_DIR, ignore_errors=True)
    print(
        "RESULT: PASS (the restore/interruption pins hold; the probe ran "
        "on a copy — the working tree was never written)"
    )
    return 0


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return verify()
    if "--recover" in argv:
        return recover()
    if "--selftest" in argv:
        return selftest()
    return run_matrix()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
