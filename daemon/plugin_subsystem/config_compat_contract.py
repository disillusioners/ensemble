"""Config-compatibility CONTRACT minter (ENSEMBLE-32 repair lane, D-2).

Stage-time companion to ``config_compat_check.py``: this driver runs
INSIDE the build/stage tree (where the TARGET release's real config
model is importable) and emits the model's accepted key-shape as a JSON
contract. The contract travels WITH the staged payload
(``daemon/plugin_subsystem/config_compat_contract.json``) so the
PROMOTE-time checker — which runs from a slice-only payload and CANNOT
import the full daemon package graph — can validate the install's
``config.yaml`` against the TARGET release's model shape.

Why a contract snapshot instead of importing the model at promote time:
the staged release carries only the daemon SLICE (plugin_subsystem
predicate + this module pair); ``daemon.config`` pulls pydantic,
pydantic_settings, PyYAML and the whole ``daemon`` sibling closure —
none of which travel with a slice-only payload (the D-2 evidence:
v0.30-era config.yaml crashed the v0.10.8 rollback binary with a
deterministic pydantic ``extra_forbidden`` AFTER the flip).

Contract shape (nested dict; leaf = null, nested section = dict):
    {"llm": {"fields": {...}}, "daemon": {...}, ...}

Depth is bounded (3) — the incident class is a KEY-SHAPE mismatch
(unknown section/field), not deep type validation.

Exit codes: 0 = contract printed on stdout; 4 = the target model could
not be imported (missing deps / wrong tree) — the caller (stage.sh)
treats 4 as "no contract minted" and WARNS; the promote-time gate then
refuses fail-closed on the missing contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CONTRACT_DEPTH = 3

# Retired config keys that the TARGET release's ``load_config`` still
# tolerates via explicit compat pops (daemon/config.py). The promote gate
# describes what the target release ACCEPTS — and a config carrying one of
# these still boots (load_config silently drops it). Without these entries
# the gate refuses a config the daemon can load: the exact regression the
# engine-arc dead-knob sweep (T1.12) surfaced (RC=3 vs base RC=0). Keep this
# set in lockstep with the pops in daemon/config.py load_config.
_TOLERATED_RETIRED_KEYS: dict[str, tuple[str, ...]] = {
    "persistence": ("checkpointer_db_path", "checkpoint_interval"),
    "services": ("worker_poll_interval", "lease_heartbeat_interval_seconds"),
}


def _is_model(annotation: Any) -> bool:
    """True when the annotation is a nested pydantic model class."""
    cls = annotation
    if hasattr(cls, "__origin__"):  # unwrap Optional[...] / Annotated[...]
        args = [a for a in (getattr(cls, "__args__", ()) or ()) if hasattr(a, "model_fields")]
        if len(args) == 1:
            cls = args[0]
    return hasattr(cls, "model_fields")


def _walk(model_cls: Any, depth: int) -> dict:
    out: dict = {}
    for name, field in model_cls.model_fields.items():
        if depth >= CONTRACT_DEPTH:
            out[name] = None
            continue
        ann = field.annotation
        if _is_model(ann):
            cls = ann
            if hasattr(cls, "__origin__"):
                args = [a for a in (getattr(cls, "__args__", ()) or ()) if hasattr(a, "model_fields")]
                if len(args) == 1:
                    cls = args[0]
            out[name] = _walk(cls, depth + 1)
        else:
            out[name] = None
    return out


def emit(repo_root: Path) -> dict:
    sys.path.insert(0, str(repo_root))
    try:
        from daemon.config import Config  # noqa: PLC0415 — stage-time, tree-bound
    except Exception as exc:  # noqa: BLE001 — the caller warns + proceeds
        print(f"config-contract: model import FAILED: {exc}", file=sys.stderr)
        raise SystemExit(4)
    contract = _walk(Config, 0)
    # A retired key that load_config pops is still ACCEPTED by this release —
    # including it keeps the promote gate from refusing a bootable config.
    for section, keys in _TOLERATED_RETIRED_KEYS.items():
        node = contract.setdefault(section, {})
        for key in keys:
            node[key] = None
    return contract


def main() -> int:
    ap = argparse.ArgumentParser(description="Emit the config-compat key-shape contract")
    ap.add_argument("--repo-root", required=True, help="the TARGET release's build tree")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    contract = emit(Path(args.repo_root).resolve())
    print(json.dumps(contract, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
