"""Config-compatibility CHECKER (ENSEMBLE-32 repair lane, D-2).

Promote-time entry check: validate the INSTALL's ``config.yaml`` against
the TARGET release's config key-shape contract BEFORE any stop/flip.
The D-2 evidence (demo promote 2026-11-15): the auto-rollback landed on
a previous release whose binary rejected the refreshed config.yaml with
a deterministic pydantic ``extra_forbidden`` crash-loop — the halt was
correct, but a promote whose TARGET cannot parse the install config
would crash-loop the same way right after the flip. This check refuses
that class at ENTRY.

Import hygiene (deliberate, mirrors promote_staleness.py): stdlib +
PyYAML only — NO ``daemon`` imports. The TARGET model's shape arrives
as a JSON contract (``config_compat_contract.json``, minted at stage
time by ``config_compat_contract.py`` from the target tree's real
model) staged alongside this module in the daemon slice.

Exit codes (the promote_plugin_staleness rc taxonomy):
    0 = CONFIG-COMPAT=ok — the install config's key shape is accepted
    3 = incompatible — prints ``code=config-incompatible`` + the
        offending path; the caller (lib.sh _config_compat_check)
        refuses (exit 78, the freshness-refusal family) with the
        remedy message
    4 = unrunnable — contract unreadable/absent, config.yaml
        unreadable/absent, or PyYAML unavailable; the caller refuses
        (fail-closed: a check that cannot run never warns-and-passes)

Scope (deliberate, MINIMAL per the lane brief): KEY-SHAPE only — every
key present in the install yaml must exist in the target contract at
the same path, up to the contract depth; deeper yaml levels are
unconstrained; missing keys are fine (model defaults); VALUE/type
validation is the boot path's job.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_INCOMPATIBLE = 3
EXIT_UNRUNNABLE = 4


def _fail_unrunnable(msg: str) -> int:
    print(f"code=config-compat-unrunnable detail={msg}")
    return EXIT_UNRUNNABLE


def _fail_incompatible(path: str, key: str) -> int:
    print(f"code=config-incompatible detail=unknown key '{key}' at {path} "
          f"(not accepted by the target release's config model)")
    return EXIT_INCOMPATIBLE


def _walk(yaml_node: object, contract_node: object, path: str) -> int | None:
    """Return the offending (path, key) at the first unknown key, else None."""
    if not isinstance(yaml_node, dict):
        return None  # scalars/lists under a known key: unconstrained
    if not isinstance(contract_node, dict):
        return None  # contract exhausted (depth bound): deeper yaml unconstrained
    for key, sub_yaml in yaml_node.items():
        if not isinstance(key, str) or key not in contract_node:
            return (path, str(key))
        offender = _walk(sub_yaml, contract_node[key], f"{path}.{key}" if path else key)
        if offender is not None:
            return offender
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("code=config-compat-unrunnable detail=usage: config_compat_check.py <contract.json> <config.yaml>")
        return EXIT_UNRUNNABLE
    contract_path = Path(argv[1])
    config_path = Path(argv[2])

    try:
        import yaml  # noqa: PLC0415 — the PyYAML requirement, checked here
    except Exception as exc:  # noqa: BLE001
        print(f"code=config-compat-unrunnable detail=PyYAML unavailable in the promote interpreter ({exc})")
        return EXIT_UNRUNNABLE

    if not contract_path.is_file():
        print(f"code=config-compat-unrunnable detail=contract not found: {contract_path}")
        return EXIT_UNRUNNABLE
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"code=config-compat-unrunnable detail=contract unreadable: {exc}")
        return EXIT_UNRUNNABLE

    if not config_path.is_file():
        print(f"code=config-compat-unrunnable detail=install config.yaml not found: {config_path} "
              f"(the daemon refuses to boot without it — stage/restore it first)")
        return EXIT_UNRUNNABLE
    try:
        import yaml as _yaml  # noqa: PLC0415
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = _yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"code=config-compat-unrunnable detail=install config.yaml unparseable: {exc}")
        return EXIT_UNRUNNABLE
    if not isinstance(cfg, dict):
        print("code=config-compat-unrunnable detail=install config.yaml is not a mapping")
        return EXIT_UNRUNNABLE

    offender = _walk(cfg, contract, "")
    if offender is not None:
        return _fail_incompatible(offender[0], offender[1])
    print("CONFIG-COMPAT=ok key-shape accepted by the target release's config model")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
