"""wi-1fbee2ec — report-obligation visibility IN-TURN gate pins.

Pins (commission §PINS, plan §10) covered here:

* **(a) repro — undelivered report + quiet tree** at the IN-TURN
  checkpoint (``decide()`` + ``evaluate()`` levels): the 10:35 shape —
  a terminal child with an outstanding undelivered report obligation,
  all liveness arms REAL zero, zero watchers. OLD: denied ×3 → the 4th
  evaluation trips ``deny_bound_exceeded`` → ``TERMINAL_AFTER_BOUND``
  closes the root. NEW: the obligation arm reads > 0 → the hold
  PERSISTS past what would have been the 4th evaluation (bound-exempt
  per the leader adjudication 2026-11-01).
* **(e) dogfood dispatch-window** at the IN-TURN checkpoint: child
  minted, NOT yet RUNNING (dormant IDLE), tree otherwise quiet → OLD
  verify-clean, NEW must NOT verify-clean (the widened
  ``live_descendants`` input set).
* **D1 error-isolation**: the obligation read's failure converts to a
  bound-composed DENY via ``report_obligation_read_error`` — isolated
  from the whole-eval fail-open-ALLOW catch and from the −1 sentinel
  short-circuit.

Mutation legs (planted-defect RED → restore GREEN) are executed at the
verification phase via source-level mutations; the assertions below are
the GREEN-side contracts the mutations must break.
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    decide,
    evaluate,
)


def real(content: str = "ship the feature") -> HumanMessage:
    return HumanMessage(content=content)


def delegating_ai() -> AIMessage:
    """An AIMessage carrying a delegation tool_call (send_message — a
    DEFAULT_DELEGATION_TOOL_NAMES member ⇒ the gate is ON,
    ``attestation_required`` computes True)."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "send_message",
                "args": {
                    "instance_id": "child-1",
                    "message": "do it",
                },
                "id": "call-1",
            }
        ],
    )


def plain_ai(content: str = "All done.") -> AIMessage:
    return AIMessage(content=content)


def make_manager(
    pending=0,
    wakeups=0,
    live_descendants=0,
    obligations=0,
    obligation_error=False,
):
    m = MagicMock()
    m.count_pending_children = MagicMock(return_value=pending)
    m.get_queued_or_expected_wakeups = MagicMock(return_value=wakeups)
    m.count_live_descendants = MagicMock(return_value=live_descendants)
    m.count_outstanding_commissions = MagicMock(return_value=0)
    if obligation_error:
        def _raise(_iid):
            raise RuntimeError("obligation read exploded")
        m.count_outstanding_report_obligations = MagicMock(
            side_effect=_raise
        )
    else:
        m.count_outstanding_report_obligations = MagicMock(
            return_value=obligations
        )
    m.enqueue_message = MagicMock()
    m.revive = MagicMock()
    m.send_message = MagicMock()
    return m


ENFORCE = GateSettings("enforce", 3, 3)


# ─────────────────────────────────────────────────────────────────────────────
# (a) REPRO — the 10:35 shape at the in-turn checkpoint
# ─────────────────────────────────────────────────────────────────────────────


class TestReproQuietTreeObligationHold:
    """The mutation shape: undelivered report + quiet tree.

    OLD closes at bound; NEW HOLDS. R2 pin: the gate holds AT/PAST the
    bound while an obligation is outstanding (bound-exempt hold).
    """

    def test_obligation_outstanding_holds_below_bound(self):
        d = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=0,
            bound=3,
            attestation_required=True,
            outstanding_report_obligations=1,
        )
        assert d.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        # Bound-exempt: counter UNCHANGED (no denied_count advance).
        assert d.next_denied_count == 0
        assert not d.should_inject_nudge

    def test_obligation_outstanding_holds_at_bound_the_1035_repro(self):
        """THE pin-(a) core: denied_count == 3 == bound with an
        outstanding obligation → the hold PERSISTS; the 4th evaluation
        does NOT trip TERMINAL_AFTER_BOUND (OLD shape closed here)."""
        d = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=3,
            bound=3,
            attestation_required=True,
            outstanding_report_obligations=1,
        )
        assert d.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert d.next_denied_count == 3  # unchanged
        assert not d.should_inject_nudge

    def test_obligation_clear_at_bound_still_closes_old_shape(self):
        """The counterfactual (the mutation leg's RED target): with the
        obligation input GONE (mutation: arm neutralized), the same
        quiet tree at the bound closes terminal_after_bound — proving
        the OLD behavior the pin guards against."""
        d = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=3,
            bound=3,
            attestation_required=True,
            outstanding_report_obligations=0,
        )
        assert d.decision is Decision.TERMINAL_AFTER_BOUND

    def test_obligation_holds_even_when_attested(self):
        """Fires BEFORE the attested split (the R2 family ordering): a
        premature attest during an outstanding obligation is an allow-
        hold, not an attested-allow reset."""
        d = decide(
            attested=True,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=0,
            bound=3,
            attestation_required=True,
            final_ai_is_text_report=True,
            outstanding_report_obligations=2,
        )
        assert d.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert d.next_denied_count == 0

    def test_evaluate_level_obligation_arm_drives_allow_hold(self):
        """The composition glue: the R2 read feeds the arm end-to-end
        (manager facade → evaluate → decide)."""
        msgs = [real("run the mission"), delegating_ai(), plain_ai()]
        result = evaluate(
            "inst-repro",
            3,
            msgs,
            ENFORCE,
            make_manager(obligations=1),
        )
        assert (
            result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        )
        assert result.outstanding_report_obligations == 1
        assert not result.report_obligation_read_error
        assert result.next_denied_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# (e) DOGFOOD dispatch-window — the in-turn liveness leg
