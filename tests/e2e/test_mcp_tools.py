#!/usr/bin/env python3
"""
End-to-end test for verifying MCP tools availability to LLM instances.

This test:
1. Starts the daemon using ./dev.sh in background
2. Waits for the daemon to be healthy
3. Spawns a coder instance via API
4. Sends a message asking about available MCP tools
5. Waits for and retrieves the LLM response
6. Verifies MCP tools are available:
   - Checks if the LLM mentions MCP tools (webfetch, context7, etc.)
   - Verifies MCP servers are configured via API
7. Prints clear pass/fail results
8. Cleans up: terminates instance and stops daemon

Run with:
    python tests/e2e/test_mcp_tools.py
    # or
    pytest tests/e2e/test_mcp_tools.py -v -s
"""

import os
import sys
import time
import signal
import subprocess
import requests
import logging
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

# Marked as integration: starts the daemon via ./dev.sh and makes real LLM
# calls to verify MCP tool availability. Excluded from the default
# non-integration test gate via the `integration` marker defined in
# pyproject.toml.
pytestmark = pytest.mark.integration

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEV_SCRIPT = PROJECT_ROOT / "dev.sh"
BASE_URL = "http://localhost:8079"  # Dev server port
API_BASE = f"{BASE_URL}/api"

# Timeouts
DAEMON_STARTUP_TIMEOUT = 120  # seconds - MCP servers need warmup
LLM_RESPONSE_TIMEOUT = 120  # seconds - LLM needs time to think
POLL_INTERVAL = 2  # seconds between message polling

