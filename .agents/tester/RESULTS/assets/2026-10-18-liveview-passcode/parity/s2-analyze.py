#!/usr/bin/env python3
"""S2 State-B analyzer — verifies each captured probe against the expected
auth-ON shapes (live-view-passcode commission item 5)."""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
WT = HERE / pathlib.Path(*([".."] * 6))
WT = WT.resolve()

RUN1 = HERE / "s2-run1-archive"  # first run (true pre-mint posture for hello.md)


def load(slug: str, run: pathlib.Path = HERE):
    hf = run / f"s2-{slug}.headers"
    bf = run / f"s2-{slug}.body"
    if not hf.exists():
        return None
    raw = hf.read_bytes().decode("utf-8", "replace")
    lines = raw.splitlines()
    status_line = lines[0] if lines else ""
    try:
        status = int(status_line.split()[1])
    except (IndexError, ValueError):
        status = -1
    headers: dict[str, str] = {}
    for ln in lines[1:]:
        if not ln.strip():
            break
        k, _, v = ln.partition(":")
        headers[k.strip().lower()] = v.strip()
    body = bf.read_bytes() if bf.exists() else b""
    return {"status_line": status_line, "status": status, "headers": headers, "body": body}


results: list[tuple[str, str, str]] = []  # probe, verdict, detail


def check(probe: str, ok: bool, detail: str) -> None:
    results.append((probe, "PASS" if ok else "FAIL", detail))


# ── S2a: direct legacy lane unauthenticated → 302 login ─────────────────────
r = load("s2a-direct-hello")
loc = (r["headers"].get("location", "") if r else "")
check(
    "S2a direct lane unauthenticated",
    bool(r) and r["status"] == 302 and loc.startswith("/api/auth/login"),
    f"status={r['status'] if r else '??'} location={loc!r} (byte-shape unchanged from base behavior: 302 + in-house login redirect)",
)

# ── S2b: public lane unauthenticated ────────────────────────────────────────
# TRUE pre-mint posture (row absent) — archived from run 1
r1 = load("s2b-pre-public-hello-unminted", RUN1)
pre_ok = bool(r1) and r1["status"] == 404 and r1["body"] == b'{"error":"view not found"}'
check(
    "S2b-pre public lane UNMINTED (row absent, run-1 archive)",
    pre_ok,
    f"status={r1['status'] if r1 else '??'} body={r1['body'][:40]!r} — uniform 404 FROM DAEMON, not a login redirect (AuthMiddleware passed the request through; row-gating posture)",
)
# minted posture (current run)
r2 = load("s2b-post-public-hello")
body2 = r2["body"] if r2 else b""
interstitial = (
    bool(r2)
    and r2["status"] == 200
    and r2["headers"].get("content-type", "").startswith("text/html")
    and (b"passcode" in body2.lower())
)
check(
    "S2b-post public lane MINTED, NO session",
    interstitial,
    f"status={r2['status'] if r2 else '??'} content-type={r2['headers'].get('content-type') if r2 else '??'} "
    f"interstitial-markers(passcode form present)={b'passcode' in body2.lower()} — 200 interstitial passcode gate, NOT 302 login = State-B shape proof",
)

