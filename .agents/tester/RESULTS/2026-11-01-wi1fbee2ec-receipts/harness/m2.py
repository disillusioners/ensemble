# M2: neutralize emission leg 6 (count forced to 0)
p = "daemon/services/child_reports.py"
s = open(p).read()
old = "            if _obligation_count > 0:"
new = "            if False and _obligation_count > 0:"
assert old in s
open(p, "w").write(s.replace(old, new, 1))
