#!/usr/bin/env python3
"""
End-to-end test for verifying MCP tools availability after daemon restart.

This test proves that MCP tools survive a daemon restart for existing instances:
1. Starts the daemon via ./dev.sh, waits for healthy
2. Creates a developer instance, sends a message about MCP tools, verifies response
3. STOPS the daemon (simulating restart)
4. Starts daemon AGAIN, waits for healthy
5. Sends NEW message to SAME instance from step 2
6. Verifies LLM response STILL mentions MCP tools
7. Also checks GET /api/instances/{id} for mcp_tool_names field
8. Cleans up: terminates instance, stops daemon

Run with:
    python tests/e2e/test_mcp_tools_restore.py
    # or
    pytest tests/e2e/test_mcp_tools_restore.py -v -s
"""

import os
import sys
import time
import signal
import subprocess
import requests
import logging
import socket
from pathlib import Path
from typing import Optional

import pytest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Marked as integration: starts the daemon via ./dev.sh, makes real LLM calls,
# and exercises the MCP tools restore path across a daemon restart cycle.
# Excluded from the default non-integration test gate via the `integration`
# marker defined in pyproject.toml.
pytestmark = pytest.mark.integration

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEV_SCRIPT = PROJECT_ROOT / "dev.sh"
BASE_URL = "http://localhost:8079"  # Dev server port
API_BASE = f"{BASE_URL}/api"
LOG_FILE_1 = "/tmp/ens-restore-test-daemon1.log"
LOG_FILE_2 = "/tmp/ens-restore-test-daemon2.log"

# Timeouts
DAEMON_STARTUP_TIMEOUT = 120  # seconds - MCP servers need warmup
LLM_RESPONSE_TIMEOUT = 120  # seconds - LLM needs time to think
POLL_INTERVAL = 2  # seconds between message polling
PORT_RELEASE_TIMEOUT = 5  # seconds to wait for port to be freed

# Test results - Phase 1
phase1_results = {
    "daemon_started": False,
    "instance_created": False,
    "mcp_tools_in_api_phase1": False,
    "message_sent_phase1": False,
    "llm_response_received_phase1": False,
    "llm_mentions_mcp_phase1": False,
}

# Test results - Phase 2 (Restart)
phase2_results = {
    "daemon_stopped": False,
    "port_freed": False,
    "daemon_restarted": False,
    "daemon_healthy_after_restart": False,
}

# Test results - Phase 3
phase3_results = {
    "message_sent_phase3": False,
    "llm_response_received_phase3": False,
    "llm_mentions_mcp_phase3": False,
    "mcp_tools_in_api_phase3": False,
}

# Test results - Cleanup
cleanup_results = {
    "instance_terminated": False,
    "daemon_stopped": False,
}


def load_env():
    """Load environment variables from .env file."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key] = value
        logger.info("Loaded environment from .env")
    else:
        logger.warning(".env file not found")


def is_port_in_use(port: int) -> bool:
    """Check if a port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0


