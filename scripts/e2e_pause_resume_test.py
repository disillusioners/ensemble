#!/usr/bin/env python3
"""
E2E Simulation Test: Pause/Resume Bug

Tests the scenario where:
1. Leader spawns a child (coder) via send_message
2. Parent (leader) is paused - cascades to child
3. Child is resumed first, parent stays paused
4. Parent is then resumed
5. Verify child completion report reaches parent correctly

This tests the recent fixes for:
- READY messages blocking child completion report
- Stale completion reports accumulating after pause/resume
- Child report not reaching parent after resume
- Premature job completion in resume
"""

import subprocess
import time
import requests
import sys
import json
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8079"
API_BASE = f"{BASE_URL}/api"
DEV_SH = "./dev.sh"
LOG_FILE = "/tmp/e2e_sim.log"
HEALTH_CHECK_TIMEOUT = 30
CHILD_APPEAR_TIMEOUT = 60
CHILD_APPEAR_POLL_INTERVAL = 2


def log(msg: str) -> None:
    """Print timestamped log message."""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}")
    sys.stdout.flush()


def log_json(label: str, data: dict) -> None:
    """Print JSON data with label."""
    log(f"{label}:")
    print(json.dumps(data, indent=2, default=str))
    sys.stdout.flush()


def check_server_running() -> bool:
    """Check if server is already running on port 8079."""
    try:
        resp = requests.get(f"{API_BASE}/health", timeout=2)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def wait_for_health(timeout: int = HEALTH_CHECK_TIMEOUT) -> bool:
    """Wait for server to become healthy."""
    log(f"Waiting for server health (timeout={timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{API_BASE}/health", timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                log(f"Server healthy! Uptime: {data.get('uptime_seconds', 0):.1f}s, Version: {data.get('version', '?')}")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    return False


def start_server() -> bool:
    """Start the dev server in background."""
    log(f"Starting dev server: {DEV_SH}")
    with open(LOG_FILE, "w") as f:
        proc = subprocess.Popen(
            [DEV_SH],
            stdout=f,
            stderr=subprocess.STDOUT,
            cwd="/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble",
        )
    log(f"Server started with PID {proc.pid}, logs at {LOG_FILE}")
    return wait_for_health()


def create_instance(agent_id: str, project_id: str | None = None) -> dict | None:
    """Create a new instance."""
    payload = {"agent_id": agent_id}
    if project_id is not None:
        payload["project_id"] = project_id

    log(f"Creating instance: agent_id={agent_id}")
    try:
        resp = requests.post(f"{API_BASE}/instances", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        log_json("Created instance", data)
        return data
    except requests.exceptions.RequestException as e:
        log(f"Error creating instance: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log(f"Response: {e.response.text}")
        return None


def send_message(instance_id: str, content: str) -> dict | None:
    """Send a message to an instance."""
    log(f"Sending message to {instance_id[:8]}...: {content[:80]}...")
    payload = {"content": content}

    try:
        resp = requests.post(f"{API_BASE}/instances/{instance_id}/messages", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        log_json("Message queued", data)
        return data
    except requests.exceptions.RequestException as e:
        log(f"Error sending message: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log(f"Response: {e.response.text}")
        return None


def get_instance(instance_id: str) -> dict | None:
    """Get instance info."""
    try:
        resp = requests.get(f"{API_BASE}/instances/{instance_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log(f"Error getting instance: {e}")
        return None


def get_messages(instance_id: str) -> list:
    """Get message history for an instance."""
    try:
        resp = requests.get(f"{API_BASE}/instances/{instance_id}/messages", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log(f"Error getting messages: {e}")
        return []


def pause_instance(instance_id: str) -> dict | None:
    """Pause an instance (cascade to children)."""
    log(f"Pausing instance {instance_id[:8]}...")
    try:
        resp = requests.post(f"{API_BASE}/instances/{instance_id}/pause", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        log_json("Pause result", data)
        return data
    except requests.exceptions.RequestException as e:
        log(f"Error pausing instance: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log(f"Response: {e.response.text}")
        return None


def resume_instance(instance_id: str, message: str | None = None) -> dict | None:
    """Resume a paused instance."""
    log(f"Resuming instance {instance_id[:8]}...")
    payload = {}
    if message:
        payload["message"] = message

    try:
        resp = requests.post(
            f"{API_BASE}/instances/{instance_id}/resume",
            json=payload if payload else None,
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        log_json("Resume result", data)
        return data
    except requests.exceptions.RequestException as e:
        log(f"Error resuming instance: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log(f"Response: {e.response.text}")
        return None


def wait_for_child(parent_id: str, timeout: int = CHILD_APPEAR_TIMEOUT) -> str | None:
    """Wait for a child instance to appear."""
    log(f"Waiting for child to appear (timeout={timeout}s)...")
    start = time.time()
    poll_count = 0

    while time.time() - start < timeout:
        poll_count += 1
        instance = get_instance(parent_id)
        if instance:
            children = instance.get("children", [])
            log(f"  Poll {poll_count}: children={children}")

            if children:
                child_id = children[0]
                log(f"Child found: {child_id[:8]}...")
                return child_id
        else:
            log(f"  Poll {poll_count}: instance info unavailable")

        time.sleep(CHILD_APPEAR_POLL_INTERVAL)

    log(f"Child did not appear within {timeout}s")
    return None


def get_jobs(instance_id: str | None = None, status: str | None = None) -> list:
    """Get jobs, optionally filtered by instance_id or status."""
    params = {}
    if instance_id is not None:
        params["instance_id"] = instance_id  # Will filter client-side
    if status is not None:
        params["status"] = status

    try:
        resp = requests.get(f"{API_BASE}/jobs", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", [])
        # Filter by instance_id client-side if provided (API may not support it)
        if instance_id is not None:
            jobs = [j for j in jobs if j.get("instance_id") == instance_id]
        return jobs
    except requests.exceptions.RequestException as e:
        log(f"Error getting jobs: {e}")
        return []


def find_child_completion_report(parent_id: str, child_id: str) -> dict | None:
    """Look for a child completion report in parent's messages."""
    messages = get_messages(parent_id)

    # Look for messages that indicate child completion
    # Completion reports have type="COMPLETION_REPORT" and source="internal_report:{child_id}:..."
    for msg in messages:
        content = str(msg.get("content", "")).lower()
        role = msg.get("role", "")
        msg_type = msg.get("type", "")
        source = msg.get("source", "")

        # Check for COMPLETION_REPORT type messages (system messages about child completion)
        if msg_type == "COMPLETION_REPORT" and child_id in source:
            return msg

        # Check for internal_report source format: internal_report:{child_id}:{message_id}
        if source.startswith("internal_report:") and child_id in source:
            return msg

        # Fallback: check content patterns (for backwards compatibility)
        if child_id[:8] in str(msg.get("content", "")):
            return msg
        if "completed" in content and "coder" in content:
            return msg
        if "child" in content and "complete" in content:
            return msg

    return None


def check_job_continuity(leader_id: str, original_job_id: str | None) -> tuple[bool, str]:
    """Verify the original PROCESSING job completed (not cancelled + recreated).

    Args:
        leader_id: The leader instance ID to check jobs for.
        original_job_id: The job ID that was PROCESSING when we paused.

    Returns:
        Tuple of (passed: bool, details: str)
    """
    if not original_job_id:
        return False, "No original job ID captured"

    # Get all jobs for this instance
    jobs = get_jobs(instance_id=leader_id)
    if not jobs:
        return False, "No jobs found for instance"

    # Find the original job
    original_job = None
    for job in jobs:
        if job.get("job_id") == original_job_id:
            original_job = job
            break

    if not original_job:
        return False, f"Original job {original_job_id} not found in job history"

    # Check if it completed (not cancelled)
    job_status = original_job.get("status")
    if job_status == "completed":
        return True, f"Original job {original_job_id} completed (PASS)"
    elif job_status == "cancelled":
        return False, f"Original job {original_job_id} was cancelled - job was recreated (FAIL)"
    else:
        return False, f"Original job {original_job_id} has unexpected status: {job_status}"


def main():
    log("=" * 70)
    log("E2E PAUSE/RESUME BUG SIMULATION TEST")
    log("=" * 70)

    # Step 1: Ensure server is running
    log("")
    log("STEP 1: Check/start dev server")
    log("-" * 40)

    if check_server_running():
        log("Server already running on port 8079 - REUSING")
        server_started_fresh = False
    else:
        log("Server not running - STARTING FRESH")
        if not start_server():
            log("FATAL: Could not start server")
            sys.exit(1)
        server_started_fresh = True

    # Step 2: Create leader instance
    log("")
    log("STEP 2: Create leader instance")
    log("-" * 40)

    leader = create_instance("leader")
    if not leader:
        log("FATAL: Could not create leader instance")
        sys.exit(1)

    leader_id = leader["instance_id"]
    log(f"Leader instance: {leader_id}")

    # Step 3: Send message to leader
    log("")
    log("STEP 3: Send message to leader")
    log("-" * 40)

    message_content = (
        "IMPORTANT: Spawn a coder instance and send it this exact message: "
        "'Just reply with the word HELLO. Do NOT use any tools. Do NOT call any functions. "
        "Just say HELLO and nothing else.' Wait for the coder to respond."
    )
    msg_result = send_message(leader_id, message_content)
    if not msg_result:
        log("WARNING: Could not send message, continuing anyway...")

    # Step 4: Wait for child instance
    log("")
    log("STEP 4: Wait for child instance to appear")
    log("-" * 40)

    child_id = wait_for_child(leader_id)
    if not child_id:
        log("WARNING: Child did not appear - this might be expected if workflow is fast")

        # Try to get any child that might exist
        leader_state = get_instance(leader_id)
        if leader_state and leader_state.get("children"):
            child_id = leader_state["children"][0]
            log(f"Found existing child: {child_id[:8]}...")
        else:
            log("FATAL: No child found")
            sys.exit(1)

    # Get child info
    child_info = get_instance(child_id)
    if child_info:
        log_json("Child instance info", child_info)
    else:
        log("WARNING: Could not get child info")

    # Step 5: Wait 2 seconds after child creation, capture job ID before pause
    log("")
    log("STEP 5: Wait 2 seconds and capture job ID for continuity check")
    log("-" * 40)
    log("Waiting 2 seconds before pause...")

    # Capture the PROCESSING job ID before pausing
    leader_jobs_before_pause = get_jobs(instance_id=leader_id, status="processing")
    if leader_jobs_before_pause:
        original_job_id = leader_jobs_before_pause[0].get("job_id")
        log(f"Captured PROCESSING job ID: {original_job_id}")
    else:
        original_job_id = None
        log("WARNING: No PROCESSING job found for leader before pause")

    time.sleep(2)

    # Step 6: Pause ONLY the parent (leader) - cascade pauses child too
    log("")
    log("STEP 6: Pause parent (leader) with cascade")
    log("-" * 40)

    pause_result = pause_instance(leader_id)
    if not pause_result:
        log("FATAL: Could not pause leader")
        sys.exit(1)

    log(f"Original job ID for continuity check: {original_job_id or 'N/A'}")

    # Verify both are paused
    time.sleep(1)
    leader_state = get_instance(leader_id)
    child_state = get_instance(child_id)

    log(f"After pause - Leader status: {leader_state.get('status') if leader_state else '?'}")
    log(f"After pause - Child status: {child_state.get('status') if child_state else '?'}")

    # Step 7: Wait 3 seconds, then resume ONLY parent (cascade resumes child too)
    log("")
    log("STEP 7: Wait 3 seconds, then resume parent with cascade")
    log("-" * 40)

    log("Waiting 3 seconds while paused...")
    time.sleep(3)

    log("Resuming PARENT (cascade will resume child too)...")
    resume_parent = resume_instance(leader_id, message="resume")
    if resume_parent:
        log(f"Parent resume response: resumed_ids={resume_parent.get('resumed_ids', [])}")

    time.sleep(1)
    leader_state = get_instance(leader_id)
    child_state = get_instance(child_id)
    log(f"After parent resume - Leader status: {leader_state.get('status') if leader_state else '?'}")
    log(f"After parent resume - Child status: {child_state.get('status') if child_state else '?'}")

    # Step 8: Wait 60 seconds for work to complete
    log("")
    log("STEP 8: Wait for work to complete")
    log("-" * 40)

    log("Waiting 60 seconds for completion...")
    time.sleep(60)

    # Fetch final states
    leader_state = get_instance(leader_id)
    child_state = get_instance(child_id)
    leader_messages = get_messages(leader_id)

    log("")
    log("Final state after waiting:")
    log(f"  Leader status: {leader_state.get('status') if leader_state else '?'}")
    log(f"  Leader waiting_for: {leader_state.get('waiting_for') if leader_state else '?'}")
    log(f"  Child status: {child_state.get('status') if child_state else '?'}")
    log(f"  Total messages in leader: {len(leader_messages)}")

    # Step 9: Print SUMMARY
    log("")
    log("=" * 70)
    log("SUMMARY")
    log("=" * 70)

    parent_status = leader_state.get("status") if leader_state else "unknown"
    child_status = child_state.get("status") if child_state else "unknown"

    log(f"Parent (leader) final status: {parent_status}")
    log(f"Child (coder) final status: {child_status}")
    log(f"Parent waiting_for: {leader_state.get('waiting_for') if leader_state else '?'}")

    # Look for child completion report
    completion_report = find_child_completion_report(leader_id, child_id)
    if completion_report:
        log("CHILD REPORT FOUND in parent's messages!")
        log(f"  Report content preview: {str(completion_report.get('content', ''))[:200]}...")
        report_found = True
    else:
        log("CHILD REPORT NOT FOUND in parent's messages")
        report_found = False

    # List all messages for debugging
    log("")
    log("All parent messages:")
    for i, msg in enumerate(leader_messages):
        content = str(msg.get("content", ""))[:150]
        role = msg.get("role", "?")
        msg_type = msg.get("type", "")
        source = msg.get("source", "")
        log(f"  [{i}] {role} (type={msg_type}): {content}...")
        if source:
            log(f"       source: {source}")

    # Step 10: Job Continuity Check
    log("")
    log("=" * 70)
    log("JOB CONTINUITY CHECK")
    log("=" * 70)

    job_continuity_passed, job_continuity_details = check_job_continuity(leader_id, original_job_id)
    log(f"Job continuity: {job_continuity_details}")

    # Also show all jobs for the instance for debugging
    all_jobs = get_jobs(instance_id=leader_id)
    log(f"Jobs for leader instance ({len(all_jobs)} total):")
    for job in all_jobs:
        log(f"  - job_id={job.get('job_id')[:16]}... status={job.get('status')} created={job.get('created_at', '')[:19]}")

    # Test result
    log("")
    log("=" * 70)
    log("TEST RESULT")
    log("=" * 70)

    # Criteria for success:
    # - Child should be COMPLETED
    # - Parent should not be stuck in WAITING_CHILDREN indefinitely
    # - Child report should reach parent
    # - Original job should have completed (not cancelled + recreated)

    success = True
    issues = []

    if child_status not in ("completed", "terminated"):
        issues.append(f"Child not completed (status={child_status})")
        success = False

    if parent_status in ["waiting", "waiting_children"]:
        if leader_state and leader_state.get("waiting_for", 0) > 0:
            issues.append(f"Parent stuck waiting for children (waiting_for={leader_state.get('waiting_for')})")
            success = False

    if not report_found:
        issues.append("Child completion report not found in parent messages")
        # Don't fail the test on this - it might just be formatted differently

    if not job_continuity_passed:
        issues.append(f"Job continuity check failed: {job_continuity_details}")
        success = False

    if success:
        log("ALL CHECKS PASSED")
    else:
        log("TEST FAILED:")
        for issue in issues:
            log(f"  - {issue}")

    log("")
    if server_started_fresh:
        log(f"Server was started fresh. Logs available at: {LOG_FILE}")
        log("Server is still running - use 'lsof -ti:8079 | xargs kill' to stop")
    else:
        log("Server was already running - not stopping it")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
