#!/usr/bin/env python3
"""
Mock Integration Test for Resume Redesign API

Tests the redesigned resume endpoint against live dev server at http://localhost:8079

Validates:
- Resume with default message (no body) → sends "resume"
- Resume with custom message → sends the custom text
- Resume when already RUNNING → skips (no message enqueued)

Usage:
    python /tmp/resume_mock_test.py
"""

import json
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

# Configuration
BASE_URL = "http://localhost:8079/api"
TIMEOUT = 10  # seconds per HTTP request
TOTAL_TIMEOUT = 300  # 5 minutes total script timeout
POLL_INTERVAL = 0.5  # seconds between status checks


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""


@dataclass
class TestSuite:
    results: list[TestResult] = field(default_factory=list)
    test_instance_id: Optional[str] = None
    test_instance_ids: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, message: str = ""):
        self.results.append(TestResult(name, passed, message))
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        if message and not passed:
            print(f"         Details: {message}")

    def summary(self) -> tuple[int, int]:
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        print(f"\n{'='*60}")
        print(f"RESULTS: {passed}/{total} tests passed")
        print(f"{'='*60}")
        return passed, total

    def cleanup(self):
        """Delete all test instances."""
        print("\n--- Cleanup ---")
        for instance_id in self.test_instance_ids:
            try:
                response = requests.delete(
                    f"{BASE_URL}/instances/{instance_id}",
                    timeout=TIMEOUT
                )
                status = response.status_code
                print(f"  Deleted: {instance_id} (status={status})")
            except Exception as e:
                print(f"  Failed to delete {instance_id}: {e}")


def check_health() -> bool:
    """Check if dev server is healthy."""
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        return response.status_code == 200
    except Exception:
        return False