# ─────────────────────────────────────────────────────────────────────────────


class TestDogfoodDispatchWindowLiveness:
    """Leader adjudication 2026-11-01: a minted-not-yet-RUNNING (dormant
    IDLE/QUEUED) descendant must read NON-QUIET to the in-turn gate —
    the dispatch→RUNNING window can never verify-clean.

    At the decide() level the widened input set manifests as
    ``live_descendants > 0`` from a dormant child (the WIDENING is the
    manager-side walk — pinned with a real engine in the emission/
    liveness pack); here we pin the gate-side contract: > 0 → allow-
    hold (NOT a verify-clean deny path).
    """

    def test_minted_dormant_child_holds_at_bound_not_verify_clean(self):
        d = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=1,  # the minted IDLE child post-widening
            denied_count=3,
            bound=3,
            attestation_required=True,
        )
        assert d.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert d.next_denied_count == 3

    def test_counterfactual_zero_liveness_at_bound_closes(self):
        """Mutation leg RED target: with the widened input removed
        (live_descendants back to 0 for the dormant child), the OLD
        verify-clean closure recurs."""
        d = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=3,
            bound=3,
            attestation_required=True,
        )
        assert d.decision is Decision.TERMINAL_AFTER_BOUND

    def test_obligation_and_liveness_are_orthogonal_legs(self):
        """Adjudication #4: two different windows, two different legs —
        either alone holds the gate."""
        d1 = decide(
            attested=False, pending_children=0,
            queued_or_expected_wakeups=0, live_descendants=1,
            denied_count=3, bound=3, attestation_required=True,
            outstanding_report_obligations=0,
        )
        d2 = decide(
            attested=False, pending_children=0,
            queued_or_expected_wakeups=0, live_descendants=0,
            denied_count=3, bound=3, attestation_required=True,
            outstanding_report_obligations=1,
        )
        assert d1.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert d2.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP


# ─────────────────────────────────────────────────────────────────────────────
# D1 — obligation-read error: fail-closed, ISOLATED from both fail-opens
# ─────────────────────────────────────────────────────────────────────────────