def wait_for_port_release(port: int, timeout: int = PORT_RELEASE_TIMEOUT) -> bool:
    """Wait for port to be freed."""
    logger.info(f"Waiting for port {port} to be freed...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        if not is_port_in_use(port):
            logger.info(f"Port {port} is now free")
            return True
        time.sleep(0.5)
    logger.error(f"Port {port} still in use after {timeout}s")
    return False


def kill_process_on_port(port: int) -> bool:
    """Kill any process using the specified port."""
    try:
        # Find PID using the port
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    logger.info(f"Killed process {pid} on port {port}")
                except ProcessLookupError:
                    pass
            time.sleep(1)
            return True
        return False
    except Exception as e:
        logger.warning(f"Error killing process on port: {e}")
        return False


def start_daemon(log_file: str = None) -> subprocess.Popen:
    """Start the daemon using ./dev.sh in background."""
    logger.info("=" * 60)
    logger.info("Starting daemon via ./dev.sh...")
    logger.info("=" * 60)

    if not DEV_SCRIPT.exists():
        raise FileNotFoundError(f"Dev script not found: {DEV_SCRIPT}")

    # Make sure it's executable
    DEV_SCRIPT.chmod(0o755)

    # Set up stdout/stderr redirection
    stdout_dest = open(log_file, "w") if log_file else subprocess.PIPE
    stderr_dest = subprocess.STDOUT if log_file else subprocess.PIPE

    # Start the daemon process
    process = subprocess.Popen(
        [str(DEV_SCRIPT)],
        stdout=stdout_dest,
        stderr=stderr_dest,
        cwd=str(PROJECT_ROOT),
        preexec_fn=os.setsid,  # Create new process group for clean cleanup
    )

    logger.info(f"Daemon started with PID: {process.pid}")
    return process


def wait_for_daemon(timeout: int = DAEMON_STARTUP_TIMEOUT) -> bool:
    """Wait for daemon to be healthy."""
    logger.info(f"Waiting for daemon to be ready (timeout: {timeout}s)...")

    start_time = time.time()
    last_error = None

    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{API_BASE}/health", timeout=5)
            if response.status_code == 200:
                elapsed = time.time() - start_time
                logger.info(f"Daemon is ready after {elapsed:.1f}s")
                logger.info(f"Health response: {response.json()}")
                return True
        except requests.exceptions.ConnectionError as e:
            last_error = e
        except requests.exceptions.RequestException as e:
            last_error = e

        time.sleep(POLL_INTERVAL)

    logger.error(f"Daemon failed to start within {timeout}s. Last error: {last_error}")
    return False


def stop_daemon(process: subprocess.Popen) -> bool:
    """Stop the daemon process gracefully."""
    logger.info("=" * 60)
    logger.info("Stopping daemon...")
    logger.info("=" * 60)

    if process:
        try:
            # Kill the entire process group with SIGTERM first
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=10)
                logger.info("Daemon stopped gracefully")
                return True
            except subprocess.TimeoutExpired:
                logger.warning("Daemon did not stop gracefully, forcing SIGKILL...")
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait(timeout=5)
                logger.info("Daemon killed with SIGKILL")
                return True
        except ProcessLookupError:
            logger.info("Daemon process already stopped")
            return True
        except Exception as e:
            logger.error(f"Error stopping daemon: {e}")
            return False
    return True


