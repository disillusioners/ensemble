#!/usr/bin/env bash
# tests/packs/preflight_base_scale_measure.sh
#
# ONE-OFF BOUNDED MEASUREMENT — NOT PART OF THE GATE. Never run this in CI.
#
# LEADER BOUNDED PROTOCOL (wave-2 dispatch, 2026-10-30): the k·N² cost model
# of the BASE O(N²) `journal_read` walk is established; this script
#
#   1. runs the legacy walk on SAMPLES in the 55–222 KB band, each under a
#      HARD `timeout -k` wrapper (never unbounded — the prior executor's
#      28-minute foreground run was killed and its evidence lost);
#   2. records USER CPU TIME (`/usr/bin/time -p`) as the ROBUST metric —
#      two unrelated CPU spinners burn ~2 cores on this host, so wall-clock
#      is advisory only and is recorded but never used for the fit;
#   3. fits k from the samples (T_user = k · N²) and EXTRAPOLATES to the
#      incident size 455,783 B, LABELLED EXTRAPOLATED.
#
# The 455 KB legacy walk itself is NEVER run here (predicted ~45–61 min CPU).
# Every leg emits a timestamped log line as it starts and finishes, so the
# log can be polled for progress while the script runs in the background.
#
# Usage:  nohup bash tests/packs/preflight_base_scale_measure.sh <out-log> &

set -u
PF_PACK_NAME="preflight-base-scale-measure"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

pf_fence_init
trap pf_fence_cleanup EXIT

ORACLE="$PF_SB/oracle.sh"
pf_base_oracle "$ORACLE"
INC="$PF_SB/incident.json"
pf_incident_fixture "$INC"

# Sample sizes (bytes). The wave-2 protocol band is 111–222 KB; the 55 KB
# anchor is included so the quadratic SHAPE is demonstrated on three points,
# not asserted from two.
SAMPLES="56640 111072 221184"
# Hard never-exceed wrapper per leg (s). The 222 KB leg is predicted ~800 s
# CPU from the established model (111 KB -> ~200 s); 1500 s is the safety
# net, not the expectation. A leg that hits the wrapper is recorded as
# BOUNDED-OUT and still yields a LOWER BOUND for k (T >= elapsed).
HARD_TIMEOUT_S=1500

say() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

say "oracle: BASE $PF_BASE_REV (verbatim journal_read from git objects)"
say "fixture: $INC ($(pf_bytes "$INC") bytes, sha256 $(pf_sha256 "$INC"))"
say "host: $(uname -sr) · bash $BASH_VERSION · python3 $(/usr/bin/python3 -V 2>&1)"
say "ambient-load: two unrelated CPU spinners (~2 cores) were running on this"
say "host during the measurement — USER CPU is the fit metric; wall is advisory"
say "protocol: samples [$SAMPLES] under hard timeout ${HARD_TIMEOUT_S}s; fit k; extrapolate 455783 B (LABELLED)"

mk_pad() { # mk_pad <path> <bytes> — a VALID padded journal of exactly N bytes
    /usr/bin/python3 - "$1" "$2" <<'PY'
import sys
dst, n = sys.argv[1], int(sys.argv[2])
seed = b'{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-01-01T00:00:00Z","event":"pad","detail":"'
tail = b'"}]}'
fill = n - len(seed) - len(tail)
if fill < 0:
    raise SystemExit("n too small")
open(dst, "wb").write(seed + b"x" * fill + tail)
PY
}

# ── the sample legs ─────────────────────────────────────────────────────────
for n in $SAMPLES; do
    f="$PF_SB/n$n.json"
    mk_pad "$f" "$n"
    say "LEG n=$n START (hard cap ${HARD_TIMEOUT_S}s)"
    t0="$(pf_now_ms)"
    /usr/bin/time -p -o "$PF_SB/n$n.time" \
        timeout -k 10 "$HARD_TIMEOUT_S" "$ORACLE" verify "$f" > "$PF_SB/n$n.out" 2> "$PF_SB/n$n.err"
    rc=$?
    t1="$(pf_now_ms)"
    wall_ms=$(( t1 - t0 ))
    tu="$(sed -n 's/^user //p' "$PF_SB/n$n.time" 2>/dev/null | tr -d ' ')"
    ts="$(sed -n 's/^sys //p'  "$PF_SB/n$n.time" 2>/dev/null | tr -d ' ')"
    tr="$(sed -n 's/^real //p' "$PF_SB/n$n.time" 2>/dev/null | tr -d ' ')"
    say "LEG n=$n DONE rc=$rc wall_ms=$wall_ms time(real=${tr}s user=${tu}s sys=${ts}s) outbytes=$(pf_bytes "$PF_SB/n$n.out")"
    [ "$rc" = "0" ] || say "LEG n=$n note: rc=$rc (124/137 = the hard wrapper fired — sample recorded as a LOWER BOUND)"
done

# ── the fit + the extrapolation ─────────────────────────────────────────────
say "FIT starting (T_user = k·N²; least squares through the origin over the completed legs)"
/usr/bin/python3 - "$PF_SB" $SAMPLES <<'PY'
import os, sys, re
sb = sys.argv[1]
samples = [int(x) for x in sys.argv[2:]]
INCIDENT = 455783
rows = []
for n in samples:
    tpath = os.path.join(sb, "n%d.time" % n)
    if not os.path.exists(tpath):
        continue
    txt = open(tpath).read()
    m = re.search(r"^user (\S+)", txt, re.M)
    if not m:
        continue
    user_s = float(m.group(1))
    rows.append((n, user_s))
if not rows:
    print("FIT-ERROR: no completed sample legs")
    sys.exit(1)
# least squares through the origin: k = Σ(T·N²)/Σ(N⁴)
num = sum(t * (n * n) for n, t in rows)
den = sum(n ** 4 for n, t in rows)
k = num / den
print("FIT points (bytes -> user-CPU s): " + ", ".join("%d -> %.2f" % r for r in rows))
for n, t in rows:
    print("FIT k(n=%d) = %.3e s/B²" % (n, t / (n * n)))
print("FIT k (LSQ through origin) = %.3e s/B²" % k)
pred = k * (INCIDENT ** 2)
print("EXTRAPOLATED T_user(455783 B) = %.0f s = %.1f min  [LABEL: EXTRAPOLATED — never run]"
      % (pred, pred / 60.0))
print("NOTE: wall-clock on this host is advisory (ambient CPU spinners); the fit uses USER CPU only")
PY
say "MEASUREMENT COMPLETE — every number above 455783 B is EXTRAPOLATED, never measured"
