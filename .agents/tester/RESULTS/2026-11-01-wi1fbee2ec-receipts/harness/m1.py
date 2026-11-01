# M1: neutralize the obligation arm in decide() step 2 (the in-turn hold)
p = "daemon/services/attestation_gate.py"
s = open(p).read()
old = """        or outstanding_commissions > 0
        or outstanding_report_obligations > 0
    ):"""
new = """        or outstanding_commissions > 0
    ):"""
assert old in s
open(p, "w").write(s.replace(old, new))