def spawn_instance(agent_id: str = "developer") -> Optional[str]:
    """Spawn a new instance via API."""
    logger.info("=" * 60)
    logger.info(f"Spawning {agent_id} instance...")
    logger.info("=" * 60)

    try:
        response = requests.post(
            f"{API_BASE}/instances",
            json={"agent_id": agent_id},
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        instance_id = data.get("instance_id")

        if instance_id:
            logger.info(f"Instance created with ID: {instance_id}")
            return instance_id
        else:
            logger.error(f"No instance_id in response: {data}")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to spawn instance: {e}")
        return None


def send_message(instance_id: str, content: str) -> Optional[str]:
    """Send a message to an instance."""
    logger.info("=" * 60)
    logger.info(f"Sending message to instance {instance_id}...")
    logger.info(f"Message: {content[:100]}...")
    logger.info("=" * 60)

    try:
        response = requests.post(
            f"{API_BASE}/instances/{instance_id}/messages",
            json={"content": content},
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        message_id = data.get("message_id")
        logger.info(f"Message queued with ID: {message_id}")
        return message_id

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send message: {e}")
        return None


def wait_for_response(instance_id: str, timeout: int = LLM_RESPONSE_TIMEOUT) -> Optional[dict]:
    """Poll for LLM response by checking message history."""
    logger.info(f"Waiting for LLM response (timeout: {timeout}s)...")

    start_time = time.time()
    last_message_count = 0

    while time.time() - start_time < timeout:
        try:
            # Get message history
            response = requests.get(
                f"{API_BASE}/instances/{instance_id}/messages",
                timeout=10,
            )
            response.raise_for_status()

            messages = response.json()

            if isinstance(messages, list) and len(messages) > 0:
                # Find the latest assistant message with content
                assistant_messages = [
                    m for m in messages
                    if m.get("role") == "assistant" and m.get("content")
                ]

                if assistant_messages:
                    latest = assistant_messages[-1]
                    content = latest.get("content", "")

                    if content and len(content) > 10:  # Reasonable response length
                        elapsed = time.time() - start_time
                        logger.info(f"Response received after {elapsed:.1f}s")
                        logger.info(f"Response length: {len(content)} chars")
                        return latest

            # Check if instance is still running
            try:
                instance_response = requests.get(
                    f"{API_BASE}/instances/{instance_id}",
                    timeout=10,
                )
                if instance_response.status_code == 200:
                    instance_data = instance_response.json()
                    status = instance_data.get("status")
                    if status in ["terminated", "error", "completed"]:
                        logger.warning(f"Instance status changed to: {status}")
                        break
            except requests.exceptions.RequestException:
                pass  # Instance might not exist yet after restart

        except requests.exceptions.RequestException as e:
            logger.warning(f"Error polling messages: {e}")

        time.sleep(POLL_INTERVAL)

    logger.warning("Timeout waiting for LLM response")
    return None


def verify_mcp_tools_via_api(instance_id: str) -> tuple[bool, list[str]]:
    """Verify MCP tools are available via direct API call.

    Args:
        instance_id: The instance ID to verify.

    Returns:
        Tuple of (found: bool, tool_names: list[str]).
    """
    logger.info("=" * 60)
    logger.info(f"Verifying MCP tools via API for instance {instance_id}...")
    logger.info("=" * 60)

    try:
        response = requests.get(
            f"{API_BASE}/instances/{instance_id}",
            timeout=10,
        )
        response.raise_for_status()

        data = response.json()
        logger.info(f"Instance API response keys: {list(data.keys())}")
        logger.info(f"Instance status: {data.get('status')}")

        # Check for MCP tool names in the response
        mcp_tool_names = data.get("mcp_tool_names")

        if mcp_tool_names and isinstance(mcp_tool_names, list):
            logger.info(f"Found {len(mcp_tool_names)} MCP tool names in API response:")
            for tool_name in mcp_tool_names:
                logger.info(f"  - {tool_name}")

            # Filter for tools starting with mcp_ prefix
            mcp_prefixed = [t for t in mcp_tool_names if t.startswith("mcp_")]
            if mcp_prefixed:
                logger.info(f"  Tools with 'mcp_' prefix: {mcp_prefixed}")

            return True, mcp_tool_names
        else:
            logger.warning("No 'mcp_tool_names' field found in API response")
            logger.info(f"Full response data: {data}")
            return False, []

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to verify MCP tools via API: {e}")
        return False, []


def terminate_instance(instance_id: str) -> bool:
    """Terminate an instance."""
    logger.info(f"Terminating instance {instance_id}...")

    try:
        response = requests.delete(
            f"{API_BASE}/instances/{instance_id}",
            timeout=30,
        )
        response.raise_for_status()

        logger.info("Instance terminated successfully")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to terminate instance: {e}")
        return False


def verify_response_mentions_mcp(response_content: str) -> tuple[bool, list[str]]:
    """Check if the response mentions MCP tools."""
    content_lower = response_content.lower()

    # Common MCP tool indicators
    mcp_indicators = [
        "webfetch",
        "context7",
        "mcp_",
        "mcp tool",
        "model context protocol",
        "mcp server",
        "fetch_url",
        "get_context",
        "retrieve_context",
    ]

    found = []
    for indicator in mcp_indicators:
        if indicator.lower() in content_lower:
            found.append(indicator)

    return len(found) > 0, found


def print_results() -> bool:
    """Print test results summary."""
    print()
    print("=" * 60)
    print("E2E TEST RESULTS: MCP Tools After Daemon Restart")
    print("=" * 60)

    all_results = {
        "Phase 1 - Initial Start": phase1_results,
        "Phase 2 - Restart": phase2_results,
        "Phase 3 - Restore Verification": phase3_results,
        "Cleanup": cleanup_results,
    }

    total_passed = 0
    total_failed = 0

    for phase_name, results in all_results.items():
        print()
        print(f"--- {phase_name} ---")
        for name, result in results.items():
            status = "PASS" if result else "FAIL"
            symbol = "✅" if result else "❌"
            display_name = name.replace("_", " ").title()
            print(f"  {symbol} {display_name}: {status}")
            if result:
                total_passed += 1
            else:
                total_failed += 1

    print()
    print("=" * 60)
    print(f"TOTAL: {total_passed} passed, {total_failed} failed")
    print("=" * 60)

    if total_failed == 0:
        print("🎉 ALL TESTS PASSED - MCP tools survive daemon restart!")
    else:
        print("⚠️  SOME TESTS FAILED - MCP tools do NOT survive daemon restart")

    return total_failed == 0


def main():
    """Main test function."""
    print()
    print("=" * 60)
    print("E2E TEST: MCP Tools Persistence After Daemon Restart")
    print("=" * 60)
    print()

    # Load environment
    load_env()

    # Check for API key
    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ ERROR: OPENAI_API_KEY is not set")
        print("   Please set it in .env or environment")
        sys.exit(1)

    daemon_process = None
    instance_id = None

    try:
        # =========================================================
        # PHASE 1: Initial Start
        # =========================================================
        print()
        print("=" * 60)
        print("PHASE 1: Initial Daemon Start")
        print("=" * 60)

        # Kill any existing process on port 8079
        kill_process_on_port(8079)
        time.sleep(1)

        # Start daemon
        daemon_process = start_daemon(LOG_FILE_1)
        phase1_results["daemon_started"] = wait_for_daemon()

        if not phase1_results["daemon_started"]:
            print("❌ FAILED: Could not start daemon")
            sys.exit(1)

        # Create instance
        instance_id = spawn_instance("developer")
        phase1_results["instance_created"] = instance_id is not None

        if not phase1_results["instance_created"]:
            print("❌ FAILED: Could not create instance")
            sys.exit(1)

        # CRITICAL: Save instance_id - we'll use this after restart
        saved_instance_id = instance_id
        print(f"✅ CRITICAL: Saved instance_id for restore test: {saved_instance_id}")

        # Verify MCP tools via API (Phase 1)
        mcp_tools_found_p1, mcp_tool_names_p1 = verify_mcp_tools_via_api(instance_id)
        phase1_results["mcp_tools_in_api_phase1"] = mcp_tools_found_p1

        # Send message (Phase 1)
        message_content_p1 = (
            "What MCP tools do you have available? "
            "List all tools you can use, especially any tools related to "
            "web fetching, context retrieval, or MCP (Model Context Protocol) servers."
        )

        message_id_p1 = send_message(instance_id, message_content_p1)
        phase1_results["message_sent_phase1"] = message_id_p1 is not None

        # Wait for response (Phase 1)
        response_p1 = wait_for_response(instance_id)

        if response_p1:
            phase1_results["llm_response_received_phase1"] = True

            content_p1 = response_p1.get("content", "")
            print()
            print("=" * 60)
            print("PHASE 1 - LLM RESPONSE:")
            print("=" * 60)
            print(content_p1[:1000] + "..." if len(content_p1) > 1000 else content_p1)
            print("=" * 60)

            mentions_mcp_p1, found_indicators_p1 = verify_response_mentions_mcp(content_p1)
            phase1_results["llm_mentions_mcp_phase1"] = mentions_mcp_p1

            if mentions_mcp_p1:
                print(f"✅ Phase 1: LLM mentioned MCP-related tools: {found_indicators_p1}")
            else:
                print("⚠️  Phase 1: LLM did not mention MCP tools in response")
        else:
            print("⚠️  Phase 1: No response received from LLM")

        # =========================================================
        # PHASE 2: Daemon Restart
        # =========================================================
        print()
        print("=" * 60)
        print("PHASE 2: Daemon Restart (Simulating Crash/Restart)")
        print("=" * 60)

        # Stop daemon
        phase2_results["daemon_stopped"] = stop_daemon(daemon_process)
        daemon_process = None

        # Wait for port to be freed
        phase2_results["port_freed"] = wait_for_port_release(8079)

        if not phase2_results["port_freed"]:
            print("⚠️  WARNING: Port 8079 may still be in use, attempting to start anyway...")

        # Small delay to ensure clean state
        time.sleep(2)

        # Start daemon again
        daemon_process = start_daemon(LOG_FILE_2)
        phase2_results["daemon_restarted"] = True

        # Wait for health
        phase2_results["daemon_healthy_after_restart"] = wait_for_daemon()

        if not phase2_results["daemon_healthy_after_restart"]:
            print("❌ FAILED: Daemon did not become healthy after restart")
            sys.exit(1)

        # =========================================================
        # PHASE 3: Restore Verification
        # =========================================================
        print()
        print("=" * 60)
        print("PHASE 3: Verify MCP Tools After Restart (SAME INSTANCE)")
        print("=" * 60)
        print(f"Using saved instance_id: {saved_instance_id}")

        # Verify MCP tools via API (Phase 3) - THE CRITICAL TEST
        mcp_tools_found_p3, mcp_tool_names_p3 = verify_mcp_tools_via_api(saved_instance_id)
        phase3_results["mcp_tools_in_api_phase3"] = mcp_tools_found_p3

        # Send NEW message to the SAME instance
        message_content_p3 = (
            "Can you list your MCP tools now? "
            "What MCP tools are available to you? "
            "Please list any tools related to MCP (Model Context Protocol)."
        )

        message_id_p3 = send_message(saved_instance_id, message_content_p3)
        phase3_results["message_sent_phase3"] = message_id_p3 is not None

        # Wait for response (Phase 3)
        response_p3 = wait_for_response(saved_instance_id)

        if response_p3:
            phase3_results["llm_response_received_phase3"] = True

            content_p3 = response_p3.get("content", "")
            print()
            print("=" * 60)
            print("PHASE 3 - LLM RESPONSE (AFTER RESTART):")
            print("=" * 60)
            print(content_p3[:1000] + "..." if len(content_p3) > 1000 else content_p3)
            print("=" * 60)

            mentions_mcp_p3, found_indicators_p3 = verify_response_mentions_mcp(content_p3)
            phase3_results["llm_mentions_mcp_phase3"] = mentions_mcp_p3

            if mentions_mcp_p3:
                print(f"✅ Phase 3: LLM mentioned MCP-related tools: {found_indicators_p3}")
                print("🎉 SUCCESS: MCP tools SURVIVED the daemon restart!")
            else:
                print("❌ Phase 3: LLM did NOT mention MCP tools after restart")
                print("   FAILURE: MCP tools did NOT survive the daemon restart!")
        else:
            print("❌ Phase 3: No response received from LLM after restart")
            print("   (Instance may have been terminated on restart)")

    except KeyboardInterrupt:
        print("\n⚠️  Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        logger.exception("Test error")
    finally:
        # =========================================================
        # Cleanup
        # =========================================================
        print()
        print("=" * 60)
        print("CLEANUP")
        print("=" * 60)

        # Terminate instance
        if instance_id:
            cleanup_results["instance_terminated"] = terminate_instance(instance_id)
        else:
            cleanup_results["instance_terminated"] = True

        # Stop daemon
        if daemon_process:
            cleanup_results["daemon_stopped"] = stop_daemon(daemon_process)
        else:
            cleanup_results["daemon_stopped"] = True

        # Clean up log files
        for log_file in [LOG_FILE_1, LOG_FILE_2]:
            try:
                if os.path.exists(log_file):
                    os.remove(log_file)
                    logger.info(f"Removed log file: {log_file}")
            except Exception as e:
                logger.warning(f"Could not remove log file {log_file}: {e}")

        # Print final results
        success = print_results()

        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
