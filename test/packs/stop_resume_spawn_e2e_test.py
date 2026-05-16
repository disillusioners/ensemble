#!/usr/bin/env python3
"""E2E test for Pause -> Resume -> Spawn Instance fix.

This test verifies that after pausing an instance and resuming with "continue",
the spawn_instance tool works correctly without "no running event loop" errors.

Expected behavior:
- After pausing an instance and sending "continue", spawning a child instance should work
- No RuntimeWarning about unawaited coroutines
- No "no running event loop" errors in logs
"""

import subprocess
import time
import requests
import signal
import atexit
import sys
import os
import re
from pathlib import Path

# Constants
BASE_URL = "http://localhost:8079/api"
HEALTH_URL = f"{BASE_URL}/health"
DEV_SCRIPT = Path(__file__).parent.parent.parent / "dev.sh"
LOG_FILE = Path("/tmp/stop_resume_spawn_test_daemon.log")
TIMEOUT_SECONDS = 180  # 3 minutes total

# Global state
daemon_process = None
test_passed = True


def log(msg: str):
    """Print timestamped message."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cleanup():
    """Clean up daemon process on exit."""
    global daemon_process
    if daemon_process and daemon_process.poll() is None:
        log("CLEANUP: Terminating daemon process...")
        daemon_process.terminate()
        try:
            daemon_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon_process.kill()
            daemon_process.wait()
        log("CLEANUP: Daemon terminated")


def kill_port_8079():
    """Kill any process using port 8079."""
    try:
        result = subprocess.run(
            ["lsof", "-ti:8079"],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    log(f"KILLED: Process {pid} on port 8079")
                except (ValueError, ProcessLookupError):
                    pass
            time.sleep(2)
    except Exception as e:
        log(f"WARNING: Could not check port 8079: {e}")


def wait_for_health(max_attempts=30, delay=2):
    """Wait for daemon health check to return 200."""
    for attempt in range(max_attempts):
        try:
            resp = requests.get(HEALTH_URL, timeout=5)
            if resp.status_code == 200:
                log(f"HEALTH: Daemon is healthy (attempt {attempt + 1})")
                return True
        except requests.exceptions.ConnectionError:
            pass
        except requests.exceptions.Timeout:
            pass
        time.sleep(delay)
    return False


def start_daemon():
    """Start the daemon using dev.sh."""
    global daemon_process

    log("START: Launching daemon via dev.sh...")

    # Open log file
    log_fd = open(LOG_FILE, "w")

    # Start daemon process
    daemon_process = subprocess.Popen(
        [str(DEV_SCRIPT)],
        stdout=log_fd,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid  # Create new process group for clean kill
    )

    log(f"START: Daemon PID={daemon_process.pid}, logging to {LOG_FILE}")

    return daemon_process


def check_instance_status(instance_id):
    """Get instance status via API."""
    resp = requests.get(f"{BASE_URL}/instances/{instance_id}", timeout=5)
    if resp.status_code == 200:
        return resp.json()
    return None


def create_instance(agent_id="leader"):
    """Create a new instance."""
    log(f"CREATE: Spawning {agent_id} instance...")
    resp = requests.post(
        f"{BASE_URL}/instances",
        json={"agent_id": agent_id},
        timeout=10
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        log(f"CREATE: SUCCESS - instance_id={data['instance_id']}, status={data['status']}")
        return data
    log(f"CREATE: FAILED - {resp.status_code} {resp.text}")
    return None


def send_message(instance_id, content):
    """Send a message to an instance."""
    log(f"MESSAGE: Sending to {instance_id}: {content[:80]}...")
    resp = requests.post(
        f"{BASE_URL}/instances/{instance_id}/messages",
        json={"content": content},
        timeout=10
    )
    if resp.status_code == 200:
        data = resp.json()
        log(f"MESSAGE: SUCCESS - message_id={data.get('message_id')}")
        return True
    log(f"MESSAGE: FAILED - {resp.status_code} {resp.text}")
    return False


def pause_instance(instance_id):
    """Pause an instance."""
    log(f"PAUSE: Pausing instance {instance_id}...")
    resp = requests.post(f"{BASE_URL}/instances/{instance_id}/pause", timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        log(f"PAUSE: SUCCESS - paused_ids={data.get('paused_ids')}")
        return True
    log(f"PAUSE: FAILED - {resp.status_code} {resp.text}")
    return False


def check_logs_for_errors():
    """Check daemon logs for the fixed errors."""
    global test_passed

    errors_found = []
    warnings_found = []

    try:
        with open(LOG_FILE, "r") as f:
            content = f.read()

        # Check for "no running event loop" error
        if "no running event loop" in content.lower():
            errors_found.append("Found 'no running event loop' error")

        # Check for RuntimeWarning about unawaited coroutines
        if "runtimewarning" in content.lower() and "coroutine" in content.lower():
            warnings_found.append("Found RuntimeWarning about unawaited coroutines")

        # Check for spawn_instance related success indicators
        spawn_indicators = [
            "spawn_instance",
            "Instance created",
            "spawning instance",
            "child instance",
        ]
        found_indicators = []
        for indicator in spawn_indicators:
            if indicator.lower() in content.lower():
                found_indicators.append(indicator)

        if found_indicators:
            log(f"LOGS: Found spawn indicators: {found_indicators}")

        # Log results
        if errors_found:
            for err in errors_found:
                log(f"LOGS: ERROR - {err}")
            test_passed = False
        else:
            log("LOGS: PASS - No 'no running event loop' errors found")

        if warnings_found:
            for warn in warnings_found:
                log(f"LOGS: WARNING - {warn}")
            test_passed = False
        else:
            log("LOGS: PASS - No RuntimeWarning about coroutines found")

        return errors_found, warnings_found

    except Exception as e:
        log(f"LOGS: ERROR reading log file: {e}")
        return [f"Failed to read logs: {e}"], []


def run_test():
    """Run the full E2E test scenario."""
    global test_passed

    log("=" * 60)
    log("E2E TEST: Pause -> Resume -> Spawn Instance Fix Verification")
    log("=" * 60)

    # Step 1: Start daemon
    log("\n--- STEP 1: Start Daemon ---")
    kill_port_8079()
    start_daemon()

    if not wait_for_health():
        log("STEP 1: FAIL - Daemon did not become healthy")
        return False
    log("STEP 1: PASS - Daemon started successfully")

    try:
        # Step 2: Spawn leader instance
        log("\n--- STEP 2: Spawn Leader Instance ---")
        instance = create_instance("leader")
        if not instance:
            log("STEP 2: FAIL - Could not create leader instance")
            return False
        leader_id = instance["instance_id"]
        log(f"STEP 2: PASS - Created leader instance {leader_id}")

        # Step 3: Send message asking leader to spawn coder
        log("\n--- STEP 3: Send Spawn Request to Leader ---")
        spawn_request = (
            "Hello! Please spawn a coder instance to do a simple task. "
            "Use the spawn_instance tool to create a coder instance."
        )
        if not send_message(leader_id, spawn_request):
            log("STEP 3: FAIL - Could not send spawn request")
            return False
        log("STEP 3: PASS - Spawn request sent")

        # Step 4: Wait for leader to process
        log("\n--- STEP 4: Wait for Processing (7 seconds) ---")
        time.sleep(7)
        log("STEP 4: Done waiting")

        # Step 5: Pause the instance
        log("\n--- STEP 5: Pause Instance ---")
        if not pause_instance(leader_id):
            log("STEP 5: FAIL - Could not pause instance")
            return False
        log("STEP 5: PASS - Instance paused")

        # Step 6: Verify instance status is paused
        log("\n--- STEP 6: Verify Instance Status ---")
        time.sleep(1)  # Give it a moment
        instance_info = check_instance_status(leader_id)
        if instance_info:
            status = instance_info.get("status")
            log(f"STEP 6: Instance status after pause: {status}")
            if status == "paused":
                log("STEP 6: PASS - Instance status is 'paused'")
            else:
                log(f"STEP 6: WARNING - Expected 'paused', got '{status}'")
        else:
            log("STEP 6: WARNING - Could not get instance info")

        # Step 7: Send "continue" message
        log("\n--- STEP 7: Send 'continue' Message ---")
        if not send_message(leader_id, "continue"):
            log("STEP 7: FAIL - Could not send continue message")
            return False
        log("STEP 7: PASS - Continue message sent")

        # Step 8: Wait for resume and spawn attempt
        log("\n--- STEP 8: Wait for Resume Processing (15 seconds) ---")
        log("This is when the fix is tested - spawn_instance should work after resume")
        time.sleep(15)
        log("STEP 8: Done waiting")

        # Step 9: Check logs for errors
        log("\n--- STEP 9: Check Daemon Logs ---")
        check_logs_for_errors()

        # Step 10: Pause instance again (cleanup)
        log("\n--- STEP 10: Pause Instance (Cleanup) ---")
        pause_instance(leader_id)
        time.sleep(1)

        # Step 11: Bonus - Second pause/resume cycle
        log("\n--- STEP 11: Second Pause/Resume Cycle (Bonus) ---")
        if send_message(leader_id, "Tell me a joke"):
            log("STEP 11a: Sent second message")
            time.sleep(3)
            if pause_instance(leader_id):
                log("STEP 11b: Paused again")
                time.sleep(1)
                instance_info = check_instance_status(leader_id)
                if instance_info and instance_info.get("status") == "paused":
                    log("STEP 11c: Status verified as 'paused'")
                if send_message(leader_id, "continue"):
                    log("STEP 11d: Resumed again - no crash expected")
                    time.sleep(5)
                    log("STEP 11: PASS - Second cycle completed without crash")
                else:
                    log("STEP 11: FAIL - Could not resume second time")
            else:
                log("STEP 11: FAIL - Could not pause second time")
        else:
            log("STEP 11: SKIP - Could not send second message")

        log("\n" + "=" * 60)
        if test_passed:
            log("RESULT: PASS - All checks passed")
            log("The 'no running event loop' fix is working correctly!")
        else:
            log("RESULT: FAIL - Some checks failed")
            log("Check the daemon logs for details")
        log("=" * 60)

        return test_passed

    finally:
        # Cleanup
        cleanup()


if __name__ == "__main__":
    # Register cleanup handler
    atexit.register(cleanup)

    # Handle signals
    def signal_handler(signum, frame):
        log(f"\nReceived signal {signum}, cleaning up...")
        cleanup()
        sys.exit(1)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run test with timeout
    try:
        result = run_test()
        sys.exit(0 if result else 1)
    except subprocess.TimeoutExpired:
        log("TIMEOUT: Test exceeded 3 minute limit")
        cleanup()
        sys.exit(2)
    except Exception as e:
        log(f"ERROR: Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        cleanup()
        sys.exit(3)
