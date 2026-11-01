# M4 (rewritten per review cycle 1, item 2): delete the emission leg-6
# try/except wrapper ENTIRELY — the raise must propagate to the
# caller-wrap fail-open (:5426-5451 / FP4-L5). The previous script's
# `if True:` + orphaned `except:` was a SyntaxError at collection =
# trivially RED, not a behavioral RED.
p = "daemon/services/child_reports.py"
s = open(p).read()
old = """            try:
                _obligation_value = (
                    self._manager.count_outstanding_report_obligations(
                        instance_id
                    )
                )
            except Exception as _obl_exc:  # noqa: BLE001 — D1 fail-closed (isolated from every fail-open catch)
                logger.warning(
                    "root_completion_gate: obligation leg read raised "
                    "for %s... (%s: %s); DENY per D1 fail-closed "
                    "(hold; never rides the caller-wrap fail-open)",
                    instance_id[:8],
                    type(_obl_exc).__name__,
                    _obl_exc,
                )
                return (
                    False,
                    "outstanding_report_obligations=unknown(read_error)",
                    False,
                )
            if isinstance(_obligation_value, int) and _obligation_value >= 0:"""
new = """            _obligation_value = (
                self._manager.count_outstanding_report_obligations(
                    instance_id
                )
            )
            if isinstance(_obligation_value, int) and _obligation_value >= 0:"""
assert old in s, "M4 target text not found"
open(p, "w").write(s.replace(old, new, 1))
