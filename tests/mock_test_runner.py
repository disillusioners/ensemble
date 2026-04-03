#!/usr/bin/env python3
"""
Mock Test Script - Tests the ensemble daemon with a mock LLM server.
Flow: Start mock server → Start daemon with mock upstream → Create instance → Send message → Wait for response.
"""

import subprocess
import time
import sys
import os
import signal
import json
import argparse
from typing import Generator

import httpx

# Add parent dir for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configuration
DAEMON_HOST = os.getenv("DAEMON_HOST", "localhost")
DAEMON_PORT = int(os.getenv("DAEMON_PORT", "8078"))
DAEMON_BASE_URL = f"http://{DAEMON_HOST}:{DAEMON_PORT}/api"


class MockTestRunner:
    def __init__(self, mock_host="0.0.0.0", mock_port=4124, daemon_host="localhost", daemon_port=8078):
        self.mock_server_process = None
        self.daemon_process = None
        self.mock_host = mock_host
        self.mock_port = mock_port
        self.mock_base_url = f"http://{mock_host}:{mock_port}/v1"
        self.daemon_host = daemon_host
        self.daemon_port = daemon_port
        self.daemon_base_url = f"http://{daemon_host}:{daemon_port}/api"

    def log(self, msg: str, level: str = "INFO"):
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {msg}")

    def wait_for_server(self, url: str, name: str, timeout: int = 30) -> bool:
        """Wait for a server to become healthy."""
        start = time.time()
        client = httpx.Client(timeout=2, verify=False)
        while time.time() - start < timeout:
            try:
                resp = client.get(f"{url}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    self.log(f"{name} is ready - {data}")
                    client.close()
                    return True
            except httpx.RequestError:
                pass
            time.sleep(0.5)
        client.close()
        self.log(f"{name} failed to start within {timeout}s", "ERROR")
        raise TimeoutError(f"{name} did not start in time")

    def start_mock_server(self) -> None:
        """Start the mock LLM server."""
        self.log("Starting Mock LLM Server...")
        mock_server_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "mock_llm_server.py"
        )

        self.mock_server_process = subprocess.Popen(
            [sys.executable, mock_server_path, "--host", self.mock_host, "--port", str(self.mock_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "MOCK_HOST": self.mock_host, "MOCK_PORT": str(self.mock_port)},
        )

        # Wait for mock server to be ready
        if not self.wait_for_server(
            f"http://{self.mock_host}:{self.mock_port}", "Mock LLM Server"
        ):
            raise TimeoutError("Mock LLM Server failed to start")

        self.log(f"Mock LLM Server running at {self.mock_base_url}")

    def start_daemon(self) -> None:
        """Start the ensemble daemon with overridden upstream URL."""
        self.log("Starting Ensemble Daemon...")

        # Build environment with overridden upstream URL
        env = {**os.environ}
        env["OPENAI_BASE_URL"] = self.mock_base_url
        env["OPENAI_API_KEY"] = "mock-api-key"
        env["HOST"] = self.daemon_host
        env["PORT"] = str(self.daemon_port)

        self.daemon_process = subprocess.Popen(
            [sys.executable, "-m", "daemon"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )

        # Wait for daemon to be ready
        if not self.wait_for_server(self.daemon_base_url, "Ensemble Daemon"):
            raise TimeoutError("Ensemble Daemon failed to start")

        self.log(f"Ensemble Daemon running at {self.daemon_base_url}")
        self.log(f"Upstream URL overridden to: {self.mock_base_url}")

    def create_instance(self, agent_dir: str = "default", title: str = "Mock Test Instance") -> dict:
        """Create a new instance."""
        self.log(f"Creating instance: agent={agent_dir}, title={title}")
        client = httpx.Client(timeout=10, verify=False)
        try:
            resp = client.post(
                f"{self.daemon_base_url}/instances",
                json={
                    "agent_dir": agent_dir,
                    "title": title,
                },
            )
            resp.raise_for_status()
            instance = resp.json()
            self.log(f"Instance created: {instance['instance_id']}")
            return instance
        finally:
            client.close()

    def send_message(self, instance_id: str, content: str) -> dict:
        """Send a message to an instance."""
        self.log(f"Sending message to instance {instance_id}: {content[:50]}...")
        client = httpx.Client(timeout=10, verify=False)
        try:
            resp = client.post(
                f"{self.daemon_base_url}/instances/{instance_id}/messages",
                json={"content": content},
            )
            resp.raise_for_status()
            result = resp.json()
            self.log(f"Message queued: {result.get('message_id')}")
            return result
        finally:
            client.close()

    def stream_events(self, instance_id: str, timeout: int = 30) -> Generator[dict, None, None]:
        """Stream SSE events from an instance."""
        self.log(f"Streaming events for instance {instance_id}...")
        with httpx.stream(
            "GET",
            f"{self.daemon_base_url}/instances/{instance_id}/events",
            timeout=timeout,
            verify=False,
        ) as resp:
            resp.raise_for_status()
            event_type = None
            data_buffer = ""

            for line in resp.iter_lines():
                decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                if decoded.startswith("event: "):
                    event_type = decoded[7:].strip()
                elif decoded.startswith("data: "):
                    data_buffer = decoded[6:].strip()
                elif decoded == "":
                    if data_buffer:
                        try:
                            data = json.loads(data_buffer)
                            yield {"type": event_type, "data": data}
                        except json.JSONDecodeError:
                            self.log(f"Failed to parse SSE data: {data_buffer}", "WARN")
                    event_type = None
                    data_buffer = ""

    def wait_for_completion(self, instance_id: str, timeout: int = 60) -> list[dict]:
        """Wait for the message to be processed and return all events."""
        events = []
        start = time.time()

        try:
            for raw_event in self.stream_events(instance_id, timeout):
                event_type = raw_event.get("type", "unknown")
                data = raw_event.get("data", {})
                self.log(f"Event: {event_type} - {data}", "DEBUG")
                events.append(raw_event)

                if event_type == "completed":
                    elapsed = time.time() - start
                    self.log(f"Completion event received after {elapsed:.1f}s")
                    return events
                elif event_type == "error":
                    self.log(f"Error event: {data}", "ERROR")
                    return events

                if time.time() - start > timeout:
                    self.log(f"Timeout waiting for completion ({timeout}s)", "ERROR")
                    break
        except httpx.ReadTimeout:
            self.log(f"Timeout reading from stream ({timeout}s)", "ERROR")

        return events

    def get_messages(self, instance_id: str) -> list[dict]:
        """Get all messages for an instance."""
        client = httpx.Client(timeout=10, verify=False)
        try:
            resp = client.get(f"{self.daemon_base_url}/instances/{instance_id}/messages")
            resp.raise_for_status()
            return resp.json()
        finally:
            client.close()

    def get_mock_stats(self) -> dict:
        """Get mock server statistics."""
        client = httpx.Client(timeout=5, verify=False)
        try:
            resp = client.get(f"http://{self.mock_host}:{self.mock_port}/stats")
            return resp.json()
        except Exception as e:
            return {"error": str(e)}
        finally:
            client.close()

    def run_test(self, test_message: str = "Hello, this is a test message!") -> bool:
        """Run the full test flow."""
        try:
            # Start servers
            self.start_mock_server()
            self.start_daemon()

            # Print mock stats before
            self.log("Mock server stats (before): " + str(self.get_mock_stats()))

            # Create instance
            instance = self.create_instance(title="Mock LLM Test Instance")
            instance_id = instance["instance_id"]

            # Send message
            self.send_message(instance_id, test_message)

            # Wait for completion
            self.log("Waiting for message processing...")
            events = self.wait_for_completion(instance_id, timeout=60)

            # Get final messages
            messages = self.get_messages(instance_id)
            self.log(f"Final message count: {len(messages)}")

            # Print results
            self.log("\n" + "=" * 60)
            self.log("TEST RESULTS")
            self.log("=" * 60)
            self.log(f"Instance ID: {instance_id}")
            self.log(f"Events received: {len(events)}")

            for msg in messages:
                role = msg.get("type", "unknown")
                content = msg.get("content", "")
                self.log(f"  [{role}]: {content[:100]}...")

            # Print mock stats after
            self.log("Mock server stats (after): " + str(self.get_mock_stats()))

            # Check for completion
            completion_events = [e for e in events if e["type"] == "completed"]
            error_events = [e for e in events if e["type"] == "error"]

            if completion_events:
                self.log("\n✅ TEST PASSED - Message processed successfully")
                return True
            elif error_events:
                self.log("\n❌ TEST FAILED - Error occurred during processing")
                return False
            else:
                self.log("\n⚠️ TEST INCONCLUSIVE - No completion or error event received")
                return False

        except Exception as e:
            self.log(f"Test failed with exception: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            return False

    def cleanup(self):
        """Stop all processes."""
        self.log("Cleaning up...")

        if self.daemon_process:
            self.daemon_process.terminate()
            try:
                self.daemon_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.daemon_process.kill()
            self.log("Daemon stopped")

        if self.mock_server_process:
            self.mock_server_process.terminate()
            try:
                self.mock_server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.mock_server_process.kill()
            self.log("Mock server stopped")


def main():
    parser = argparse.ArgumentParser(description="Mock LLM Test Runner")
    parser.add_argument(
        "--message",
        default="Hello, this is a test message for the mock LLM server!",
        help="Test message to send",
    )
    parser.add_argument(
        "--mock-host",
        default="0.0.0.0",
        help="Mock server host",
    )
    parser.add_argument(
        "--mock-port",
        type=int,
        default=4124,
        help="Mock server port",
    )
    parser.add_argument(
        "--daemon-host",
        default="localhost",
        help="Daemon host",
    )
    parser.add_argument(
        "--daemon-port",
        type=int,
        default=8078,
        help="Daemon port",
    )
    args = parser.parse_args()

    # Create runner with CLI-provided config
    runner = MockTestRunner(
        mock_host=args.mock_host,
        mock_port=args.mock_port,
        daemon_host=args.daemon_host,
        daemon_port=args.daemon_port,
    )

    # Handle signals
    def signal_handler(sig, frame):
        runner.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        success = runner.run_test(args.message)
        runner.cleanup()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"ERROR: {e}")
        runner.cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()
