# M6: remove the SYNC_MINT provenance stamp
p = "daemon/services/child_reports.py"
s = open(p).read()
old = "                        deferred_reason=DEFERRED_REASON_SYNC_MINT,\n"
assert old in s
open(p, "w").write(s.replace(old, "", 1))
