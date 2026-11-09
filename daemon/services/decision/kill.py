"""Decision module — the kill channel (``controls-and-phasing.md`` §3.1.1).

The kill channel is the **daemon-settings surface** — a DB-backed,
``settings:write``-scoped store — and NEVER the agent-callable
per-instance tunable (``set_instance_tunable`` writes ``instance_metadata``
and is allowed by ~22 agent metas: an agent could otherwise re-enable a
killed overlay, the inverse of the failure class this module exists to
prevent).

Three properties are load-bearing:

1. **LIVE READ, PER CONSULT.** The switch is resolved at the call site
   on EVERY path, so a flip takes effect on the **next consult** — no
   restart, no deploy.
2. **FAIL-CLOSED POLARITY** (§3.2.1). An unreadable / unparseable /
   unknown switch state means the overlay is **OFF** for that seam. An
   overlay switch must never fail to ENABLED.
3. **WRITE → READ-BACK → VERIFY.** A write is only believed after the
   stored value has been read back and compared. A write failure or a
   read-back mismatch resolves the switch OFF and raises a
   ``switch_unreadable`` suppression.

The resolution ladder is the house one — **DB > env > yaml > default**
(``daemon/services/settings_service.py`` §"Resolution ladder"). The
env tier is the BOOT SEED only: ``boot_seed`` writes it into the store
**only where the store has no existing value**, so the DB tier wins from
the first boot onward and **a kill is never undone by a restart**.

**THE WRITE PATH.** Switch writes (the boot seed AND the auto-trip's
both-keys write) go through the sanctioned
``SettingsService.set_override`` path — the operator-visible, audited,
invalidation-hooking path. ``set_override`` is REGISTRY-GATED (an
unregistered key raises ``UnknownManagedKeyError``) and VALIDATES
before writing, so the three overlay switch keys MUST be registered in
``MANAGED_KEYS`` by the wiring round before this path can carry them.

⚠️ ``set_override`` itself writes NO audit line — the audit line is the
ROUTER's (``daemon/routers/settings.py``: ``[Settings] db override set
key=%s value=%r subject=%s``). A daemon-side write (the boot seed, the
auto-trip) therefore writes the row but emits no router audit line, so
this module emits its own line in the SAME format — one grep covers
both surfaces.

``set_override`` is SYNCHRONOUS (it opens its own Session), so it is
run OFF the event loop through ``daemon.services.db_offload.offload``
(the house sync-DB seam — the router uses ``asyncio.to_thread`` for
the same reason).

**THE RESOLVED-STATE CACHE (L5).** "Read per consult" must NOT be a
synchronous DB read per consult: ``get_overrides()`` opens a NEW
Session and queries EVERY call with no cache, and it is SYNC — a sync
DB read per consult would put a DB round-trip on the hot path and
violate the house event-loop rule.

So the RESOLVED switch state is cached in-process and the cache is
invalidated by the settings WRITE path (``set_override`` /
``clear_override``). The daemon is the only writer of that DB tier, so
write-path invalidation suffices:

* a kill written through the live settings channel takes effect on the
  **next consult** — no restart, no deploy (the control is unchanged);
* the kill SURVIVES a restart — the boot re-verification re-reads the
  store and never overwrites a stored value.

**FAIL-CLOSED.** If the state cannot be resolved (the cache is cold AND
the read fails), the seam resolves OFF, NO call is made, and a
``switch_unreadable`` suppressed row is emitted (FLAG12).

The HTTP route + the managed-key registration belong to the wiring
round; this module owns the RESOLUTION and the WRITE VERIFICATION and
exposes them as a service-level API.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from daemon.services.decision.types import DecisionStage

logger = logging.getLogger(__name__)

__all__ = [
    "AppSettingKillStore",
    "GLOBAL_ENABLED_KEY",
    "InMemoryKillStore",
    "KillChannelResolver",
    "KillStore",
    "ReverifyOutcome",
    "SeedOutcome",
    "SwitchState",
    "WriteResult",
    "flatten_seam_id",
    "seam_enabled_key",
    "seam_env_name",
    "seam_stage_key",
]


#: The global master's store key.
GLOBAL_ENABLED_KEY = "decision_global_enabled"

#: The env seed for the global master.
GLOBAL_ENABLED_ENV = "DECISION_ENABLED"

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", "disabled"})


def flatten_seam_id(seam_id: str) -> str:
    """The UNDERSCORE-FLATTENED seam id used in the store keys.

    L7: the KEY (and the managed key's ``config_path``) carries the
    flattened form — ``snapshot.l3.selection`` → ``snapshot_l3_selection``
    — because the settings service's ``config_path`` walk DOT-SPLITS
    (a dotted seam id would walk into a non-existent attribute, and
    the crash is LATENT: it fires the first time an operator writes a
    DB override). The human-readable ``snapshot.l3.selection`` stays
    the registry record's ``seam_id``.
    """
    return "".join(ch if ch.isalnum() else "_" for ch in seam_id).lower()


def seam_enabled_key(seam_id: str) -> str:
    """The per-seam enabled store key — ``decision_seam_<flat>_enabled``."""
    return f"decision_seam_{flatten_seam_id(seam_id)}_enabled"


def seam_stage_key(seam_id: str) -> str:
    """The per-seam stage store key — ``decision_seam_<flat>_stage``."""
    return f"decision_seam_{flatten_seam_id(seam_id)}_stage"


def seam_env_name(seam_id: str) -> str:
    """The per-seam env seed name — ``DECISION_SEAM_<ID>``.

    The seam id's punctuation is folded to underscores so the env name
    is a legal shell identifier (``snapshot.l3.selection`` →
    ``DECISION_SEAM_SNAPSHOT_L3_SELECTION``).
    """
    slug = "".join(ch if ch.isalnum() else "_" for ch in seam_id).upper()
    return f"DECISION_SEAM_{slug}"


# ── the store seam ───────────────────────────────────────────────────────


class KillStore(Protocol):
    """The DB-backed switch store seam.

    Implementations persist the three key families above. The keys live
    in the daemon's ``app_settings`` table (the same DB-backed settings
    surface the operator writes through ``PUT /api/settings/daemon/...``).
    """

    async def get(self, key: str) -> str | None:
        """Read one raw value; ``None`` when the key has no row."""
        ...

    async def set(self, key: str, value: str) -> None:
        """Write one raw value (overwrite)."""
        ...


class InMemoryKillStore:
    """An in-process store — the default for tests and for a daemon with
    no settings table wired yet.

    It implements the SAME contract as :class:`AppSettingKillStore`, so
    the resolver's behaviour is identical either way.
    """

    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self._values: dict[str, str] = dict(initial or {})

    async def get(self, key: str) -> str | None:
        return self._values.get(key)

    async def set(self, key: str, value: str) -> None:
        self._values[key] = value

    def snapshot(self) -> dict[str, str]:
        """A copy of the stored values (test/diagnostic helper)."""
        return dict(self._values)


class AppSettingKillStore:
    """The DB-backed store — one row per switch key in ``app_settings``.

    Every DB access runs through ``daemon.services.db_offload.offload``
    so a synchronous session NEVER runs on the event loop (house rule).
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def get(self, key: str) -> str | None:
        """Read one raw value (off-loop).

        D2 — a read ERROR is RE-RAISED so the resolver's fail-closed
        ``switch_unreadable`` branch is REACHABLE in production. Only
        "no row" returns ``None``; a broken engine, a dropped table or
        a connection failure raises, and the resolver maps that to
        OFF + a ``switch_unreadable`` suppressed row.
        """
        from daemon.services.db_offload import offload

        return await offload(self._read_sync, key)

    async def set(self, key: str, value: str) -> None:
        """Write one raw value (off-loop)."""
        from daemon.services.db_offload import offload

        await offload(self._write_sync, key, value)

    # ── the off-loop bodies (no session across an await boundary) ──────

    def _read_sync(self, key: str) -> str | None:
        """Read one raw value — RAISING on a read error (D2).

        "No row" returns ``None`` (a legitimate empty tier). Any other
        failure — a broken engine, a dropped table, a connection
        failure — is RE-RAISED so the resolver's fail-closed
        ``switch_unreadable`` branch is reachable in production.
        """
        from sqlmodel import Session, select

        from daemon.models.app_settings import AppSetting

        try:
            with Session(self._engine) as session:
                row = session.exec(
                    select(AppSetting).where(AppSetting.key == key)
                ).first()
                if row is None:
                    return None
                try:
                    decoded = json.loads(row.value_json)
                except (ValueError, TypeError):
                    return row.value_json
                return decoded if isinstance(decoded, str) else str(decoded)
        except Exception:
            raise

    def _write_sync(self, key: str, value: str) -> None:
        from datetime import datetime, timezone
        from sqlmodel import Session, select

        from daemon.models.app_settings import AppSetting

        now = datetime.now(timezone.utc).isoformat()
        with Session(self._engine) as session:
            row = session.exec(select(AppSetting).where(AppSetting.key == key)).first()
            if row is None:
                session.add(
                    AppSetting(
                        key=key,
                        value_json=json.dumps(value),
                        value_type="str",
                        updated_by=None,
                        updated_at=now,
                    )
                )
            else:
                row.value_json = json.dumps(value)
                row.value_type = "str"
                row.updated_at = now
            session.commit()