# Test results
test_results = {
    "daemon_started": False,
    "instance_created": False,
    "mcp_tools_in_api": False,
    "message_sent": False,
    "llm_response_received": False,
    "llm_mentions_mcp": False,
    "mcp_servers_configured": False,
    "cleanup_completed": False,
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


def start_daemon() -> subprocess.Popen:
    """Start the daemon using ./dev.sh in background."""
    logger.info("=" * 60)
    logger.info("Starting daemon via ./dev.sh...")
    logger.info("=" * 60)

    if not DEV_SCRIPT.exists():
        raise FileNotFoundError(f"Dev script not found: {DEV_SCRIPT}")

    # Make sure it's executable
    DEV_SCRIPT.chmod(0o755)

    # Start the daemon process
    process = subprocess.Popen(
        [str(DEV_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def spawn_instance(agent_id: str = "coder") -> Optional[str]:
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

        except requests.exceptions.RequestException as e:
            logger.warning(f"Error polling messages: {e}")

        time.sleep(POLL_INTERVAL)

    logger.warning("Timeout waiting for LLM response")
    return None


def check_mcp_servers() -> list:
    """Check configured MCP servers via API."""
    logger.info("=" * 60)
    logger.info("Checking MCP servers configuration...")
    logger.info("=" * 60)

    try:
        response = requests.get(f"{API_BASE}/mcp-servers", timeout=10)
        response.raise_for_status()

        data = response.json()
        servers = data.get("mcp_servers", [])

        logger.info(f"Found {len(servers)} MCP servers configured")

        for server in servers:
            name = server.get("name", "unknown")
            is_active = server.get("is_active", False)
            logger.info(f"  - {name} (active: {is_active})")

        return servers

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to check MCP servers: {e}")
        return []


def verify_mcp_tools_via_api(instance_id: str) -> tuple[bool, list[str]]:
    """Verify MCP tools are available via direct API call.

    This is the PRIMARY verification step - it checks the API response directly
    rather than relying on LLM interpretation.

    Args:
        instance_id: The instance ID to verify.

    Returns:
        Tuple of (found: bool, tool_names: list[str]).
    """
    logger.info("=" * 60)
    logger.info(f"Verifying MCP tools via API for instance {instance_id}...")
    logger.info("=" * 60)

    try:
        # Call GET /api/instances/{instance_id}
        response = requests.get(
            f"{API_BASE}/instances/{instance_id}",
            timeout=10,
        )
        response.raise_for_status()

        data = response.json()
        logger.info(f"Instance API response keys: {list(data.keys())}")

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


def stop_daemon(process: subprocess.Popen) -> None:
    """Stop the daemon process."""
    logger.info("=" * 60)
    logger.info("Stopping daemon...")
    logger.info("=" * 60)

    if process:
        try:
            # Kill the entire process group
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=10)
            logger.info("Daemon stopped successfully")
        except subprocess.TimeoutExpired:
            logger.warning("Daemon did not stop gracefully, forcing...")
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            logger.info("Daemon process already stopped")
        except Exception as e:
            logger.error(f"Error stopping daemon: {e}")


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


def print_results():
    """Print test results summary."""
    print()
    print("=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)

    checks = [
        ("1. Daemon Started", test_results["daemon_started"]),
        ("2. Instance Created", test_results["instance_created"]),
        ("3. MCP Tools in API Response", test_results["mcp_tools_in_api"]),
        ("4. Message Sent", test_results["message_sent"]),
        ("5. LLM Response Received", test_results["llm_response_received"]),
        ("6. LLM Mentions MCP Tools", test_results["llm_mentions_mcp"]),
        ("7. MCP Servers Configured", test_results["mcp_servers_configured"]),
        ("8. Cleanup Completed", test_results["cleanup_completed"]),
    ]

    passed = 0
    failed = 0

    for name, result in checks:
        status = "PASS" if result else "FAIL"
        symbol = "✅" if result else "❌"
        print(f"{symbol} {name}: {status}")
        if result:
            passed += 1
        else:
            failed += 1

    print()
    print("=" * 60)
    print(f"TOTAL: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed == 0:
        print("🎉 ALL TESTS PASSED - MCP tools are available!")
    else:
        print("⚠️  SOME TESTS FAILED - Check the output above for details")

    return failed == 0


def main():
    """Main test function."""
    print()
    print("=" * 60)
    print("E2E TEST: MCP Tools Availability Verification")
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
        # Step 1: Start daemon
        daemon_process = start_daemon()
        test_results["daemon_started"] = wait_for_daemon()

        if not test_results["daemon_started"]:
            print("❌ FAILED: Could not start daemon")
            sys.exit(1)

        # Step 2: Check MCP servers configuration
        servers = check_mcp_servers()
        test_results["mcp_servers_configured"] = len(servers) > 0

        if not test_results["mcp_servers_configured"]:
            print("⚠️  WARNING: No MCP servers configured - test may not be meaningful")
            print("   (Some MCP servers may be built-in and not show in API)")

        # Step 3: Spawn instance
        instance_id = spawn_instance("coder")
        test_results["instance_created"] = instance_id is not None

        if not test_results["instance_created"]:
            print("❌ FAILED: Could not create instance")
            sys.exit(1)

        # Step 4: Verify MCP tools via direct API call (PRIMARY verification)
        # This is called BEFORE the LLM message to ensure MCP tools are loaded
        mcp_tools_found, mcp_tool_names = verify_mcp_tools_via_api(instance_id)
        test_results["mcp_tools_in_api"] = mcp_tools_found

        if not mcp_tools_found:
            print("⚠️  WARNING: No MCP tools found in API response")
            print("   (Some MCP servers may be configured but not loaded)")

        # Step 5: Send message asking about MCP tools
        message_content = (
            "What MCP tools do you have available? "
            "List all tools you can use, especially any tools related to "
            "web fetching, context retrieval, or MCP (Model Context Protocol) servers."
        )

        message_id = send_message(instance_id, message_content)
        test_results["message_sent"] = message_id is not None

        if not test_results["message_sent"]:
            print("❌ FAILED: Could not send message")
            sys.exit(1)

        # Step 6: Wait for response
        response = wait_for_response(instance_id)

        if response:
            test_results["llm_response_received"] = True

            # Step 7: Verify response mentions MCP tools (SECONDARY verification)
            content = response.get("content", "")
            print()
            print("=" * 60)
            print("LLM RESPONSE:")
            print("=" * 60)
            print(content[:1000] + "..." if len(content) > 1000 else content)
            print("=" * 60)

            mentions_mcp, found_indicators = verify_response_mentions_mcp(content)
            test_results["llm_mentions_mcp"] = mentions_mcp

            if mentions_mcp:
                print(f"✅ LLM mentioned MCP-related tools: {found_indicators}")
            else:
                print("⚠️  LLM did not mention MCP tools in response")
                print("   (This may be expected if no MCP servers are configured)")
        else:
            print("⚠️  No response received from LLM")
            print("   (Check daemon logs for errors)")

    except KeyboardInterrupt:
        print("\n⚠️  Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        logger.exception("Test error")
    finally:
        # Cleanup
        print()
        print("=" * 60)
        print("CLEANUP")
        print("=" * 60)

        if instance_id:
            test_results["cleanup_completed"] = terminate_instance(instance_id)
        else:
            test_results["cleanup_completed"] = True

        if daemon_process:
            stop_daemon(daemon_process)

        # Print final results
        success = print_results()

        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
