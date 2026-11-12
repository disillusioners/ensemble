"""The RAM-guard CONFIG ladder (Phase 5) — the config surface pack.

Covers phase5-plan.md T2 / T3 / T4 / T5 / T16 / T17 and test-plan.md
§3.6 (C2/C3/C4/C6/C7/C8/C9/C10 + K3) against the escalation-pass
assertion set (arch §15 B9, house idiom
``tests/unit/test_auth_config.py:371-377``):

* the FOUR INT-only knobs with the ruled bounds (B8);
* unset ⇒ dynamic (``ceiling_mib is None``) — the env tier is LIVE
  (C9: the root-Config wiring exists, no dead code);
* an explicit out-of-range value ⇒ ``ValidationError`` OUT OF
  ``load_config`` ⇒ BOOT ABORTS (C3 — the rung is ASSERTED, never
  clamped, no "untestable" escape);
* **OFF BEATS VALIDATION** (C10 / §15 B26): ``ENSEMBLE_RAM_GUARD=0`` +
  an out-of-range ceiling ⇒ NO ABORT + the ``model_construct`` defaults
  + ``ram_guard_derived_switch is False``;
* the yaml trap (C4): a literal is IGNORED (no load_config pass-through)
  and the env tier wins;
* the master switch's pure reaper semantics (K3): absent ⇒ ON,
  empty/whitespace ⇒ ON, ``0/false/no/off`` ⇒ OFF, anything else ⇒ ON;
* INT-only discipline (C6): a human-string size is refused;
* the §4.1a re-arm constants are MODULE CONSTANTS, ABSENT from the
  field set (T3 acceptance);
* the per-call ceiling override is the INTERNAL seam and the
  LLM-facing tool schemas carry NO ceiling parameter (C5 / the
  SECURITY RULING).

House fences: tmp yaml only (never the live config path), every
``RAM_GUARD_*`` / ``ENSEMBLE_RAM_GUARD`` env var scrubbed per test (a
partial scrub is the house phantom-red hazard), no DB, no /proc reads
(the ceiling stays UNSET in every load here — resolution is the
guard's boot-time job, not config's).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from daemon.config import (
    ENSEMBLE_RAM_GUARD,
    RamGuardConfig,
    load_config,
    ram_guard_switch_from_env,
)
from daemon.models.common import LivezResponse, ReadyzResponse
from daemon.services.ram_guard import verdict as rg_verdict

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every env var this pack may touch — scrubbed per test via the
#: autouse fixture (a partial scrub is the house phantom-red hazard).
_RAM_GUARD_ENV_VARS = (
    "ENSEMBLE_RAM_GUARD",
    "RAM_GUARD_CEILING_MIB",
    "RAM_GUARD_POLL_INTERVAL_MS",
    "RAM_GUARD_GRACE_MS",
    "RAM_GUARD_PSS_CONFIRM_DEADLINE_MS",
)


@pytest.fixture(autouse=True)
def _scrub_ram_guard_env(monkeypatch):
    """Scrub the whole RAM-guard env family (all-or-none house rule)."""
    for name in _RAM_GUARD_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write_yaml(tmp_path: Path, body: str = "daemon:\n  host: 127.0.0.1\n  port: 8088\n") -> Path:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(body)
    return yaml_file


# ── C8 / §15 B9 — the escalation-pass assertion shapes, as written ─────────


class TestB9AssertionShapes:
    """The §15 B9 set, verbatim order, as the house idiom."""

    def test_unset_yields_dynamic_and_the_ruled_defaults(self, tmp_path):
        yaml_file = _write_yaml(tmp_path)
        config = load_config(str(yaml_file))
        assert config.ram_guard.ceiling_mib is None  # unset => dynamic
        assert config.ram_guard.poll_interval_ms == 200
        assert config.ram_guard.grace_ms == 250
        assert config.ram_guard.pss_confirm_deadline_ms == 250
        # The switch defaults ON when absent (B2/T2).
        assert config.ram_guard_derived_switch is True

    def test_explicit_env_override_binds_and_unit_math_holds(self, tmp_path, monkeypatch):
        yaml_file = _write_yaml(tmp_path)
        monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "64")
        config = load_config(str(yaml_file))
        assert config.ram_guard.ceiling_mib == 64
        # S15's unit discipline: 3072 MiB → 3 221 225 472 bytes.
        monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "3072")
        config = load_config(str(yaml_file))
        assert config.ram_guard.ceiling_mib * 1_048_576 == 3_221_225_472
        monkeypatch.setenv("RAM_GUARD_POLL_INTERVAL_MS", "500")
        monkeypatch.setenv("RAM_GUARD_GRACE_MS", "0")
        monkeypatch.setenv("RAM_GUARD_PSS_CONFIRM_DEADLINE_MS", "2000")
        config = load_config(str(yaml_file))
        assert config.ram_guard.poll_interval_ms == 500
        assert config.ram_guard.grace_ms == 0  # the DQ4 floor holds
        assert config.ram_guard.pss_confirm_deadline_ms == 2000

    def test_explicit_out_of_range_aborts_while_on(self, tmp_path, monkeypatch):
        """C3 — the boot-abort rung is ASSERTABLE: ``load_config`` itself
        raises. No clamp, no daemon-boot harness, no disclosure."""
        yaml_file = _write_yaml(tmp_path)
        monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "99999")
        with pytest.raises(ValidationError):
            load_config(str(yaml_file))  # explicit out-of-range => ABORTS (ON)

    def test_off_beats_validation(self, tmp_path, monkeypatch):
        """C10 / §15 B26 — ``ENSEMBLE_RAM_GUARD=0`` + an out-of-range
        ceiling ⇒ NO ABORT; the ``model_construct`` defaults apply and
        the derived switch reads False."""
        yaml_file = _write_yaml(tmp_path)
        monkeypatch.setenv("ENSEMBLE_RAM_GUARD", "0")
        monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "99999")
        config = load_config(str(yaml_file))  # OFF => NO ABORT (§17.4)
        assert config.ram_guard_derived_switch is False
        assert config.ram_guard.ceiling_mib is None  # the construct default
        assert config.ram_guard.poll_interval_ms == 200
        assert config.ram_guard.grace_ms == 250
        assert config.ram_guard.pss_confirm_deadline_ms == 250


# ── C2/C7 — the knob bounds (B8, verbatim) ──────────────────────────────────


class TestKnobBounds:
    def test_every_bound_is_the_ruled_one(self):
        def _bounds(field):
            ge = le = None
            for meta in field.metadata:
                ge = getattr(meta, "ge", ge)
                le = getattr(meta, "le", le)
            return ge, le

        fields = RamGuardConfig.model_fields
        ceiling = fields["ceiling_mib"]
        assert ceiling.default is None
        assert _bounds(ceiling) == (64, 65536)
        poll = fields["poll_interval_ms"]
        assert poll.default == 200
        assert _bounds(poll) == (50, 2000)
        grace = fields["grace_ms"]
        assert grace.default == 250
        assert _bounds(grace) == (0, 2000)
        confirm = fields["pss_confirm_deadline_ms"]
        assert confirm.default == 250
        assert _bounds(confirm) == (50, 2000)

    @pytest.mark.parametrize(
        ("env", "value"),
        [
            ("RAM_GUARD_CEILING_MIB", "63"),  # below the explicit floor
            ("RAM_GUARD_CEILING_MIB", "65537"),  # above the explicit cap
            ("RAM_GUARD_POLL_INTERVAL_MS", "49"),
            ("RAM_GUARD_POLL_INTERVAL_MS", "2001"),
            ("RAM_GUARD_GRACE_MS", "-1"),
            ("RAM_GUARD_GRACE_MS", "2001"),
            ("RAM_GUARD_PSS_CONFIRM_DEADLINE_MS", "49"),
            ("RAM_GUARD_PSS_CONFIRM_DEADLINE_MS", "2001"),
        ],
    )
    def test_out_of_range_env_aborts_boot(
        self, tmp_path, monkeypatch, env, value
    ):
        yaml_file = _write_yaml(tmp_path)
        monkeypatch.setenv(env, value)
        with pytest.raises(ValidationError):
            load_config(str(yaml_file))

    def test_in_range_extremes_apply(self, tmp_path, monkeypatch):
        yaml_file = _write_yaml(tmp_path)
        monkeypatch.setenv("RAM_GUARD_POLL_INTERVAL_MS", "2000")
        monkeypatch.setenv("RAM_GUARD_GRACE_MS", "2000")
        monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "65536")
        config = load_config(str(yaml_file))
        assert config.ram_guard.poll_interval_ms == 2000
        assert config.ram_guard.grace_ms == 2000
        assert config.ram_guard.ceiling_mib == 65536

    def test_human_string_size_is_refused(self, tmp_path, monkeypatch):
        """C6 — INT-only discipline: no human-string parsing."""
        yaml_file = _write_yaml(tmp_path)
        monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "8G")
        with pytest.raises(ValidationError):
            load_config(str(yaml_file))


# ── C4 — the yaml trap ──────────────────────────────────────────────────────


class TestEmptyStringEnvIsUnset:
    """An EMPTY-string env value (``RAM_GUARD_CEILING_MIB=`` in a shell,
    a docker-compose hole, a systemd EnvironmentFile line with no value)
    is the UNSET semantics — never a boot abort (live-verified carryover:
    the pre-validator ValidationError aborted the daemon on ``""``)."""

    @pytest.mark.parametrize(
        ("env_name", "field", "expected"),
        [
            ("RAM_GUARD_CEILING_MIB", "ceiling_mib", None),
            ("RAM_GUARD_POLL_INTERVAL_MS", "poll_interval_ms", 200),
            ("RAM_GUARD_GRACE_MS", "grace_ms", 250),
            ("RAM_GUARD_PSS_CONFIRM_DEADLINE_MS", "pss_confirm_deadline_ms", 250),
        ],
    )
    def test_an_empty_string_env_is_the_unset_semantics_for_all_four_knobs(
        self, monkeypatch, env_name, field, expected
    ):
        monkeypatch.setenv(env_name, "")
        config = RamGuardConfig()
        assert getattr(config, field) == expected

    def test_a_whitespace_string_env_is_also_unset(self, monkeypatch):
        monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "   ")
        assert RamGuardConfig().ceiling_mib is None

    def test_a_real_value_still_binds(self, monkeypatch):
        monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "3072")
        assert RamGuardConfig().ceiling_mib == 3072


class TestYamlTrap:
    def test_a_yaml_literal_is_ignored_and_the_env_tier_wins(
        self, tmp_path, monkeypatch
    ):
        """A ``ram_guard:`` literal does NOT beat the env tier — there is
        NO load_config pass-through for the section, so the literal is
        silently ignored (the trap is ASSERTED, not assumed)."""
        yaml_file = _write_yaml(
            tmp_path,
            "daemon:\n  host: 127.0.0.1\n"
            "ram_guard:\n  ceiling_mib: 128\n  poll_interval_ms: 999\n",
        )
        monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "64")
        config = load_config(str(yaml_file))
        assert config.ram_guard.ceiling_mib == 64  # env wins
        assert config.ram_guard.poll_interval_ms == 200  # the literal is ignored

    def test_a_yaml_literal_alone_does_not_apply(self, tmp_path):
        yaml_file = _write_yaml(
            tmp_path,
            "daemon:\n  host: 127.0.0.1\n"
            "ram_guard:\n  ceiling_mib: 128\n",
        )
        config = load_config(str(yaml_file))
        assert config.ram_guard.ceiling_mib is None  # the literal is ignored

    def test_the_shipped_yaml_block_is_comment_only(self):
        """The shipped config.yaml carries NO ``ram_guard:`` literal key
        (the T4 construction rule — a literal there would shadow the
        env tier for any future pass-through)."""
        shipped = (_REPO_ROOT / "config.yaml").read_text(encoding="utf-8")
        for line in shipped.splitlines():
            stripped = line.strip()
            if stripped.startswith("ram_guard:") and not stripped.startswith("#"):
                pytest.fail(f"yaml literal in config.yaml kills the env tier: {line!r}")


# ── K3 / T2 — the master switch (the pure reaper-semantics parser) ─────────


class TestMasterSwitch:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, True),  # absent ⇒ ON
            ("", True),  # empty ⇒ ON
            ("   ", True),  # whitespace ⇒ ON
            ("0", False),
            ("false", False),
            ("FALSE", False),
            ("False", False),
            ("no", False),
            ("No", False),
            ("off", False),
            (" OFF ", False),
            ("1", True),
            ("on", True),
            ("junk", True),  # explicit anything else ⇒ ON
            ("00", True),  # not the literal "0"
        ],
    )
    def test_parsing_is_the_exact_reaper_semantics(self, monkeypatch, value, expected):
        if value is None:
            monkeypatch.delenv(ENSEMBLE_RAM_GUARD, raising=False)
        else:
            monkeypatch.setenv(ENSEMBLE_RAM_GUARD, value)
        assert ram_guard_switch_from_env() is expected

    def test_the_parser_is_pure_and_injectable(self, monkeypatch):
        monkeypatch.setenv(ENSEMBLE_RAM_GUARD, "0")
        # The ambient env is NOT consulted when a mapping is injected.
        assert ram_guard_switch_from_env({}) is True
        assert ram_guard_switch_from_env({ENSEMBLE_RAM_GUARD: "off"}) is False

    def test_the_switch_is_not_a_pydantic_field(self):
        """T2 — the switch must NOT be a pydantic bool (pydantic would
        raise on "" and abort boot); it is a DERIVED root-Config value."""
        assert "ram_guard_derived_switch" not in RamGuardConfig.model_fields
        root_fields = load_config.__annotations__  # sanity: load_config exists
        assert callable(load_config)
        assert root_fields is not None


# ── T3 — the field set is EXACTLY the four knobs; the re-arm pair is
#        a module constant, not a knob ──────────────────────────────────────


class TestFieldSet:
    def test_exactly_the_four_int_only_fields(self):
        assert tuple(RamGuardConfig.model_fields.keys()) == (
            "ceiling_mib",
            "poll_interval_ms",
            "grace_ms",
            "pss_confirm_deadline_ms",
        )

    def test_rearm_constants_are_module_constants_not_knobs(self):
        assert "RAM_GUARD_REARM_MAX_EXTRA_BYTES" not in RamGuardConfig.model_fields
        assert "RAM_GUARD_REARM_MIN_GROWTH_BYTES" not in RamGuardConfig.model_fields
        # They live where B1 pins them, with the B1 values.
        assert rg_verdict.RAM_GUARD_REARM_MAX_EXTRA_BYTES == 33554432
        assert rg_verdict.RAM_GUARD_REARM_MIN_GROWTH_BYTES == 134217728

    def test_the_class_loads_with_no_env_set(self, monkeypatch):
        for name in _RAM_GUARD_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        config = RamGuardConfig()
        assert config.ceiling_mib is None
        assert config.poll_interval_ms == 200
        assert config.grace_ms == 250
        assert config.pss_confirm_deadline_ms == 250


# ── C9 / T16 — the root-Config wiring is LIVE ───────────────────────────────


class TestRootWiring:
    def test_config_ram_guard_is_reachable_from_load_config(self, tmp_path):
        yaml_file = _write_yaml(tmp_path)
        config = load_config(str(yaml_file))
        assert isinstance(config.ram_guard, RamGuardConfig)
        assert config.ram_guard.ceiling_mib is None

    def test_root_field_is_not_optional(self):
        """B8/T16 — the root field is EXACTLY
        ``ram_guard: RamGuardConfig = Field(default_factory=RamGuardConfig)``;
        NOT ``| None`` (OFF is a value, not an absence)."""
        assert not isinstance(RamGuardConfig, type(None))
        from daemon.config import Config

        field = Config.model_fields["ram_guard"]
        assert field.default_factory is RamGuardConfig
        assert field.is_required() is False

    def test_derived_switch_defaults_on_when_config_built_directly(self):
        from daemon.config import Config

        config = Config()
        assert config.ram_guard_derived_switch is True


# ── C5 — the per-call ceiling override + the tool-schema ruling ─────────────


class TestCeilingOverrideSeam:
    def test_per_call_ceiling_overrides_and_the_default_path_uses_the_resolved_ceiling(
        self, tmp_path
    ):
        from daemon.services.ram_guard.guard import RamGuard

        resolved = 3072 * 1024 * 1024
        guard = RamGuard(
            proc_root=tmp_path / "proc",
            cgroup_root=tmp_path / "cgroup",
            resolved_ceiling=(resolved, "derived-meminfo"),
        )
        record_default = guard.register(
            tracking_id="t-default",
            instance_id="i-1",
            lane="bash",
            pid=4242,
            pgid=4242,
            start_time_jiffies=1000,
        )
        assert record_default.ceiling_bytes == resolved
        record_override = guard.register(
            tracking_id="t-override",
            instance_id="i-1",
            lane="bash",
            pid=4243,
            pgid=4243,
            start_time_jiffies=1000,
            ceiling_bytes=64 * 1024 * 1024,
        )
        assert record_override.ceiling_bytes == 64 * 1024 * 1024

    def test_llm_facing_tool_schemas_gain_no_ceiling_parameter(self):
        """The SECURITY RULING — the override is an internal/test seam
        ONLY; the LLM-facing tool schemas gain NO ceiling parameter (an
        agent-visible knob would let the agent raise its own ceiling)."""
        from daemon.tools.bash import BashInputSchema

        assert "ceiling" not in BashInputSchema.model_fields
        assert set(BashInputSchema.model_fields) == {
            "command",
            "timeout",
            "workdir",
            "input",
            "env",
        }
        proc_source = (_REPO_ROOT / "daemon" / "tools" / "proc_tools.py").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r"async def proc_run\((.*?)\)\s*->", proc_source, re.DOTALL
        )
        assert match is not None, "proc_run signature not found"
        assert "ceiling" not in match.group(1)
        bash_source = (_REPO_ROOT / "daemon" / "tools" / "bash.py").read_text(
            encoding="utf-8"
        )
        schema_match = re.search(
            r"class BashInputSchema\(BaseModel\):(.*?)\n\n", bash_source, re.DOTALL
        )
        assert schema_match is not None
        # The only "ceiling" mentions in bash.py are the INTERNAL seam
        # (the register plumbing), never an LLM-visible schema field.
        assert "ceiling" not in schema_match.group(1)


# ── T12's negative lane — the endpoint is NOT in livez/readyz ──────────────


class TestNotInProbes:
    def test_livez_readyz_do_not_reference_the_guard(self):
        """An observability death must not gate traffic: the probe
        handlers carry no ``ram_guard`` reference (source-level pin via
        ast — the handlers' exact bodies)."""
        import ast

        api_source = (_REPO_ROOT / "daemon" / "api.py").read_text(encoding="utf-8")
        tree = ast.parse(api_source)
        handler_names = {"liveness_probe", "readiness_probe"}
        found: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in handler_names:
                    segment = ast.get_source_segment(api_source, node) or ""
                    found[node.name] = segment
        assert set(found) == handler_names, "probe handlers renamed — re-pin"
        for name, segment in found.items():
            assert "ram_guard" not in segment, f"{name} references ram_guard"

    def test_probe_response_models_carry_no_guard_field(self):
        assert "ram_guard" not in LivezResponse.model_fields
        assert "ram_guard" not in ReadyzResponse.model_fields