class TestD1ObligationReadErrorFailClosed:
    """Query error → DENY (bound-composed degradation), never the
    whole-eval fail-open ALLOW, never the −1 sentinel short-circuit.
    """

    def test_error_below_bound_denies_with_increment(self):
        d = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=0,
            bound=3,
            attestation_required=True,
            report_obligation_read_error=True,
        )
        assert d.decision is Decision.DENIED
        assert d.next_denied_count == 1
        assert d.should_inject_nudge

    def test_error_at_bound_degrades_terminal_after_bound(self):
        """Persistent error degrades to terminal_after_bound — bounded,
        never a forever-wedge."""
        d = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=3,
            bound=3,
            attestation_required=True,
            report_obligation_read_error=True,
        )
        assert d.decision is Decision.TERMINAL_AFTER_BOUND

    def test_error_fires_before_attested_split_never_laundered(self):
        """An attested + text-report turn with an obligation-read error
        must still DENY — the error can never verify-clean via the
        attested-allow reset."""
        d = decide(
            attested=True,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=0,
            bound=3,
            attestation_required=True,
            final_ai_is_text_report=True,
            report_obligation_read_error=True,
        )
        assert d.decision is Decision.DENIED

    def test_error_yields_to_genuine_pending_inputs(self):
        """The error arm fires AFTER the legit-pending family: real
        pending work (a live descendant) still allow-holds even when
        the obligation read is unhealthy."""
        d = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=2,
            denied_count=0,
            bound=3,
            attestation_required=True,
            report_obligation_read_error=True,
        )
        assert d.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP

    def test_evaluate_isolation_raising_facade_denies_not_fail_open(self):
        """THE D1 mutation pin: a facade that RAISES produces a DENIED
        decision — NOT the whole-eval fail-open ALLOWED (which the
        outer DB-seam catch would return for the sibling reads), NOT
        the −1-sentinel per-input fail-open."""
        msgs = [real("run the mission"), delegating_ai(), plain_ai()]
        result = evaluate(
            "inst-d1",
            0,
            msgs,
            ENFORCE,
            make_manager(obligation_error=True),
        )
        assert result.decision is Decision.DENIED
        assert result.next_denied_count == 1
        assert result.report_obligation_read_error is True
        assert result.should_inject_nudge

    def test_evaluate_isolation_garbage_return_denies(self):
        """A non-int (garbage) facade return reads as unknown → error
        flag → DENY (unknown ≠ quiet)."""
        m = make_manager()
        m.count_outstanding_report_obligations = MagicMock(
            return_value="not-a-count"
        )
        msgs = [real("run the mission"), delegating_ai(), plain_ai()]
        result = evaluate("inst-d1-garbage", 0, msgs, ENFORCE, m)
        assert result.decision is Decision.DENIED
        assert result.report_obligation_read_error is True

    def test_evaluate_missing_facade_reads_inactive_legacy(self):
        """Duck-typing guard: a manager WITHOUT the facade (legacy /
        test doubles) keeps the legacy quiet posture — the arm is
        inactive, the deny path stays reachable."""
        m = make_manager()
        del m.count_outstanding_report_obligations
        msgs = [real("run the mission"), delegating_ai(), plain_ai()]
        result = evaluate("inst-legacy", 0, msgs, ENFORCE, m)
        assert result.decision is Decision.DENIED  # legacy deny path
        assert result.report_obligation_read_error is False


# ─────────────────────────────────────────────────────────────────────────────
# Per-leg "unchanged except the new input" assertions (plan §6/§7)
# ─────────────────────────────────────────────────────────────────────────────


class TestExistingLegsUnchanged:
    """The five existing arms + the deny ladder behave byte-identically
    when the new inputs are inactive (defaults)."""

    @pytest.mark.parametrize(
        "kwargs,expected,count",
        [
            # user_answer_pending plain-allow (step 1) — unchanged.
            (
                dict(user_answer_pending=True),
                Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP,
                1,
            ),
            # each legacy R2 arm — unchanged.
            (
                dict(pending_children=1),
                Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP,
                1,
            ),
            (
                dict(queued_or_expected_wakeups=1),
                Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP,
                1,
            ),
            (
                dict(live_descendants=1),
                Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP,
                1,
            ),
            (
                dict(outstanding_commissions=1),
                Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP,
                1,
            ),
            # attested + standalone text report → ALLOWED + reset —
            # unchanged.
            (
                dict(attested=True, final_ai_is_text_report=True),
                Decision.ALLOWED,
                0,
            ),
            # attested, not a text report → HOLD, counter unchanged.
            (
                dict(attested=True, final_ai_is_text_report=False),
                Decision.HOLD,
                1,
            ),
            # quiet + not attested below bound → DENIED + increment.
            (dict(), Decision.DENIED, 2),
            # quiet + not attested at bound → TERMINAL_AFTER_BOUND.
            (
                dict(),
                Decision.TERMINAL_AFTER_BOUND,
                0,
            ),
        ],
    )
    def test_matrix(self, kwargs, expected, count):
        at_bound = expected is Decision.TERMINAL_AFTER_BOUND
        d = decide(
            attested=kwargs.pop("attested", False),
            pending_children=kwargs.pop("pending_children", 0),
            queued_or_expected_wakeups=kwargs.pop(
                "queued_or_expected_wakeups", 0
            ),
            live_descendants=kwargs.pop("live_descendants", 0),
            denied_count=3 if at_bound else 1,
            bound=3,
            attestation_required=True,
            **kwargs,
        )
        assert d.decision is expected
        assert d.next_denied_count == count
