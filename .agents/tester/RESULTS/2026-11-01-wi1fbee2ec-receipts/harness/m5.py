# M5: revert the liveness widening — dormant NOT counted (the dogfood window reopens)
p = "daemon/manager.py"
s = open(p).read()
old = """                live_count += 1
            else:
                # Unknown status — preserve the historical default"""
new = """                pass
            else:
                # Unknown status — preserve the historical default"""
assert old in s
open(p, "w").write(s.replace(old, new, 1))