# ── resolved state ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class SwitchState:
    """The resolved switch state for one seam — read PER consult.

    Attributes:
        readable: False when any tier carried an unparseable value (the
            switch then resolves OFF and the caller raises a
            ``switch_unreadable`` suppression).
        global_on: The global master's state.
        seam_on: The per-seam enabled state.
        stage: The per-seam stage (``shadow`` by default).
        source: The tier each value came from (``db`` / ``env`` /
            ``yaml`` / ``default``).
    """

    readable: bool = True
    global_on: bool = False
    seam_on: bool = False
    stage: DecisionStage = DecisionStage.SHADOW
    source: Mapping[str, str] = field(default_factory=dict)

    @property
    def consult_allowed(self) -> bool:
        """True when the CONSULT gate may open (§3.2.2 composition).

        The STAGE does **not** gate consultation (D3 + FLAG12): a seam
        in ``shadow`` is still consulted — its answer is only LOGGED.
        """
        return self.readable and self.global_on and self.seam_on


@dataclass(frozen=True)
class WriteResult:
    """The outcome of one kill write."""

    ok: bool
    verified: Mapping[str, bool] = field(default_factory=dict)
    mismatched: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class SeedOutcome:
    """The outcome of one boot seed attempt."""

    key: str
    seeded: bool
    reason: str


