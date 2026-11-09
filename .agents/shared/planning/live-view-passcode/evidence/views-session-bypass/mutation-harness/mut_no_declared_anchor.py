"""Mutation: remove ONLY the FIX-8 declared-base anchors (the loop
appending ``_declared_base_anchor(...)`` results). Legs (a)/(b)/(c)/(d)
and both declaration sources stay intact — the declaration still
licenses the forwarded headers, it just no longer contributes its own
host as an anchor.

If P1 (auth-OFF declared Origin), P2 (auth-ON service-source declared
Origin, leg-(d)-severed) and P4 (unit trust-set pins) genuinely pin the
declared anchor, they MUST fail here. Everything else should pass.
"""
import daemon.routers.live_views as lv


lv._declared_base_anchor = lambda _value: None
print("[mut_no_declared_anchor] FIX-8 declared-base anchors REMOVED "
      "(declaration boolean + legs a/b/c/d intact)")
