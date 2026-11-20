#!/usr/bin/env python3
"""Tier B drift + timing benchmark (row2-2C, fix pass: shape-aware cut).

Methodology mirrors the deep-dive §2.1.4 / tester re-run (sonic-off-loop-audit
RESULTS): time the EXACT path (ENSEMBLE_TOKEN_COUNT_EXACT=1) and the CHUNKED
path (policy default) over the same corpora, and report
drift% = (chunked - exact) / exact.

Shape set (commission row2-2c + fix-pass acceptance):
  SYNTHETIC   : lf_run, spaces_run, letters_run, symbols_run
  REALISTIC   : lf_code, crlf_code, lone_cr_code, markdown, prose, minified
  DENSE       : dense_json, base64, digits

GATE MODE (default ON): every shape above the chunking threshold must hold
  drift <= TIER_B_MAX_DRIFT_PCT (default 0.1%), the direction must hold
  (chunked >= exact, never smaller), and the 200 KB unbroken run must count
  in < TIER_B_MAX_GATE_D_S (default 1.0 s). A violation exits non-zero so the
  pack inherits a real gate.

Env filters: TIER_B_SHAPES (comma list), TIER_B_SIZES (comma list),
TIER_B_RESULTS_PATH (json output path).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.pop("ENSEMBLE_TOKEN_COUNT_EXACT", None)
os.environ.pop("ENSEMBLE_TOKEN_COUNT_THRESHOLD", None)
os.environ.pop("ENSEMBLE_TOKEN_COUNT_CHUNK", None)

from daemon.loader import (  # noqa: E402
    _TOKEN_CHUNK_SIZE_DEFAULT,
    _TOKEN_CHUNK_THRESHOLD_DEFAULT,
    estimate_tokens,
)

SIZES = [4_096, 16_384, 65_536, 200_000]

SENT = ("The quick brown fox jumps over the lazy dog. "
        "Pack my box with five dozen liquor jugs. ")
JSON_TPL = '{"name":"agent_%d","status":"active","tokens":%d,"score":0.98,"meta":{"k":"v","n":%d}}'
B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
CODE_LINE = "def handler_{n}(request, context):\n    payload = request.json()\n    return {{'ok': True, 'id': {n}, 'items': [1, 2, 3]}}\n"
MINIFIED = "function h(a,b){return a+b}var x=h(1,2),y=h(3,4);"

ALL_SHAPES = [
    "prose", "markdown", "minified", "dense_json", "base64", "digits",
    "lf_code", "crlf_code", "lone_cr_code",
    "lf_run", "spaces_run", "letters_run", "symbols_run",
]


def make_corpus(shape: str, n: int) -> str:
    if shape == "prose":
        s = SENT * (n // len(SENT) + 1)
        return s[:n]
    if shape == "markdown":
        s = ""
        i = 0
        while len(s) < n:
            s += (f"## Section {i}\n\n- item {i} with `code` and **bold**\n"
                  f"- item {i+1} with a [link](https://example.invalid/{i})\n\n"
                  "```python\ndef f():\n    return 1\n```\n\n")
            i += 1
        return s[:n]
    if shape == "minified":
        return (MINIFIED * (n // len(MINIFIED) + 1))[:n]
    if shape == "dense_json":
        out, i, total = [], 0, 0
        while total < n:
            piece = JSON_TPL % (i, i * 17, i)
            out.append(piece)
            total += len(piece) + 1
            i += 1
        return ("[" + ",".join(out) + "]")[:n]
    if shape == "base64":
        return (B64_ALPHABET * (n // len(B64_ALPHABET) + 1))[:n]
    if shape == "digits":
        return "".join(str(i % 10) for i in range(n))
    if shape in ("lf_code", "crlf_code", "lone_cr_code"):
        nl = {"lf_code": "\n", "crlf_code": "\r\n", "lone_cr_code": "\r"}[shape]
        body = CODE_LINE.replace("\n", nl)
        s, i = "", 0
        while len(s) < n:
            s += body.replace("{n}", str(i))
            i += 1
        return s[:n]
    if shape == "lf_run":
        return "\n" * n
    if shape == "spaces_run":
        return " " * n
    if shape == "letters_run":
        return "q" * n
    if shape == "symbols_run":
        return "!" * n
    raise ValueError(shape)


def timed(fn):
    t0 = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t0


def main() -> None:
    shapes_filter = os.environ.get("TIER_B_SHAPES", "")
    sizes_filter = os.environ.get("TIER_B_SIZES", "")
    shapes = ([s.strip() for s in shapes_filter.split(",") if s.strip()]
              if shapes_filter else ALL_SHAPES)
    sizes = ([int(s) for s in sizes_filter.split(",") if s.strip()]
             if sizes_filter else SIZES)
    max_drift_pct = float(os.environ.get("TIER_B_MAX_DRIFT_PCT", "0.1"))
    max_gate_d_s = float(os.environ.get("TIER_B_MAX_GATE_D_S", "1.0"))

    violations = []
    rows = []
    print(f"{'shape':>12} {'chars':>7} {'exact':>7} {'exact_s':>9} "
          f"{'chunked':>7} {'chunk_s':>8} {'drift%':>8} {'over':>5} {'bnd':>4}")
    for shape in shapes:
        for size in sizes:
            text = make_corpus(shape, size)
            os.environ["ENSEMBLE_TOKEN_COUNT_EXACT"] = "1"
            exact, t_exact = timed(lambda: estimate_tokens(text))
            os.environ.pop("ENSEMBLE_TOKEN_COUNT_EXACT", None)
            chunked, t_chunked = timed(lambda: estimate_tokens(text))
            drift_pct = (100.0 * (chunked - exact) / exact) if exact else 0.0
            boundaries = -(-len(text) // _TOKEN_CHUNK_SIZE_DEFAULT)
            over = chunked - exact
            row = {
                "shape": shape, "chars": len(text),
                "exact": exact, "exact_s": round(t_exact, 4),
                "chunked": chunked, "chunked_s": round(t_chunked, 4),
                "drift_pct": round(drift_pct, 4),
                "over_tokens": over, "boundaries": boundaries,
                "direction_ok": chunked >= exact,
                "above_threshold": len(text) > _TOKEN_CHUNK_THRESHOLD_DEFAULT,
                "speedup": round(t_exact / t_chunked, 1) if t_chunked else 0.0,
            }
            rows.append(row)
            print(f"{shape:>12} {len(text):>7} {exact:>7} {t_exact:>9.4f} "
                  f"{chunked:>7} {t_chunked:>8.4f} {drift_pct:>8.4f} "
                  f"{over:>5} {boundaries:>4}", flush=True)
            if chunked < exact:
                violations.append(f"{shape}@{size}: DIRECTION chunked<exact")
            if row["above_threshold"] and drift_pct > max_drift_pct:
                violations.append(
                    f"{shape}@{size}: drift {drift_pct:.4f}% > {max_drift_pct}%")
            if row["above_threshold"] and over > boundaries:
                violations.append(
                    f"{shape}@{size}: over-count {over} > n/CHUNK bound {boundaries}")

    # gate (d): 200 KB unbroken run timing at HEAD, default knobs
    text = make_corpus("letters_run", 200_000)
    _, t_head = timed(lambda: estimate_tokens(text))
    print(f"\nGATE-D 200KB-unbroken chunked timing: {t_head:.4f}s "
          f"(target < {max_gate_d_s}s; exact today 27.05s; C=4KB model ~0.55s)",
          flush=True)
    if t_head >= max_gate_d_s:
        violations.append(f"gate-d timing {t_head:.4f}s >= {max_gate_d_s}s")

    out_path = os.environ.get(
        "TIER_B_RESULTS_PATH",
        f"/tmp/tier_b_drift_results_{os.getpid()}.json",
    )
    with open(out_path, "w") as f:
        json.dump({"rows": rows, "gate_d_s": round(t_head, 4),
                   "violations": violations}, f, indent=1)
    print(f"wrote {out_path}", flush=True)

    if violations:
        print("\nRESULT: FAIL")
        for v in violations:
            print(f"  VIOLATION: {v}")
        sys.exit(1)
    print("\nRESULT: PASS")


if __name__ == "__main__":
    main()
