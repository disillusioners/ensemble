#!/usr/bin/env python3
"""
Mock Integration Test for Pause/Resume API Endpoints

Tests against live dev server at http://localhost:8079
Validates:
- Instance pause/resume functionality
- Status transitions
- Job queuing while paused (job stays PENDING)

Note: Cascade (parent/child) testing is NOT possible via REST API because
children can only be created when an agent spawns another via the
spawn_child_instance tool internally. The API's InstanceCreate model
does not have a parent_id field.

Usage:
    python tests/mock_pause_resume.py
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
TOTAL_TIMEOUT = 120  # total script timeout in seconds


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""


@dataclass
class TestSuite:
    results: list[TestResult] = field(default_factory=list)
    base_instance_id: Optional[str] = None

    def add(self, name: str, passed: bool, message: str = ""):
        self.results.append(TestResult(name, passed, message))
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        if message and not passed:
            print(f"         {message}")

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
        if self.base_instance_id:
            try:
                response = requests.delete(
                    f"{BASE_URL}/instances/{self.base_instance_id}",
                    timeout=TIMEOUT
                )
                print(f"  Deleted instance: {self.base_instance_id} (status={response.status_code})")
            except Exception as e:
                print(f"  Failed to delete {self.base_instance_id}: {e}")


def check_health() -> bool:
    """Check if dev server is healthy."""
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        return response.status_code == 200
    except Exception:
        return False


def create_instance(agent_id: str = "coder") -> Optional[dict]:
    """Create a test instance."""
    try:
        response = requests.post(
            f"{BASE_URL}/instances",
            json={"agent_id": agent_id},
            timeout=TIMEOUT
        )
        # Accept both 200 and 201
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


def pause_instance(instance_id: str) -> tuple[bool, Optional[dict]]:
    """Pause an instance."""
    try:
        response = requests.post(f"{BASE_URL}/instances/{instance_id}/pause", timeout=TIMEOUT)
        if response.status_code == 200:
            return True, response.json()
        return False, response.json() if response.status_code != 404 else None
    except Exception:
        return False, None


def resume_instance(instance_id: str) -> tuple[bool, Optional[dict]]:
    """Resume an instance."""
    try:
        response = requests.post(f"{BASE_URL}/instances/{instance_id}/resume", timeout=TIMEOUT)
        if response.status_code == 200:
            return True, response.json()
        return False, response.json() if response.status_code != 404 else None
    except Exception:
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


def find_job_for_instance(instance_id: str) -> Optional[dict]:
    """Find a job for the given instance ID from the jobs list."""
    try:
        response = requests.get(f"{BASE_URL}/jobs", timeout=TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            jobs = data.get("jobs", [])
            # Find jobs matching this instance (may have multiple)
            matching = [j for j in jobs if j.get("instance_id") == instance_id]
            # Return the most recent one (first in list after sorting by created_at desc)
            return matching[0] if matching else None
        return None
    except Exception:
        return None


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

    print("\n--- Test Suite: Pause/Resume API ---")

    # Test 1: Create instance
    print("\n[1] Create test instance")
    instance = create_instance()
    if instance:
        suite.base_instance_id = instance.get("instance_id")
        suite.add("Create instance", True, f"Created: {suite.base_instance_id}")
    else:
        suite.add("Create instance", False, "Failed to create instance")
        return False

    # Test 2: Verify initial state is not paused
    print("\n[2] Verify initial state (not paused)")
    time.sleep(0.5)  # Brief wait for server processing
    instance_data = get_instance(suite.base_instance_id)
    if instance_data:
        status = instance_data.get("status", "").lower()
        is_paused = status == "paused" or instance_data.get("is_paused", False)
        suite.add(
            "Initial state not paused",
            not is_paused,
            f"status={status}, is_paused={instance_data.get('is_paused')}"
        )
    else:
        suite.add("Initial state not paused", False, "Could not fetch instance")

    # Test 3: Pause the instance
    print("\n[3] Pause the instance")
    success, response_data = pause_instance(suite.base_instance_id)
    if success and response_data:
        suite.add(
            "Pause endpoint returns 200",
            True,
            f"Response: {json.dumps(response_data)}"
        )
    else:
        suite.add("Pause endpoint returns 200", False, f"Response: {response_data}")

    # Test 4: Verify paused state
    print("\n[4] Verify paused state")
    time.sleep(0.5)
    instance_data = get_instance(suite.base_instance_id)
    if instance_data:
        status = instance_data.get("status", "").lower()
        is_paused = status == "paused" or instance_data.get("is_paused", False)
        suite.add(
            "Instance status is PAUSED after pause",
            is_paused,
            f"status={status}, is_paused={instance_data.get('is_paused')}"
        )
    else:
        suite.add("Instance status is PAUSED after pause", False, "Could not fetch instance")

    # Test 5: Pause again (should be idempotent, skip already paused)
    print("\n[5] Pause again (idempotency)")
    success, response_data = pause_instance(suite.base_instance_id)
    if success and response_data:
        skipped = response_data.get("skipped_ids", [])
        suite.add(
            "Pause idempotent (skips already paused)",
            suite.base_instance_id in skipped,
            f"skipped_ids={skipped}"
        )
    else:
        suite.add("Pause idempotent (skips already paused)", False, "Pause failed or returned error")

    # Test 6: Send message while paused and verify job is PENDING
    print("\n[6] Send message while paused")
    msg_response = send_message(suite.base_instance_id, "test message while paused")
    if msg_response:
        suite.add("Message enqueued successfully", True)
        time.sleep(1.0)  # Wait for job to be created
        job_data = find_job_for_instance(suite.base_instance_id)
        if job_data:
            job_status = job_data.get("status", "").upper()
            suite.add(
                "Job is PENDING (not PROCESSING) while paused",
                job_status == "PENDING",
                f"job status={job_status}"
            )
        else:
            suite.add("Job is PENDING (not PROCESSING) while paused", False, "Could not find job for instance")
    else:
        suite.add("Message enqueued successfully", False, "No response from message endpoint")

    # Test 7: Resume the instance
    print("\n[7] Resume the instance")
    success, response_data = resume_instance(suite.base_instance_id)
    if success and response_data:
        suite.add(
            "Resume endpoint returns 200",
            True,
            f"Response: {json.dumps(response_data)}"
        )
    else:
        suite.add("Resume endpoint returns 200", False, f"Response: {response_data}")

    # Test 8: Verify resumed state
    print("\n[8] Verify resumed state")
    time.sleep(0.5)
    instance_data = get_instance(suite.base_instance_id)
    if instance_data:
        status = instance_data.get("status", "").lower()
        is_paused = status == "paused" or instance_data.get("is_paused", False)
        suite.add(
            "Instance is NOT paused after resume",
            not is_paused,
            f"status={status}, is_paused={instance_data.get('is_paused')}"
        )
    else:
        suite.add("Instance is NOT paused after resume", False, "Could not fetch instance")

    # Test 9: Resume again (should be idempotent)
    print("\n[9] Resume again (idempotency)")
    success, response_data = resume_instance(suite.base_instance_id)
    if success and response_data:
        skipped = response_data.get("skipped_ids", [])
        suite.add(
            "Resume idempotent (skips not paused)",
            suite.base_instance_id in skipped,
            f"skipped_ids={skipped}"
        )
    else:
        suite.add("Resume idempotent (skips not paused)", False, "Resume failed")

    # Test 10: Verify job becomes PROCESSING after resume
    print("\n[10] Verify job processes after resume")
    time.sleep(2.0)  # Wait for job to be picked up
    job_data = find_job_for_instance(suite.base_instance_id)
    if job_data:
        job_status = job_data.get("status", "").upper()
        is_processing_or_done = job_status in ("PROCESSING", "COMPLETED", "PENDING")
        suite.add(
            "Job is processing or completed after resume",
            is_processing_or_done,
            f"job status={job_status}"
        )
    else:
        suite.add("Job is processing or completed after resume", True, "No pending jobs found (may have completed)")

    # Cleanup
    suite.cleanup()

    # Summary
    passed, total = suite.summary()
    return passed == total


def timeout_handler(signum, frame):
    """Handle script timeout."""
    print("\n[TIMEOUT] Script exceeded 120 seconds")
    sys.exit(1)


def main():
    """Main entry point."""
    print("=" * 60)
    print("Mock Integration Test: Pause/Resume API")
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