@dataclass(frozen=True)
class ReverifyOutcome:
    """The outcome of one boot re-verification."""

    seam_id: str
    outcome: str  # verified_off | verified_on | unreadable


class KillChannelResolver:
    """Resolve + write the overlay's kill switches.

    Args:
        store: The DB-backed switch store.
        env: The environment mapping (defaults to ``os.environ``).
        yaml_snapshot: The yaml tier (a pre-parsed mapping). Empty by
            default — the keys have no yaml literal today.
    """

    def __init__(
        self,
        store: KillStore,
        *,
        env: Mapping[str, str] | None = None,
        yaml_snapshot: Any = None,
        cache_ttl_s: float = 0.0,
        override_writer: Callable[[str, Any, str | None], Any] | None = None,
    ) -> None:
        self._store = store
        self._env: Mapping[str, str] = os.environ if env is None else env
        self._yaml: Any = yaml_snapshot
        # The sanctioned write path — ``SettingsService.set_override``
        # (SYNC; the wiring round binds it). ``None`` ⇒ the writes go
        # straight to the store (the pre-wiring path).
        self._override_writer = override_writer
        # The resolved-state cache (L5). ``cache_ttl_s`` is 0 by default:
        # the cache is invalidated by the WRITE path, not by a timer.
        self._cache_ttl = max(0.0, float(cache_ttl_s))
        self._cache: dict[str, tuple[float, SwitchState]] = {}
        # D2 (council obligation 8): a seam whose WRITE did not read back
        # as written is POISONED — the store's value is untrustworthy, so
        # every subsequent read resolves UNREADABLE ⇒ OFF. Cleared by an
        # explicit invalidate()/reverify() (an operator retry).
        self._poisoned: set[str] = set()

    # ── read ────────────────────────────────────────────────────────────

    async def read_state(self, seam_id: str) -> SwitchState:
        """Resolve the switch state for one seam — PER CONSULT.

        The RESOLVED state is served from the in-process cache (L5); a
        cache miss resolves it (DB > env > yaml > default) and caches
        it. The cache is invalidated by the settings WRITE path (see
        :meth:`invalidate`).

        Never raises: an unreadable store resolves the switches OFF and
        reports ``readable=False`` (the caller raises a
        ``switch_unreadable`` suppression and makes NO call).
        """
        if seam_id in self._poisoned:
            # D2 — a failed write left this seam's store value
            # untrustworthy: resolve UNREADABLE ⇒ OFF (never the stale ON).
            return SwitchState(readable=False)
        cached = self._cached(seam_id)
        if cached is not None:
            return cached
        state = await self._resolve_state(seam_id)
        self._cache[seam_id] = (self._now(), state)
        return state

    def _cached(self, seam_id: str) -> SwitchState | None:
        """The cached resolved state, when it is still valid."""
        entry = self._cache.get(seam_id)
        if entry is None:
            return None
        stored_at, state = entry
        if self._cache_ttl and (self._now() - stored_at) >= self._cache_ttl:
            self._cache.pop(seam_id, None)
            return None
        return state

    def invalidate(self, seam_id: str | None = None) -> None:
        """INVALIDATE the resolved-state cache — the write-path hook (L5).

        The wiring round calls this from the settings write path
        (``set_override`` / ``clear_override``) so a kill written
        through the live channel takes effect on the NEXT consult.

        An explicit ``invalidate()`` is also an operator RETRY: it
        clears a D2 write-failure poison so the next consult re-reads
        the store.

        ``seam_id=None`` invalidates every cached seam.
        """
        self._drop_cache(seam_id)
        if seam_id is None:
            self._poisoned.clear()
        else:
            self._poisoned.discard(seam_id)

    def _drop_cache(self, seam_id: str | None = None) -> None:
        """Drop the CACHE only — the poison survives (D2)."""
        if seam_id is None:
            self._cache.clear()
            return
        self._cache.pop(seam_id, None)

    def _now(self) -> float:
        """The monotonic clock (the cache's timestamp)."""
        import time as _time

        return _time.monotonic()

    async def _resolve_state(self, seam_id: str) -> SwitchState:
        """Resolve the switch state (uncached) — the ladder walk."""
        global_value, global_source, global_readable = await self._resolve_enabled(
            GLOBAL_ENABLED_KEY, GLOBAL_ENABLED_ENV
        )
        seam_value, seam_source, seam_readable = await self._resolve_enabled(
            seam_enabled_key(seam_id), seam_env_name(seam_id)
        )
        stage_value, stage_source, stage_readable = await self._resolve_stage(
            seam_stage_key(seam_id), f"{seam_env_name(seam_id)}_STAGE"
        )
        return SwitchState(
            readable=global_readable and seam_readable and stage_readable,
            global_on=global_value,
            seam_on=seam_value,
            stage=stage_value,
            source={
                GLOBAL_ENABLED_KEY: global_source,
                seam_enabled_key(seam_id): seam_source,
                seam_stage_key(seam_id): stage_source,
            },
        )

    async def _resolve_enabled(
        self, key: str, env_name: str
    ) -> tuple[bool, str, bool]:
        """Resolve one boolean switch over the ladder.

        Returns ``(value, source, readable)``.
        """
        raw, source = await self._raw_tier(key, env_name)
        if source == "unreadable":
            return False, source, False
        if raw is None:
            return False, source, True
        parsed = _parse_enabled(raw)
        if parsed is None:
            # Unparseable ⇒ fail CLOSED (§3.2.1): the switch is OFF.
            logger.warning(
                "decision switch %s is unparseable (source=%s) — resolving OFF",
                key,
                source,
            )
            return False, source, False
        return parsed, source, True

    async def _resolve_stage(
        self, key: str, env_name: str
    ) -> tuple[DecisionStage, str, bool]:
        """Resolve the per-seam stage over the ladder."""
        raw, source = await self._raw_tier(key, env_name)
        if source == "unreadable":
            return DecisionStage.SHADOW, source, False
        if raw is None:
            return DecisionStage.SHADOW, source, True
        text = raw.strip().lower()
        if text == DecisionStage.SHADOW.value:
            return DecisionStage.SHADOW, source, True
        if text == DecisionStage.DECIDING.value:
            return DecisionStage.DECIDING, source, True
        logger.warning(
            "decision stage %s is unparseable (source=%s) — resolving shadow",
            key,
            source,
        )
        return DecisionStage.SHADOW, source, False

    async def _raw_tier(
        self, key: str, env_name: str
    ) -> tuple[str | None, str]:
        """Walk the ladder: DB > env > yaml > default.

        Returns ``(raw_value_or_None, source)``.
        """
        try:
            stored = await self._store.get(key)
        except Exception as exc:  # noqa: BLE001 — the store never raises
            logger.warning(
                "decision switch store read failed for %s: %s: %s",
                key,
                type(exc).__name__,
                exc,
            )
            # FAIL-CLOSED (L5): the state could not be resolved ⇒ the
            # switch is OFF and the state is UNREADABLE.
            return None, "unreadable"
        if stored is not None:
            return str(stored), "db"

        env_value = self._env.get(env_name)
        if env_value is not None and str(env_value).strip():
            return str(env_value), "env"

        yaml_value = self._yaml_tier().get(key)
        if yaml_value is not None:
            return str(yaml_value), "yaml"

        return None, "default"

    def _yaml_tier(self) -> Mapping[str, Any]:
        """The yaml tier, keyed by the store keys.

        Accepts a plain mapping (keyed by the store keys) OR the house
        config object — its ``yaml_switch_tier()`` renders the same
        mapping (the decision module's ``DecisionConfig`` provides it).
        """
        tier = self._yaml
        if tier is None:
            return {}
        renderer = getattr(tier, "yaml_switch_tier", None)
        if callable(renderer):
            try:
                rendered = renderer()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "decision switch yaml tier failed (non-fatal): %s: %s",
                    type(exc).__name__,
                    exc,
                )
                return {}
            return rendered if isinstance(rendered, Mapping) else {}
        if isinstance(tier, Mapping):
            return tier
        # A HOUSE config object (the wiring round's ``Config.decision``):
        # ``global_enabled`` + the ``seams`` sub-model.
        try:
            from daemon.services.decision.config import seam_switch_tier

            rendered = seam_switch_tier(tier)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "decision switch yaml tier failed (non-fatal): %s: %s",
                type(exc).__name__,
                exc,
            )
            return {}
        return rendered

    # ── write ───────────────────────────────────────────────────────────

    async def write(
        self,
        seam_id: str,
        *,
        enabled: bool | None = None,
        stage: DecisionStage | None = None,
        global_enabled: bool | None = None,
    ) -> WriteResult:
        """Write the switches — WRITE → READ-BACK → VERIFY.

        A write failure or a read-back mismatch resolves the switch OFF
        and reports ``ok=False`` (the caller raises a
        ``switch_unreadable`` suppression).
        """
        pending: list[tuple[str, str]] = []
        if global_enabled is not None:
            pending.append(
                (GLOBAL_ENABLED_KEY, "1" if global_enabled else "0")
            )
        if enabled is not None:
            pending.append(
                (seam_enabled_key(seam_id), "1" if enabled else "0")
            )
        if stage is not None:
            pending.append((seam_stage_key(seam_id), stage.value))

        verified: dict[str, bool] = {}
        mismatched: list[str] = []
        error: str | None = None

        for key, value in pending:
            try:
                await self._write_value(key, value)
            except Exception as exc:  # noqa: BLE001 — the store never raises
                logger.warning(
                    "decision switch write failed for %s: %s: %s",
                    key,
                    type(exc).__name__,
                    exc,
                )
                error = f"write failed: {type(exc).__name__}"
                verified[key] = False
                mismatched.append(key)
                continue
            try:
                read_back = await self._store.get(key)
            except Exception as exc:  # noqa: BLE001
                read_back = None
                error = f"read-back failed: {type(exc).__name__}"
            if read_back is None or str(read_back) != value:
                logger.warning(
                    "decision switch read-back mismatch for %s (expected=%s got=%s)",
                    key,
                    value,
                    read_back,
                )
                mismatched.append(key)
                verified[key] = False
                if error is None:
                    error = "read-back mismatch"
            else:
                verified[key] = True

        # D2 — a write that did not land poisons the seam: the stored
        # value cannot be trusted, so subsequent reads resolve UNREADABLE
        # ⇒ OFF until an operator retries.
        if mismatched:
            if seam_id:
                self._poisoned.add(seam_id)
            if global_enabled is not None:
                self._poisoned.update(
                    {GLOBAL_ENABLED_KEY, "__global_poison__"}
                )
        # The write-path invalidation (L5): the next consult re-resolves.
        # The CACHE is dropped here; a poison set above SURVIVES.
        self._drop_cache(seam_id)
        if global_enabled is not None:
            self._drop_cache(None)
        return WriteResult(
            ok=not mismatched,
            verified=verified,
            mismatched=tuple(mismatched),
            error=error,
        )

    async def trip_shadow_off(
        self, seam_ids: Sequence[str], *, global_off: bool = True
    ) -> WriteResult:
        """The auto-trip write — BOTH keys per seam + the global master.

        "Shadow-off" means BOTH ``decision_seam_<id>_stage := 'shadow'``
        AND ``decision_seam_<id>_enabled := 0`` (FLAG2); the global
        master is driven OFF too when the ceiling is global.
        """
        results: list[WriteResult] = []
        if global_off:
            results.append(await self.write("", global_enabled=False))
        for seam_id in seam_ids:
            results.append(
                await self.write(
                    seam_id,
                    enabled=False,
                    stage=DecisionStage.SHADOW,
                )
            )
        self._drop_cache(None)
        ok = all(result.ok for result in results)
        verified: dict[str, bool] = {}
        mismatched: tuple[str, ...] = ()
        for result in results:
            verified.update(result.verified)
            mismatched = mismatched + result.mismatched
        return WriteResult(ok=ok, verified=verified, mismatched=mismatched)

    # ── boot ────────────────────────────────────────────────────────────

    async def boot_seed(self, seam_ids: Sequence[str]) -> list[SeedOutcome]:
        """Seed the env tier into the store — ONLY where no value exists.

        The DB tier wins the ladder, so a seeded value is written ONCE
        and a later env edit never overrides an operator's stored value.
        A kill is therefore NEVER undone by a restart.
        """
        outcomes: list[SeedOutcome] = []

        global_env = self._env.get(GLOBAL_ENABLED_ENV)
        if global_env is not None and str(global_env).strip():
            outcomes.append(
                await self._seed_one(
                    GLOBAL_ENABLED_KEY, str(global_env), "global master"
                )
            )

        for seam_id in seam_ids:
            env_name = seam_env_name(seam_id)
            enabled_env = self._env.get(env_name)
            if enabled_env is not None and str(enabled_env).strip():
                outcomes.append(
                    await self._seed_one(
                        seam_enabled_key(seam_id), str(enabled_env), "enabled"
                    )
                )
            stage_env = self._env.get(f"{env_name}_STAGE")
            if stage_env is not None and str(stage_env).strip():
                outcomes.append(
                    await self._seed_one(
                        seam_stage_key(seam_id), str(stage_env), "stage"
                    )
                )
        return outcomes

    async def _write_value(self, key: str, raw: str) -> None:
        """Write one switch value through the SANCTIONED path.

        With an ``override_writer`` bound (``SettingsService.set_override``)
        the write goes there — registry-gated, validated, operator-
        visible — and an audit line is emitted in the router's format
        (``set_override`` itself does not audit). Without one, the
        write goes straight to the store (the pre-wiring path).
        """
        writer = self._override_writer
        if writer is None:
            await self._store.set(key, raw)
            return
        typed = _typed_value(key, raw)
        from daemon.services.db_offload import offload

        await offload(writer, key, typed, None)
        logger.info(
            "[Settings] db override set key=%s value=%r subject=%s",
            key,
            typed,
            "auth-off",
        )

    async def _seed_one(
        self, key: str, raw: str, what: str
    ) -> SeedOutcome:
        """Seed one key — only when the store has NO existing value."""
        try:
            existing = await self._store.get(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "decision boot seed read failed for %s: %s: %s",
                key,
                type(exc).__name__,
                exc,
            )
            return SeedOutcome(key=key, seeded=False, reason="read failed")
        if existing is not None:
            return SeedOutcome(key=key, seeded=False, reason="existing value")
        if what == "enabled":
            parsed = _parse_enabled(raw)
            if parsed is None:
                return SeedOutcome(key=key, seeded=False, reason="unparseable")
            value = "1" if parsed else "0"
        elif what == "stage":
            text = raw.strip().lower()
            if text not in (DecisionStage.SHADOW.value, DecisionStage.DECIDING.value):
                return SeedOutcome(key=key, seeded=False, reason="unparseable")
            value = text
        else:
            value = raw.strip()
        try:
            await self._write_value(key, value)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "decision boot seed write failed for %s: %s: %s",
                key,
                type(exc).__name__,
                exc,
            )
            return SeedOutcome(key=key, seeded=False, reason="write failed")
        self._drop_cache(None)
        return SeedOutcome(key=key, seeded=True, reason="seeded")

    async def reverify(self, seam_ids: Sequence[str]) -> list[ReverifyOutcome]:
        """Re-verify every kill at boot — the BOOT RE-VERIFY ENTRY POINT.

        The wiring round calls this once at boot; it also drops the
        in-process cache (L5) so the first consult after the boot
        re-resolves from the store.

        Reads the STORE (the authoritative kill record) and classifies
        each switch: ``verified_off`` / ``verified_on`` / ``unreadable``.
        A kill is never undone by this pass — it only OBSERVES.
        """
        from daemon.services.decision import metrics

        self.invalidate(None)
        outcomes: list[ReverifyOutcome] = []
        for seam_id in ("global", *seam_ids):
            key = (
                GLOBAL_ENABLED_KEY
                if seam_id == "global"
                else seam_enabled_key(seam_id)
            )
            try:
                raw = await self._store.get(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "decision switch re-verify read failed for %s: %s: %s",
                    key,
                    type(exc).__name__,
                    exc,
                )
                outcomes.append(ReverifyOutcome(seam_id=seam_id, outcome="unreadable"))
                continue
            if raw is None:
                # No stored row ⇒ the default (OFF) — verified off.
                outcomes.append(ReverifyOutcome(seam_id=seam_id, outcome="verified_off"))
                continue
            parsed = _parse_enabled(str(raw))
            if parsed is None:
                outcomes.append(ReverifyOutcome(seam_id=seam_id, outcome="unreadable"))
            elif parsed:
                outcomes.append(ReverifyOutcome(seam_id=seam_id, outcome="verified_on"))
            else:
                outcomes.append(ReverifyOutcome(seam_id=seam_id, outcome="verified_off"))
        for outcome in outcomes:
            metrics.record_switch_reverify(outcome.seam_id, outcome.outcome)
        return outcomes


def _typed_value(key: str, raw: str) -> Any:
    """The registry-typed value for a store key.

    ``set_override`` validates against the managed key's declared type
    (``int`` for the ``_enabled`` keys, ``str`` for the ``_stage``
    key), so the value handed to it must carry that type — the store
    keeps the string form.
    """
    if key.endswith("_enabled"):
        return 1 if raw == "1" else 0
    return raw


def _parse_enabled(raw: str) -> bool | None:
    """Parse a boolean switch value; ``None`` means UNPARSEABLE."""
    text = raw.strip().lower()
    if text in _TRUE_TOKENS:
        return True
    if text in _FALSE_TOKENS:
        return False
    return None