def list_instances() -> list[dict]:
    """List all instances."""
    try:
        response = requests.get(f"{BASE_URL}/instances", timeout=TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            return data.get("instances", [])
        return []
    except Exception:
        return []


def create_instance(agent_id: str = "developer") -> Optional[dict]:
    """Create a test instance."""
    try:
        response = requests.post(
            f"{BASE_URL}/instances",
            json={"agent_id": agent_id},
            timeout=TIMEOUT
        )
        if response.status_code in (200, 201):
            return response.json()
        return None
    except Exception as e:
        print(f"    Error creating instance: {e}")
        return None


def get_instance(instance_id: str) -> Optional[dict]:
    """Get instance details."""
    try:
        response = requests.get(f"{BASE_URL}/instances/{instance_id}", timeout=TIMEOUT)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None


def get_messages(instance_id: str) -> list[dict]:
    """Get message history for an instance."""
    try:
        response = requests.get(f"{BASE_URL}/instances/{instance_id}/messages", timeout=TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            return data if isinstance(data, list) else data.get("messages", [])
        return []
    except Exception:
        return []


def get_jobs(instance_id: str) -> list[dict]:
    """Get jobs for an instance."""
    try:
        response = requests.get(f"{BASE_URL}/jobs", timeout=TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            jobs = data.get("jobs", [])
            # Filter jobs for this instance
            return [j for j in jobs if j.get("instance_id") == instance_id]
        return []
    except Exception:
        return []


def find_resume_job(messages: list[dict], content: str) -> Optional[dict]:
    """Find a job with the given message content."""
    for msg in messages:
        if content.lower() in msg.get("content", "").lower():
            return msg
    return None


def wait_for_status(instance_id: str, target_status: str, timeout: int = 30) -> bool:
    """Wait for instance to reach target status."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        instance = get_instance(instance_id)
        if instance:
            status = instance.get("status", "").lower()
            if status == target_status.lower():
                return True
        time.sleep(POLL_INTERVAL)
    return False


def pause_instance(instance_id: str) -> tuple[bool, Optional[dict]]:
    """Pause an instance."""
    try:
        response = requests.post(f"{BASE_URL}/instances/{instance_id}/pause", timeout=TIMEOUT)
        if response.status_code == 200:
            return True, response.json()
        return False, response.json() if response.status_code != 404 else None
    except Exception:
        return False, None


def resume_instance(instance_id: str, message: Optional[str] = None) -> tuple[bool, Optional[dict]]:
    """
    Resume an instance with optional custom message.

    Args:
        instance_id: The instance to resume
        message: Optional custom message. If None, sends no body (tests default).
                 If provided, sends {"message": message}.
    """
    try:
        url = f"{BASE_URL}/instances/{instance_id}/resume"
        if message is not None:
            response = requests.post(url, json={"message": message}, timeout=TIMEOUT)
        else:
            response = requests.post(url, timeout=TIMEOUT)
        if response.status_code == 200:
            return True, response.json()
        return False, response.json()
    except Exception as e:
        return False, None


def send_message(instance_id: str, message: str) -> Optional[dict]:
    """Send a message to an instance."""
    try:
        response = requests.post(
            f"{BASE_URL}/instances/{instance_id}/messages",
            json={"content": message},
            timeout=TIMEOUT
        )
        if response.status_code in (200, 202):
            return response.json()
        return None
    except Exception:
        return None


def find_or_create_test_instance(suite: TestSuite) -> Optional[str]:
    """Find an existing test instance or create a new one."""
    # Look for existing test-resume-mock instances
    instances = list_instances()
    for inst in instances:
        title = inst.get("title", "") or ""
        if "test-resume" in title.lower():
            return inst.get("instance_id")

    # Create a new instance
    instance = create_instance()
    if instance:
        instance_id = instance.get("instance_id")
        suite.test_instance_ids.append(instance_id)
        return instance_id
    return None


def run_test_1_default_message(suite: TestSuite) -> bool:
    """
    Test 1: Resume with default message (no body)

    Steps:
    1. Create/spawn an instance
    2. Send a message to it (so it has history)
    3. Pause the instance
    4. Wait for PAUSED state
    5. Resume with NO body
    6. Verify response is 200
    7. Verify instance is no longer PAUSED
    8. Check message history contains "resume" message
    """
    print("\n[TEST 1] Resume with default message (no body)")
    print("-" * 40)

    # Step 1: Create instance
    print("\n  [1.1] Create test instance")
    instance = create_instance()
    if not instance:
        suite.add("Test 1: Create instance", False, "Failed to create instance")
        return False

    instance_id = instance.get("instance_id")
    suite.test_instance_id = instance_id
    suite.test_instance_ids.append(instance_id)
    suite.add("Test 1: Create instance", True, f"instance_id={instance_id}")

    # Give the instance a moment to initialize
    time.sleep(1.0)

    # Step 2: Send a message to the instance (so it has history)
    print("\n  [1.2] Send initial message to instance")
    msg_response = send_message(instance_id, "Hello, this is a test message")
    if msg_response:
        suite.add("Test 1: Send initial message", True)
    else:
        suite.add("Test 1: Send initial message", False, "Message send failed")
        return False

    # Wait for message to be processed
    time.sleep(2.0)

    # Step 3: Pause the instance
    print("\n  [1.3] Pause the instance")
    success, pause_data = pause_instance(instance_id)
    if not success:
        suite.add("Test 1: Pause instance", False, f"Pause failed: {pause_data}")
        return False
    suite.add("Test 1: Pause instance", True, f"Response: {json.dumps(pause_data)}")

    # Step 4: Wait for PAUSED state
    print("\n  [1.4] Wait for PAUSED state")
    is_paused = wait_for_status(instance_id, "PAUSED", timeout=30)
    instance_data = get_instance(instance_id)
    current_status = instance_data.get("status", "unknown") if instance_data else "unknown"
    suite.add(
        "Test 1: Instance is PAUSED",
        is_paused,
        f"current_status={current_status}"
    )
    if not is_paused:
        return False

    # Step 5: Resume with NO body
    print("\n  [1.5] Resume with NO body (testing default message)")
    success, resume_data = resume_instance(instance_id, message=None)
    if not success:
        suite.add("Test 1: Resume (no body)", False, f"Resume failed: {resume_data}")
        return False
    suite.add("Test 1: Resume returns 200", True, f"Response: {json.dumps(resume_data)}")

    # Step 6: Verify resumed_ids includes our instance
    resumed_ids = resume_data.get("resumed_ids", [])
    suite.add(
        "Test 1: Instance in resumed_ids",
        instance_id in resumed_ids,
        f"resumed_ids={resumed_ids}"
    )

    # Step 7: Verify message_id is present (message was enqueued)
    message_id = resume_data.get("message_id")
    suite.add(
        "Test 1: message_id returned",
        message_id is not None,
        f"message_id={message_id}"
    )

    # Step 8: Verify instance is no longer PAUSED
    time.sleep(0.5)
    instance_data = get_instance(instance_id)
    current_status = instance_data.get("status", "unknown") if instance_data else "unknown"
    is_still_paused = current_status.lower() == "paused"
    suite.add(
        "Test 1: Instance not PAUSED after resume",
        not is_still_paused,
        f"current_status={current_status}"
    )

    # Step 9: Check job queue for resume message
    time.sleep(2.0)  # Wait for job to be created
    jobs = get_jobs(instance_id)
    resume_job = None
    for job in jobs:
        job_message = job.get("message", "")
        if "resume" in job_message.lower():
            resume_job = job
            break

    suite.add(
        "Test 1: Resume job in queue",
        resume_job is not None,
        f"Found resume job: {resume_job is not None}, jobs count: {len(jobs)}"
    )

    # Summary for Test 1
    test1_passed = all(r.passed for r in suite.results if r.name.startswith("Test 1:"))
    print(f"\n  Test 1 Summary: {'PASS' if test1_passed else 'FAIL'}")
    return test1_passed


def run_test_2_custom_message(suite: TestSuite) -> bool:
    """
    Test 2: Resume with custom message

    Steps:
    1. Use existing test instance (or create new)
    2. Pause the instance
    3. Wait for PAUSED state
    4. Resume with {"message": "please continue with X"}
    5. Verify response is 200
    6. Check message history contains "please continue with X"
    """
    print("\n[TEST 2] Resume with custom message")
    print("-" * 40)

    # Get or create an instance
    instance_id = suite.test_instance_id
    if not instance_id:
        instance = create_instance()
        if not instance:
            suite.add("Test 2: Create/get instance", False, "Failed to create instance")
            return False
        instance_id = instance.get("instance_id")
        suite.test_instance_ids.append(instance_id)
        suite.test_instance_id = instance_id
        time.sleep(1.0)

    suite.add("Test 2: Have test instance", True, f"instance_id={instance_id}")

    # Step 1: Pause the instance
    print("\n  [2.1] Pause the instance")
    success, pause_data = pause_instance(instance_id)
    if not success:
        suite.add("Test 2: Pause instance", False, f"Pause failed: {pause_data}")
        return False
    suite.add("Test 2: Pause instance", True)

    # Step 2: Wait for PAUSED state
    print("\n  [2.2] Wait for PAUSED state")
    is_paused = wait_for_status(instance_id, "PAUSED", timeout=30)
    instance_data = get_instance(instance_id)
    current_status = instance_data.get("status", "unknown") if instance_data else "unknown"
    suite.add(
        "Test 2: Instance is PAUSED",
        is_paused,
        f"current_status={current_status}"
    )
    if not is_paused:
        return False

    # Step 3: Resume with custom message
    custom_message = "please continue with X"
    print(f"\n  [2.3] Resume with custom message: '{custom_message}'")
    success, resume_data = resume_instance(instance_id, message=custom_message)
    if not success:
        suite.add("Test 2: Resume with custom message", False, f"Resume failed: {resume_data}")
        return False
    suite.add("Test 2: Resume returns 200", True, f"Response: {json.dumps(resume_data)}")

    # Step 4: Verify message_id is present
    message_id = resume_data.get("message_id")
    suite.add(
        "Test 2: message_id returned",
        message_id is not None,
        f"message_id={message_id}"
    )

    # Step 5: Check job queue for custom message
    time.sleep(2.0)  # Wait for job to be created
    jobs = get_jobs(instance_id)
    custom_job = None
    for job in jobs:
        job_message = job.get("message", "")
        if custom_message.lower() in job_message.lower():
            custom_job = job
            break

    suite.add(
        "Test 2: Custom message job in queue",
        custom_job is not None,
        f"Found custom job: {custom_job is not None}, jobs count: {len(jobs)}"
    )

    # Summary for Test 2
    test2_passed = all(r.passed for r in suite.results if r.name.startswith("Test 2:"))
    print(f"\n  Test 2 Summary: {'PASS' if test2_passed else 'FAIL'}")
    return test2_passed


def run_test_3_already_running(suite: TestSuite) -> bool:
    """
    Test 3: Resume when already RUNNING

    Steps:
    1. Use existing test instance
    2. Ensure it is RUNNING (not paused)
    3. Get message count before
    4. Try to resume (should skip, no message enqueued)
    5. Verify response shows skipped_ids
    6. Verify message count did NOT increase
    """
    print("\n[TEST 3] Resume when already RUNNING")
    print("-" * 40)

    # Get or create an instance
    instance_id = suite.test_instance_id
    if not instance_id:
        instance = create_instance()
        if not instance:
            suite.add("Test 3: Create/get instance", False, "Failed to create instance")
            return False
        instance_id = instance.get("instance_id")
        suite.test_instance_ids.append(instance_id)
        suite.test_instance_id = instance_id
        time.sleep(1.0)

    suite.add("Test 3: Have test instance", True, f"instance_id={instance_id}")

    # Step 1: Ensure instance is RUNNING
    print("\n  [3.1] Ensure instance is RUNNING")
    instance_data = get_instance(instance_id)
    current_status = instance_data.get("status", "unknown") if instance_data else "unknown"

    if current_status.lower() == "paused":
        # Resume to make it running
        print("  Instance is PAUSED, resuming first...")
        success, _ = resume_instance(instance_id, message=None)
        if success:
            time.sleep(1.0)
            instance_data = get_instance(instance_id)
            current_status = instance_data.get("status", "unknown") if instance_data else "unknown"

    is_running = current_status.lower() not in ("paused", "pausing")
    suite.add(
        "Test 3: Instance is RUNNING",
        is_running,
        f"current_status={current_status}"
    )
    if not is_running:
        # Try a few more times
        for _ in range(5):
            time.sleep(1.0)
            instance_data = get_instance(instance_id)
            current_status = instance_data.get("status", "unknown") if instance_data else "unknown"
            is_running = current_status.lower() not in ("paused", "pausing")
            if is_running:
                break

    # Get message count before
    messages_before = get_messages(instance_id)
    count_before = len(messages_before)
    print(f"  Messages before: {count_before}")

    # Step 2: Try to resume (should skip)
    print("\n  [3.2] Resume when already running (should skip)")
    success, resume_data = resume_instance(instance_id, message=None)
    if not success:
        suite.add("Test 3: Resume returns 200", False, f"Resume failed: {resume_data}")
        return False
    suite.add("Test 3: Resume returns 200", True, f"Response: {json.dumps(resume_data)}")

    # Step 3: Verify skipped_ids contains our instance
    skipped_ids = resume_data.get("skipped_ids", [])
    resumed_ids = resume_data.get("resumed_ids", [])

    suite.add(
        "Test 3: Instance in skipped_ids",
        instance_id in skipped_ids,
        f"skipped_ids={skipped_ids}, resumed_ids={resumed_ids}"
    )

    # Step 4: Verify NO message was enqueued (message_id should be None)
    message_id = resume_data.get("message_id")
    suite.add(
        "Test 3: message_id is None (no message enqueued)",
        message_id is None,
        f"message_id={message_id}"
    )

    # Step 5: Verify message count did NOT increase
    time.sleep(1.0)
    messages_after = get_messages(instance_id)
    count_after = len(messages_after)
    print(f"  Messages after: {count_after}")

    suite.add(
        "Test 3: Message count unchanged",
        count_after == count_before,
        f"count_before={count_before}, count_after={count_after}"
    )

    # Summary for Test 3
    test3_passed = all(r.passed for r in suite.results if r.name.startswith("Test 3:"))
    print(f"\n  Test 3 Summary: {'PASS' if test3_passed else 'FAIL'}")
    return test3_passed


def run_tests() -> bool:
    """Run all integration tests."""
    suite = TestSuite()

    # Pre-flight check
    print("\n--- Pre-flight: Health Check ---")
    if not check_health():
        print("ERROR: Dev server is not healthy at http://localhost:8079")
        suite.add("Server health check", False, "Server not responding")
        return False
    suite.add("Server health check", True)

    # Run Test 1
    test1_passed = run_test_1_default_message(suite)

    # Run Test 2 (uses same instance)
    test2_passed = run_test_2_custom_message(suite)

    # Run Test 3 (uses same instance)
    test3_passed = run_test_3_already_running(suite)

    # Cleanup
    suite.cleanup()

    # Summary
    passed, total = suite.summary()

    # Overall result
    overall_passed = test1_passed and test2_passed and test3_passed
    print(f"\n{'='*60}")
    print(f"OVERALL: {'PASS' if overall_passed else 'FAIL'}")
    print(f"{'='*60}")

    return overall_passed


def timeout_handler(signum, frame):
    """Handle script timeout."""
    print("\n[TIMEOUT] Script exceeded 5 minutes")
    sys.exit(1)


def main():
    """Main entry point."""
    print("=" * 60)
    print("Resume Redesign Mock Integration Test")
    print("Target: http://localhost:8079")
    print("=" * 60)

    # Set up timeout
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(TOTAL_TIMEOUT)

    try:
        success = run_tests()
        signal.alarm(0)  # Cancel alarm

        print("\n" + "=" * 60)
        if success:
            print("RESULT: PASS")
            print("=" * 60)
            return 0
        else:
            print("RESULT: FAIL")
            print("=" * 60)
            return 1
    except KeyboardInterrupt:
        print("\n[ABORTED] Test interrupted by user")
        return 1
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