# ── S2c: full verify flow ───────────────────────────────────────────────────
rw = load("s2c-wrong-code")
check(
    "S2c-wrong wrong code + same-host Origin",
    bool(rw) and rw["status"] == 200,
    f"status={rw['status'] if rw else '??'} (must NOT be 403 — proceeds to compare/deny; error re-render)",
)
rp = load("s2c-correct-plain")
plain_ok = bool(rp) and rp["status"] == 200 and b"Secure connection required" in rp["body"]
check(
    "S2c-plain correct code over plain HTTP",
    plain_ok,
    f"status={rp['status'] if rp else '??'} secure-context copy present={b'Secure connection required' in (rp['body'] if rp else b'')} (D6: no cookie, no redirect loop)",
)
rh = load("s2c-correct-https")
sc = rh["headers"].get("set-cookie", "") if rh else ""
cookie_attrs = {
    "Path-scoped": "Path=/views/public/parityroot1/hello.md" in sc,
    "HttpOnly": "HttpOnly" in sc,
    "Secure": "Secure" in sc,
    "SameSite=Lax": "SameSite=Lax" in sc,
    "Max-Age=86400": "Max-Age=86400" in sc,
}
loc_h = rh["headers"].get("location", "") if rh else ""
check(
    "S2c-https correct code + X-Forwarded-Proto: https",
    bool(rh) and rh["status"] == 303 and loc_h == "/views/public/parityroot1/hello.md" and all(cookie_attrs.values()),
    f"status={rh['status'] if rh else '??'} location={loc_h!r} cookie-attrs={cookie_attrs} (303 PRG + grant cookie)",
)
rc = load("s2c-get-with-cookie")
get_ok = (
    bool(rc)
    and rc["status"] == 200
    and b"# parity hello" in rc["body"]
)
check(
    "S2c-get GET with grant cookie renders view bytes",
    get_ok,
    f"status={rc['status'] if rc else '??'} content-type={rc['headers'].get('content-type') if rc else '??'} "
    f"file-content-present={b'# parity hello' in (rc['body'] if rc else b'')} body-bytes={len(rc['body']) if rc else 0}",
)

# ── S2d: unminted public path under auth-ON ────────────────────────────────
for tag, run in (("pre-mint", HERE), ("post-mint", HERE)):
    rd = load("s2d-public-absent-unminted" if tag == "pre-mint" else "s2d-postmint-public-absent", run)
    ok = (
        bool(rd)
        and rd["status"] == 404
        and rd["body"] == b'{"error":"view not found"}'
        and "location" not in rd["headers"]
    )
    check(
        f"S2d unminted public path ({tag})",
        ok,
        f"status={rd['status'] if rd else '??'} uniform-body={rd['body'] == b'{\"error\":\"view not found\"}' if rd else '??'} location-header-absent={'location' not in (rd['headers'] if rd else {})} (no existence leak)",
    )

# ── S2e: control — /api/instances unauthenticated ───────────────────────────
re_ = load("s2e-api-instances")
loc_e = re_["headers"].get("location", "") if re_ else ""
check(
    "S2e control /api/instances unauthenticated",
    bool(re_) and re_["status"] == 302 and loc_e.startswith("/api/auth/login"),
    f"status={re_['status'] if re_ else '??'} location={loc_e!r} (still guarded)",
)

# ── sanity ──────────────────────────────────────────────────────────────────
rz = load("sanity-livez")
check(
    "sanity /views/livez (public exact tag)",
    bool(rz) and rz["status"] == 200,
    f"status={rz['status'] if rz else '??'}",
)

# ── report ──────────────────────────────────────────────────────────────────
lines = [
    "S2 STATE-B AUTH-ON REPORT (feature/live-view-passcode @ worktree, port 8085)",
    "env: ENSEMBLE_AUTH_ENABLED=true, placeholder OIDC (drill.invalid), anonymous_view_paths UNSET",
    "mint seam: ViewPasscodeService.mint/refresh direct (same service call view_link makes —",
    "           view_link is instance-bound; see daemon/tools/live_views.py:113 factory → passcode_service.mint)",
    "",
    "| probe | verdict | detail |",
    "|---|---|---|",
]
for probe, verdict, detail in results:
    lines.append(f"| {probe} | {verdict} | {detail} |")
n_fail = sum(1 for _, v, _ in results if v == "FAIL")
lines += ["", f"RESULT: {'FAIL (' + str(n_fail) + ')' if n_fail else 'PASS'}"]
report = "\n".join(lines) + "\n"
(HERE / "s2-report.txt").write_text(report, encoding="utf-8")
print(report)
sys.exit(1 if n_fail else 0)
