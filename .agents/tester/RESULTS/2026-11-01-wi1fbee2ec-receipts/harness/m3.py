# M3: D1 in-turn isolation removed — the read error rides the outer fail-open
p = "daemon/services/attestation_gate.py"
s = open(p).read()
old = """                try:
                    _obligation_value = _obligation_reader(instance_id)
                except Exception:  # noqa: BLE001 — D1: isolated error path, NEVER the whole-eval fail-open
                    report_obligation_read_error = True
                else:"""
new = """                if True:
                    _obligation_value = _obligation_reader(instance_id)
                else:"""
assert old in s
open(p, "w").write(s.replace(old, new))
