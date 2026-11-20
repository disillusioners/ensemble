#!/usr/bin/env python3
"""Tier B drift + timing benchmark (row2-2c) — runs from the repo root.

Methodology mirrors the deep-dive §2.1.4 / tester re-run (sonic-off-loop-audit
RESULTS): time the EXACT path (ENSEMBLE_TOKEN_COUNT_EXACT=1) and the CHUNKED
path (policy default) over the same corpora, and report drift% = (chunked-exact)/exact.

Corpora shapes: representative (prose / dense JSON / base64-ish) + pathological
(unbroken letters / symbols / whitespace / digits / CJK-letters).
Sizes: 4/16/64/200 KB. 200 KB unbroken exact = the known ~27 s leg (measured once).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.pop("ENSEMBLE_TOKEN_COUNT_EXACT", None)
os.environ.pop("ENSEMBLE_TOKEN_COUNT_THRESHOLD", None)
os.environ.pop("ENSEMBLE_TOKEN_COUNT_CHUNK", None)

from daemon.loader import estimate_tokens  # noqa: E402

SIZES = [4_096, 16_384, 65_536, 200_000]

SENT = ("The quick brown fox jumps over the lazy dog. "
        "Pack my box with five dozen liquor jugs. ")
JSON_TPL = '{"name":"agent_%d","status":"active","tokens":%d,"score":0.98,"meta":{"k":"v","n":%d}}'
B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def make_corpus(shape: str, n: int) -> str:
    if shape == "prose":
        s = SENT * (n // len(SENT) + 1)
        return s[:n]
    if shape == "json":
        out = []
        i = 0
        total = 0
        while total < n:
            piece = JSON_TPL % (i, i * 17, i)
            out.append(piece)
            total += len(piece) + 1
            i += 1
        return "[" + ",".join(out) + "]"[:n]
    if shape == "base64":
        return (B64_ALPHABET * (n // len(B64_ALPHABET) + 1))[:n]
    if shape == "letters":
        return "q" * n  # unbroken single-class letter run
    if shape == "symbols":
        return "!" * n  # unbroken single-class symbol run
    if shape == "whitespace":
        return " " * n  # unbroken whitespace run
    if shape == "newlines":
        return ("\n" * (n // 2 - 1)) + "x" + ("\n" * (n // 2))
    if shape == "digits":
        return "".join(str(i % 10) for i in range(n))  # digits break <= 3
    if shape == "cjk":
        return "漢" * n  # CJK letters = \p{L} run, 3 bytes/char
    raise ValueError(shape)


def timed(fn) -> tuple[int, float]:
    t0 = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t0


def main() -> None:
    # Optional filters (pack uses them to pin the commission shape set;
    # CJK excluded there — its exact reference costs ~135 s).
    shapes_filter = os.environ.get("TIER_B_SHAPES", "")
    sizes_filter = os.environ.get("TIER_B_SIZES", "")
    all_shapes = ["prose", "json", "base64", "letters", "symbols",
                  "whitespace", "newlines", "digits", "cjk"]
    shapes = ([s.strip() for s in shapes_filter.split(",") if s.strip()]
              if shapes_filter else all_shapes)
    sizes = ([int(s) for s in sizes_filter.split(",") if s.strip()]
             if sizes_filter else SIZES)
    rows = []
    for shape in shapes:
        for size in sizes:
            text = make_corpus(shape, size)
            # exact reference: kill-switch path
            os.environ["ENSEMBLE_TOKEN_COUNT_EXACT"] = "1"
            exact, t_exact = timed(lambda: estimate_tokens(text))
            # chunked: policy default
            os.environ.pop("ENSEMBLE_TOKEN_COUNT_EXACT", None)
            chunked, t_chunked = timed(lambda: estimate_tokens(text))
            drift_pct = (100.0 * (chunked - exact) / exact) if exact else 0.0
            rows.append({
                "shape": shape, "chars": len(text),
                "exact": exact, "exact_s": round(t_exact, 4),
                "chunked": chunked, "chunked_s": round(t_chunked, 4),
                "drift_pct": round(drift_pct, 4),
                "direction_ok": chunked >= exact,
                "speedup": round(t_exact / t_chunked, 1) if t_chunked else 0.0,
            })
            print(f"{shape:>10} {len(text):>7}c exact={exact:>7} ({t_exact:>8.4f}s) "
                  f"chunked={chunked:>7} ({t_chunked:>7.4f}s) "
                  f"drift={drift_pct:>7.4f}% dir_ok={chunked >= exact} "
                  f"x{rows[-1]['speedup']}", flush=True)

    # gate (d): 200 KB unbroken run timing at HEAD, default knobs
    text = make_corpus("letters", 200_000)
    _, t_head = timed(lambda: estimate_tokens(text))
    print(f"\nGATE-D 200KB-unbroken chunked timing: {t_head:.4f}s (target < 1s; "
          f"exact today 27.05s; C=4KB model predicts ~0.55s)", flush=True)

    out_path = os.environ.get(
        "TIER_B_RESULTS_PATH",
        f"/tmp/tier_b_drift_results_{os.getpid()}.json",
    )
    with open(out_path, "w") as f:
        json.dump(rows, f, indent=1)
    print(f"wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
