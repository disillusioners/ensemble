# M7: disable the detector classification (never fires)
p = "daemon/services/report_delivery_recovery.py"
s = open(p).read()
old = """        first_parent = True
        chain_depth = 0"""
new = """        return (None, {"reason": "mutation-disabled"})
        first_parent = True
        chain_depth = 0"""
assert old in s
open(p, "w").write(s.replace(old, new, 1))
