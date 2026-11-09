"""Mutation: restore UNCONSTRAINED forwarded trust (pre-FIX-10).

Generated from the pre-FIX-10 blob at %s — the exact
``_views_trusted_hosts`` that shipped in that commit: the
forwarded-derived values (first X-Forwarded-Host value, first
RFC 7239 Forwarded host= element) ANCHOR UNCONDITIONALLY inside
the declared-front block, and there is NO agreement veto.

K1/K2/K4 MUST fail under this mutant.
"""
import daemon.routers.live_views as lv


def _views_trusted_hosts(request: Request) -> list[tuple[str, int | None]]:
    """Derive the SERVER-SIDE trusted-host universe for the views gate.

    Legs (R2, views-session-bypass 2026-11-08; FIX-1/FIX-7/FIX-8
    hardening; G2.7/FIX-9 host-vouching — every entry is a
    server-derivable host, never a client-invented allowlist entry):

    (a) the request ``Host`` header — CONDITIONAL since G2.7 (FIX-9,
        2026-11-09): an anchor ONLY when the HOST NAME is
        operator-vouched — audience membership, a declared-base
        host, or the loopback carve. The raw Host header is
        attacker-controlled on a DNS-rebinding victim: without this
        gate, ``Host: evil.example`` + ``Origin:
        https://evil.example`` self-matched and minted a FULL view
        grant. This mirrors the auth lane's ``host_is_trusted``
        hardening (``daemon/auth/cookie.py:249`` comparator
        ``:232-329``, derived at
        ``daemon/middleware/auth.py:535-537``) that the views gate
        never carried over. Vouch check = exact lowercased name
        membership (mirrors the auth lane; the ORIGIN comparison
        keeps the www/port folding — a vouched-name anchor
        participates in the same comparisons as every other
        anchor);
    (b) the FIRST ``X-Forwarded-Host`` value — the fronting-proxy
        anchor — ONLY when the operator declared a proxy front;
    (c) the RFC 7239 ``Forwarded`` ``host=`` parameter (first element)
        — the standardized spelling of (b) — under the SAME
        operator-declared-proxy-front gate;
    (d) the operator-audience trusted-host set —
        :func:`daemon.auth.cookie.parse_operator_audience` over
        ``auth.allowed_origins`` + ``auth.external_redirect_base`` +
        the one-shot ``live_view_external_base_url_fallback`` copy.
        Portless by construction (bare hosts) — port-compatible with
        any origin port for the same host spelling;
    (e) the DECLARED base's own host (FIX-8) — the auth-fallback
        and/or ``live_views.external_base_url`` hosts;
    (f) the loopback carve (G2.7/FIX-9) — the SOUND-BY-CONSTRUCTION
        fallback the commission prescribed: the Host name is an
        EXACT loopback spelling AND the TCP peer is loopback (a
        remote rebinding victim can never connect FROM loopback).
        The auth lane's carve additionally requires the dev gate
        (``allow_insecure_cookie``); that gate does NOT port to the
        views lane — the lane must serve unconfigured dev access
        with auth OFF (no AuthConfig to read the knob from) — so
        the peer gate carries the soundness alone, exactly as the
        commission's fallback specifies.

    FIX-1: legs (b)/(c) are CLIENT headers gated on the operator
    having DECLARED a proxy front (``auth.live_view_external_base_url_fallback``
    set — the lifespan one-shot copy of ``live_views.external_base_url``,
    env ``ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL`` — the SAME accessor
    :func:`parse_operator_audience` uses). This is the C-2c condition
    the auth lane applies to ``X-Forwarded-Proto`` trust.

    FIX-7: the second declaration source — ``live_views.external_base_url``
    set (read through
    :attr:`LiveViewsService.declared_external_base_url`, the raw
    tier-1 override; the resolved chain is request-derived and never
    counts). The auth fallback's sole writer sits inside
    ``if config.auth.enabled`` (daemon/api.py:2937); without this
    source an auth-OFF deployment behind a Host-rewriting proxy
    could never declare a front.

    FIX-8: the declared base's own host joins the anchor set — the
    operator's declared origin must be accepted without a forwarded
    header, and the set must be self-sufficient where the audience
    leg is absent (auth-OFF has no auth config to absorb through).

    DEGRADATION CONTRACT (G2.7/FIX-9): with NO vouching configured
    (no audience, no declared base) and a non-loopback peer, a
    same-origin POST carrying a REAL Origin now REJECTS — the
    intended strict-contract degradation (cure: the operator
    declares a base or audience). Absent-Origin and null-Origin
    flows are UNCHANGED (both allow before the anchor set is
    consulted), and loopback peers keep working via the carve.

    A request may carry several of these at once (proxy chains); the
    gate accepts if the Origin host matches ANY of them.
    """
    trusted: list[tuple[str, int | None]] = []
    headers = request.headers
    auth_config = getattr(request.app.state, "auth_config", None)

    # ── the operator-vouch universe (computed FIRST — the Host
    # anchor's vouch check needs it; G2.7/FIX-9) ──────────────────
    declared_bases: list[str] = []
    if auth_config is not None:
        external_base = getattr(
            auth_config,
            "live_view_external_base_url_fallback",
            None,
        )
        if external_base:
            declared_bases.append(external_base)
    # FIX-7 (fix-iteration 2, 2026-11-08): SECOND operator-
    # declaration source — ``live_views.external_base_url`` set.
    # The auth fallback above has exactly ONE writer
    # (daemon/api.py:2937) inside ``if config.auth.enabled:``, so
    # an auth-OFF deployment behind a Host-rewriting proxy could
    # never declare a front and the forwarded legs were dead
    # code in that posture (legit forwarded POSTs 403'd). This
    # source is OPERATOR CONFIG ONLY — read through the service's
    # DECLARATION accessor (``declared_external_base_url``: the
    # raw tier-1 override). The resolved chain
    # (``public_base()``: host-capture / bind tiers) is
    # request-derived and is deliberately NOT consulted — using
    # it would let client-shaped evidence declare a front and
    # re-open the self-anchoring class FIX-1 closed. Service
    # unwired / property absent / value unset ⇒ STILL
    # undeclared.
    live_views_service = getattr(
        request.app.state, "live_views_service", None
    )
    if live_views_service is not None:
        declared_base = getattr(
            live_views_service,
            "declared_external_base_url",
            None,
        )
        if declared_base:
            declared_bases.append(declared_base)
    proxy_front_declared = bool(declared_bases)

    audience_hosts: frozenset[str] = frozenset()
    if auth_config is not None:
        from daemon.auth.cookie import parse_operator_audience

        try:
            audience_hosts, _audience_origins = parse_operator_audience(
                auth_config
            )
        except Exception:  # noqa: BLE001 — a malformed audience is inert
            audience_hosts = frozenset()

    # G2.7/FIX-9 vouch names: the audience names ∪ the declared-base
    # hosts. Exact lowercased NAME membership only (auth-lane mirror).
    vouched_names: set[str] = set(audience_hosts)
    declared_anchors: list[tuple[str, int | None]] = []
    for declared_base_value in declared_bases:
        declared_anchor = _declared_base_anchor(declared_base_value)
        if declared_anchor is not None:
            declared_anchors.append(declared_anchor)
            vouched_names.add(declared_anchor[0])

    # Loopback carve — peer-gated (sound by construction: a remote
    # rebinding victim never connects FROM loopback) + exact-name
    # gated on the HOST side below.
    from daemon.auth.cookie import peer_is_loopback

    peer_loopback = peer_is_loopback(request.scope.get("client"))

    # ── leg (a): the Host header — CONDITIONALLY anchored
    # (G2.7/FIX-9). A vouched name only. ──────────────────────────
    host_header = headers.get("host")
    if host_header:
        parsed = _authority_host_port(host_header)
        if parsed is not None:
            host_name = parsed[0]
            host_vouched = host_name in vouched_names or (
                peer_loopback and _views_exact_loopback_name(host_name)
            )
            if host_vouched:
                trusted.append(parsed)

    if proxy_front_declared:
        # FIX-8 (fix-iteration 3, 2026-11-08): the DECLARED base's
        # own host joins the anchor set — the operator's declared
        # origin (``Origin: https://<declared-host>``, no forwarded
        # header) must be accepted, and the anchor set must be
        # self-sufficient where the audience leg is absent (auth-OFF
        # has no auth config for ``parse_operator_audience`` to
        # absorb the base through). Each declared value parses via
        # :func:`_declared_base_anchor`; unparseable declarations
        # contribute nothing (FIX-3 fail-closed discipline). This is
        # an ADDITION to legs (a)/(d), never a replacement — legs
        # (b)/(c) stay boolean-licensed by the declaration (the
        # reviewed adjudication: the forwarded-original-host
        # topology the R2 ruling asked to honor; C-2c house
        # precedent shape).
        for declared_anchor in declared_anchors:
            trusted.append(declared_anchor)
        xfh = headers.get("x-forwarded-host")
        if xfh:
            first_value = xfh.split(",")[0].strip()
            if first_value:
                parsed = _authority_host_port(first_value)
                if parsed is not None:
                    trusted.append(parsed)

        forwarded = headers.get("forwarded")
        if forwarded:
            # RFC 7239 first element only (the element closest to the
            # client-facing edge); ``host=`` is case-insensitive and its
            # value may be quoted.
            first_element = forwarded.split(",")[0]
            for piece in first_element.split(";"):
                piece = piece.strip()
                if piece.lower().startswith("host="):
                    raw_value = piece[len("host="):].strip()
                    if (
                        len(raw_value) >= 2
                        and raw_value.startswith('"')
                        and raw_value.endswith('"')
                    ):
                        raw_value = raw_value[1:-1]
                    parsed = _authority_host_port(raw_value)
                    if parsed is not None:
                        trusted.append(parsed)
                    break

    # ── leg (d): the operator audience hosts (parse hoisted above —
    # G2.7/FIX-9 needs it for the Host-vouch check before this
    # point; the append behavior is unchanged). ────────────────────
    for audience_host in sorted(audience_hosts):
        normalized = _authority_host_port(audience_host)
        if normalized is not None:
            trusted.append(normalized)

    return trusted


lv._views_trusted_hosts = _views_trusted_hosts
print("[mut_fwd_unconstrained] pre-FIX-10 forwarded trust RESTORED")
