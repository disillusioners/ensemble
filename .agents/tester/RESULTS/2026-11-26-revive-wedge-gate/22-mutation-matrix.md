# Mutation matrix — revive-wedge gate

| Mutation | Group | Target file | Red observed | Expected pin RED (hit) | Restore byte-identical | Restored run GREEN | Verdict |
|---|---|---|---|---|---|---|---|
| L2a | L2 | `tests/unit/services/test_stale_running_sweep_rider_pins.py` | 2 red(s) | test_prior_pending_report_under_a_different_anchor_blocks_a_second | YES | YES | **PASS** |
| L2b | L2 | `tests/unit/services/test_stale_running_sweep_rider_pins.py` | 1 red(s) | test_prior_pending_report_under_a_different_anchor_blocks_a_second | YES | YES | **PASS** |
| L2b2 | L2 | `tests/unit/services/test_stale_running_sweep_rider_pins.py` | 1 red(s) | test_new_anchor_with_prior_delivered_report_mints_a_second_row | YES | YES | **PASS** |
| L2c | L2 | `tests/unit/services/test_stale_running_sweep_rider_pins.py` | 1 red(s) | test_same_anchor_re_supplied_with_prior_delivered_report_does_not_mint | YES | YES | **PASS** |
| L2d | L2 | `tests/unit/services/test_child_reports.py` | 1 red(s) | test_a_second_invocation_without_a_revive_mints_no_second_row | YES | YES | **PASS** |
| M-C1 | M-C | `tests/unit/services/test_parent_completion_guard_pins.py` | 2 red(s) | test_pc1_root_terminal_fire_with_a_queued_watchdog_notice, test_pc2_non_root_reports_to_parent_with_a_queued_watchdog_notice | YES | YES | **PASS** |
| M-C2 | M-C | `tests/unit/services/test_parent_completion_guard_pins.py` | 1 red(s) | test_pc3_recency_old_task_does_not_defer_fresh_task_does | YES | YES | **PASS** |
| M-C3 | M-C | `tests/unit/services/test_parent_completion_guard_pins.py` | 1 red(s) | test_pc4_system_resume_wake_still_defers | YES | YES | **PASS** |
| M-C4 | M-C | `tests/unit/services/test_parent_completion_guard_pins.py` | 1 red(s) | test_pc6_injected_report_exclusion_still_holds | YES | YES | **PASS** |
| M-C5 | M-C | `tests/unit/services/test_parent_completion_guard_pins.py` | 3 red(s) | test_pc1_root_terminal_fire_with_a_queued_watchdog_notice, test_pc2_non_root_reports_to_parent_with_a_queued_watchdog_notice, test_pc3_recency_old_task_does_not_defer_fresh_task_does | YES | YES | **PASS** |
| M-C6 | M-C | `tests/unit/services/test_parent_completion_guard_pins.py` | 1 red(s) | test_pc9_two_arm_premature_terminal_protection | YES | YES | **PASS** |
| M-C7 | M-C | `tests/unit/services/test_parent_completion_guard_pins.py` | 10 red(s) | test_pc10_dual_dialect_portability_compile | YES | YES | **PASS** |
| M-C8 | M-C | `tests/unit/services/test_parent_completion_guard_pins.py` | 1 red(s) | test_pc1_root_terminal_fire_with_a_queued_watchdog_notice | YES | YES | **PASS** |
| NEW7-a | NEW-7 | `tests/unit/services/test_child_reports.py` | 3 red(s) | test_arm_b_suppression_emits_exactly_one_record_and_counts_only_a_loss | YES | YES | **PASS** |
| NEW7-b | NEW-7 | `tests/unit/services/test_child_reports.py` | 2 red(s) | test_arm_b_suppression_emits_exactly_one_record_and_counts_only_a_loss | YES | YES | **PASS** |
| NEW7-c | NEW-7 | `tests/unit/services/test_child_reports.py` | 1 red(s) | test_an_arm_a_only_suppression_emits_exactly_one_arm_a_record | YES | YES | **PASS** |
| NEW7-d | NEW-7 | `tests/unit/services/test_child_reports.py` | 1 red(s) | test_a_mint_over_a_retrying_obligation_emits_exactly_one_info_record | YES | YES | **PASS** |
| NEW7-e | NEW-7 | `tests/unit/services/test_child_reports.py` | 1 red(s) | test_concurrent_counter_increments_lose_no_update | YES | YES | **PASS** |
| B-M1 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 1 red(s) | test_explicit_window_starts_at_start_line_and_excludes_earlier_lines | YES | YES | **PASS** |
| B-M2 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 7 red(s) | test_explicit_window_keeps_absolute_line_numbers | YES | YES | **PASS** |
| B-M3 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 1 red(s) | test_default_path_window_is_unchanged_tail_window | YES | YES | **PASS** |
| B-M4 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 4 red(s) | test_explicit_window_reports_window_range_and_total | YES | YES | **PASS** |
| B-M5 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 1 red(s) | — | YES | YES | **FAIL-NO-RED** |
| B-M6 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 2 red(s) | test_start_line_zero_is_rejected_with_friendly_error | YES | YES | **PASS** |
| B-M7 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 1 red(s) | test_start_line_beyond_eof_returns_distinct_message | YES | YES | **PASS** |
| B-M8 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 1 red(s) | test_explicit_window_respects_match_limit | YES | YES | **PASS** |
| B-M9 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 1 red(s) | test_explicit_window_preserves_context_level_and_redaction | YES | YES | **PASS** |
| B-M10 | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 1 red(s) | test_start_line_is_appended_last_in_the_signature | YES | YES | **PASS** |
| L2d-prev | L2 | `tests/unit/services/test_child_reports.py` | 1 red(s) | test_a_second_invocation_without_a_revive_mints_no_second_row | YES | YES | **PASS** |
| M-C6b | M-C | `tests/unit/services/test_parent_completion_guard_pins.py` | 1 red(s) | test_pc3_recency_old_task_does_not_defer_fresh_task_does | YES | YES | **PASS** |
| B-M5b | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 2 red(s) | test_explicit_window_reports_unsearched_region_and_resume_point | YES | YES | **PASS** |
| B-M8b | B | `tests/unit/tools/test_system_log_search_paging_pins.py` | 1 red(s) | test_explicit_window_respects_match_limit | YES | YES | **PASS** |
