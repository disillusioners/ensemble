# M8: remove the root-terminal pre-check (BOTH layers) — the apparition path reopens
p = "daemon/services/report_delivery_recovery.py"
s = open(p).read()
old1 = """        # ── PART 3: the root-terminal pre-check (BEFORE reviving). ──
        _root_terminal, _root_id = self._ancestor_root_is_terminal(parent_id)
        if _root_terminal:
            logger.warning(
                f"sweep suppressed_root_terminal: recipient="
                f"{parent_id[:8]}... is terminal AND its tree root "
                f"{_root_id[:8] if _root_id else '?'}... is TERMINAL — "
                f"revival suppressed (the obligation cannot deliver "
                f"in-session; row held DEFERRED per D3; the 10:35 "
                f"apparition shape). Real message flow (T1 funnel-top "
                f"reactivation / send_message) is NOT affected — this "
                f"check lives at the RDRS revival seam only."
            )
            return False
        try:"""
new1 = """        try:"""
assert old1 in s
s = s.replace(old1, new1, 1)
old2 = """            # ── wi-1fbee2ec PART 3: pre-revival ROOT-TERMINAL check ──
            # (the D3 disposition surface: HOLD DEFERRED + structured
            # observability — a dedicated counter so operators can
            # distinguish root-terminal suppression from genuine
            # revival failure; NEVER a FAILED dead-letter).
            _root_terminal, _root_id = self._ancestor_root_is_terminal(
                parent_id
            )
            if _root_terminal:
                result.suppressed_root_terminal += 1
                logger.warning(
                    f"sweep orphan suppressed_root_terminal: "
                    f"recipient={parent_id[:8]}... is terminal and its "
                    f"tree root {_root_id[:8] if _root_id else '?'}... "
                    f"is TERMINAL — revival SUPPRESSED, row held "
                    f"DEFERRED (injection_id={row.injection_id[:8]}..., "
                    f"child={child_id[:8]}..., "
                    f"msg={child_msg_id[:8]}..., "
                    f"reason={row.deferred_reason}); the obligation "
                    f"cannot deliver in-session (the 10:35 apparition "
                    f"shape — real message flow via T1/send_message is "
                    f"unaffected)"
                )
                return
            revived = self._try_revive_terminal_parent(parent_id)"""
new2 = """            revived = self._try_revive_terminal_parent(parent_id)"""
assert old2 in s
open(p, "w").write(s.replace(old2, new2, 1))
