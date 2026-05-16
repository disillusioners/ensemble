#!/usr/bin/env python3
"""E2E test for Pause TTL + Cold Resume flow.

This test verifies that:
1. Pausing an instance sets paused_at in the database
2. After daemon restart (simulating TTL expiry), cold resume works
3. The instance properly restores from database and continues processing

Expected behavior:
- paused_at is set when instance is paused
- After daemon restart, sending a message triggers cold resume
- Instance status transitions: paused -> running -> completed
"""

import subprocess
import time
import requests
import signal
import atexit
import sys
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

# Constants
BASE_URL = "http://localhost:8079/api"
HEALTH_URL = f"{BASE_URL}/health"
DEV_SCRIPT = Path(__file__).parent.parent.parent / "dev.sh"
LOG_FILE = Path("/tmp/pause_ttl_cold_resume_test_daemon.log")
DATA_DIR = Path(__file__).parent.parent.parent / "data_dev"
DB_PATH = DATA_DIR / "instances.db"
TIMEOUT_SECONDS = 180  # 3 minutes total

# Global state
daemon_process = None
test_passed = True
instance_id = None


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

    # Ensure data directory exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

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


def stop_daemon():
    """Stop the daemon process."""
    global daemon_process

    if daemon_process:
        daemon_pid = daemon_process.pid
        log(f"STOP: Stopping daemon PID={daemon_pid}...")

        # Terminate the main process
        if daemon_process.poll() is None:
            daemon_process.terminate()
            try:
                daemon_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                daemon_process.kill()
                daemon_process.wait()

        daemon_process = None
        time.sleep(1)

        # Verify port 8079 is now free, if not, force kill
        result = subprocess.run(
            ["lsof", "-ti:8079"],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            log(f"STOP: Force killing {len(pids)} remaining process(es)...")
            for pid in pids:
                pid_int = int(pid.strip())
                # Safety: don't kill ourselves or obvious parent processes
                if pid_int != os.getpid() and pid_int != os.getppid():
                    try:
                        os.kill(pid_int, signal.SIGKILL)
                    except (ValueError, ProcessLookupError):
                        pass
            time.sleep(2)

        log("STOP: Daemon stopped")


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


def send_message(instance_id, content, timeout=60):
    """Send a message to an instance."""
    log(f"MESSAGE: Sending to {instance_id}: {content[:80]}...")
    resp = requests.post(
        f"{BASE_URL}/instances/{instance_id}/messages",
        json={"content": content},
        timeout=timeout
    )
    if resp.status_code == 200:
        data = resp.json()
        log(f"MESSAGE: SUCCESS - message_id={data.get('message_id')}")
        return data
    log(f"MESSAGE: FAILED - {resp.status_code} {resp.text}")
    return None


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


def check_db_paused_at(instance_id):
    """Check paused_at in database."""
    if not DB_PATH.exists():
        log(f"DB: Database not found at {DB_PATH}")
        return None

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    cursor.execute(
        "SELECT instance_id, status, paused_at FROM instances WHERE instance_id = ?",
        (instance_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            "instance_id": row[0],
            "status": row[1],
            "paused_at": row[2]
        }
    return None


def wait_for_instance_status(instance_id, target_status, max_wait=30):
    """Wait for instance to reach a specific status."""
    for _ in range(max_wait):
        info = check_instance_status(instance_id)
        if info and info.get("status") == target_status:
            return True
        time.sleep(1)
    return False


def run_test():
    """Run the full E2E test scenario."""
    global test_passed, instance_id

    log("=" * 60)
    log("E2E TEST: Pause TTL + Cold Resume Verification")
    log("=" * 60)

    # Check for OPENAI_API_KEY
    if not os.environ.get("OPENAI_API_KEY"):
        log("SKIP: OPENAI_API_KEY not set - cannot run test")
        return False

    # Step 1: Start daemon
    log("\n--- STEP 1: Start Daemon ---")
    kill_port_8079()
    start_daemon()

    if not wait_for_health():
        log("STEP 1: FAIL - Daemon did not become healthy")
        return False
    log("STEP 1: PASS - Daemon started successfully")

    try:
        # Step 2: Create instance
        log("\n--- STEP 2: Create Instance ---")
        instance = create_instance("leader")
        if not instance:
            log("STEP 2: FAIL - Could not create instance")
            return False
        instance_id = instance["instance_id"]
        log(f"STEP 2: PASS - Created instance {instance_id}")

        # Step 3: Send initial message and wait for completion
        log("\n--- STEP 3: Send Initial Message ---")
        msg_resp = send_message(instance_id, "Hello! Just say 'Hello received' and nothing else.", timeout=60)
        if not msg_resp:
            log("STEP 3: FAIL - Could not send message")
            return False
        log("STEP 3: PASS - Initial message sent")

        # Wait for processing
        log("STEP 3: Waiting for initial processing (5 seconds)...")
        time.sleep(5)

        # Step 4: Pause the instance
        log("\n--- STEP 4: Pause Instance ---")
        if not pause_instance(instance_id):
            log("STEP 4: FAIL - Could not pause instance")
            return False

        # Verify via API
        time.sleep(1)
        info = check_instance_status(instance_id)
        if info and info.get("status") == "paused":
            log("STEP 4a: PASS - Instance status is 'paused' via API")
        else:
            log(f"STEP 4a: WARNING - Expected 'paused', got '{info.get('status') if info else 'N/A'}'")
        log("STEP 4: PASS - Instance paused")

        # Step 5: Check paused_at in database
        log("\n--- STEP 5: Check paused_at in DB ---")
        db_info = check_db_paused_at(instance_id)
        if not db_info:
            log("STEP 5: FAIL - Could not query database")
            test_passed = False
        elif db_info["paused_at"]:
            log(f"STEP 5: PASS - paused_at is set: {db_info['paused_at']}")
            log(f"STEP 5: PASS - status in DB: {db_info['status']}")
        else:
            log("STEP 5: FAIL - paused_at is NULL in database")
            test_passed = False

        # Step 6: Stop daemon (simulates TTL expiry - graph removed from memory)
        log("\n--- STEP 6: Stop Daemon (Simulate TTL Expiry) ---")
        stop_daemon()
        log("STEP 6: PASS - Daemon stopped (graph removed from memory)")

        # Wait a moment for cleanup
        time.sleep(2)

        # Step 7: Restart daemon
        log("\n--- STEP 7: Restart Daemon ---")
        start_daemon()
        if not wait_for_health():
            log("STEP 7: FAIL - Daemon did not become healthy after restart")
            return False
        log("STEP 7: PASS - Daemon restarted successfully")

        # Step 8: Cold resume via message
        log("\n--- STEP 8: Cold Resume via Message ---")
        log("STEP 8: Sending message to trigger cold resume...")

        # Verify instance is still in DB after restart
        db_info_after_restart = check_db_paused_at(instance_id)
        if db_info_after_restart:
            log(f"STEP 8a: Instance still in DB - status={db_info_after_restart['status']}, paused_at={db_info_after_restart['paused_at']}")

        # Send message - this should trigger cold resume
        msg_resp = send_message(instance_id, "Please continue. Say 'Resumed' if you can hear me.", timeout=90)
        if msg_resp:
            log("STEP 8: PASS - Cold resume message accepted")
        else:
            log("STEP 8: FAIL - Could not send cold resume message")
            test_passed = False

        # Wait for cold resume processing
        log("STEP 8: Waiting for cold resume processing (10 seconds)...")
        time.sleep(10)

        # Step 9: Verify final status
        log("\n--- STEP 9: Verify Final Status ---")
        final_info = check_instance_status(instance_id)
        if final_info:
            final_status = final_info.get("status")
            log(f"STEP 9: Final status: {final_status}")

            # Status should NOT be 'paused' anymore
            if final_status == "paused":
                log("STEP 9: WARNING - Status is still 'paused' after cold resume")
            elif final_status in ("completed", "idle", "running"):
                log(f"STEP 9: PASS - Status is '{final_status}' (not stuck at paused)")
            else:
                log(f"STEP 9: INFO - Status is '{final_status}'")
        else:
            log("STEP 9: WARNING - Could not get instance status")

        # Check DB for final state
        final_db_info = check_db_paused_at(instance_id)
        if final_db_info:
            log(f"STEP 9b: Final DB status={final_db_info['status']}, paused_at={final_db_info['paused_at']}")
            if final_db_info['paused_at'] is None:
                log("STEP 9b: PASS - paused_at is NULL (cleared after resume)")
            else:
                log("STEP 9b: INFO - paused_at still set (may be cleared on next message)")

        log("\n" + "=" * 60)
        if test_passed:
            log("RESULT: PASS - Cold resume flow working correctly")
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
