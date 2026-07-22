#!/usr/bin/env python3
"""
Mock Test: notification_mock_sse_test
Branch: feature/notification-ui-improvements (commit 4502aebb)

Validates the SSE notification flow by simulating the frontend notification
service and notification-bell component logic against mock SSE payloads.

Since the frontend is Angular (TypeScript), we simulate the EXACT mapping logic
extracted from the source files:
  - notification.service.ts: instance_created handler (data.data mapping)
  - notification.service.ts: addNotification (InstanceNotification → Notification)
  - notification-bell.component.ts: getNotificationTitle (priority chain)
  - notification-bell.component.ts: onNotificationClick (route construction)
  - notification-bell.component.html: project-label conditional rendering

The mock SSE server on port 10080 emits a real instance_created event to
verify the full event → parse → map → render → navigate pipeline.

Dual-layer timeout:
  - Layer 1 (outer): timeout 300 (bash wrapper)
  - Layer 2 (inner): this script self-timeouts at 4 minutes (240s)
"""

import json
import signal
import socket
import sys
import threading
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MOCK_PORT = 10080
SCRIPT_TIMEOUT_S = 240  # 4 min internal timer
PASS_COUNT = 0
FAIL_COUNT = 0
ERRORS = []

# ---------------------------------------------------------------------------
# Script-internal timeout (Layer 2)
# ---------------------------------------------------------------------------

def _timeout_handler(signum, frame):
    print("RESULT: TIMEOUT")
    print(f"(internal timer {SCRIPT_TIMEOUT_S}s exceeded)")
    sys.exit(124)

signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(SCRIPT_TIMEOUT_S)

# ---------------------------------------------------------------------------
# Port cleanup helpers
# ---------------------------------------------------------------------------

def kill_port(port: int, label: str = "") -> None:
    """Kill any process listening on the given port."""
    if port == 8088:
        print(f"  [SKIP] Port {port} is ensemble self-system — NEVER kill.")
        return
    try:
        import subprocess
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5
        )
        pids = result.stdout.strip().split("\n") if result.stdout.strip() else []
        for pid in pids:
            if pid:
                subprocess.run(["kill", "-9", pid], timeout=5)
                print(f"  [CLEANUP] Killed PID {pid} on port {port} ({label})")
    except Exception as e:
        # Non-fatal — port may simply be free
        pass


def is_port_free(port: int) -> bool:
    """Check if a port is free."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) != 0
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Frontend logic simulation (extracted from source)
# ---------------------------------------------------------------------------

# --- from notification.service.ts ---

KB_AGENT_IDS = {"experiencer", "kb-importer", "kb-writer"}
SOUND_EXCLUDED_AGENT_IDS = {"kb-importer", "experiencer", "kb-writer"}


def parse_instance_created_sse(event_data_json: str, show_kb: bool = True):
    """
    Simulate the instance_created SSE handler from notification.service.ts
    (lines 127-154).

    Parses the SSE event data and maps it to an InstanceInfo dict,
    applying KB agent filtering.

    Returns the InstanceInfo dict or None (if filtered or parse error).
    """
    try:
        data = json.loads(event_data_json)
    except (json.JSONDecodeError, TypeError):
        return None

    agent_id = data.get("data", {}).get("agent_id")
    if not show_kb and agent_id in KB_AGENT_IDS:
        return None

    d = data.get("data", {})
    instance_data = {
        "instance_id": d.get("instance_id"),
        "agent_id": agent_id,
        "parent_id": d.get("parent_id") or None,
        "status": d.get("status"),
        "project_id": d.get("project_id") if d.get("project_id") is not None else None,
        "title": d.get("title") if d.get("title") is not None else None,
        "instance_name": d.get("instance_name") if d.get("instance_name") is not None else None,
        "children": d.get("children") or [],
        "created_at": d.get("created_at"),
        "updated_at": d.get("created_at"),  # FE maps updated_at = created_at
    }
    return instance_data


def parse_notification_sse(event_data_json: str):
    """
    Simulate the 'notification' SSE handler from notification.service.ts
    (lines 115-124, addNotification at lines 183-206).

    Parses the notification event data into the internal Notification shape.
    """
    try:
        data = json.loads(event_data_json)
    except (json.JSONDecodeError, TypeError):
        return None

    # The notification event data is flat (InstanceNotification shape)
    notification = {
        "instance_id": data.get("instance_id"),
        "agent_id": data.get("agent_id"),
        "name": data.get("name"),
        "status": data.get("status"),
        "timestamp": data.get("timestamp"),
        "project_id": data.get("project_id"),
        "instance_name": data.get("instance_name"),
        # addNotification adds these:
        "id": data.get("instance_id"),
        "read": False,
    }
    return notification


# --- from notification-bell.component.ts ---

def get_notification_title(notification: dict) -> str:
    """
    Simulate getNotificationTitle from notification-bell.component.ts (line 110-112).

    Priority: instance_name > name > agent_id
    """
    return (
        notification.get("instance_name")
        or notification.get("name")
        or notification.get("agent_id")
    )


def build_navigation_route(notification: dict) -> list:
    """
    Simulate onNotificationClick route construction from
    notification-bell.component.ts (lines 118-140).

    Returns the Angular router.navigate array.
    """
    project_id = notification.get("project_id")
    instance_id = notification.get("instance_id")
    route = ["/projects", project_id or "all", "instances", instance_id]
    return route


def route_to_string(route: list) -> str:
    """Convert route array to URL path string."""
    parts = [str(p) for p in route if p is not None]
    return "/".join(parts)


def should_show_project_label(notification: dict) -> bool:
    """
    Simulate the HTML template conditional:
      @if (notification.project_id) { <span class="project-label">...}
    (notification-bell.component.html line 70-72)
    """
    return bool(notification.get("project_id"))


# ---------------------------------------------------------------------------
# Mock SSE Server on port 10080
# ---------------------------------------------------------------------------

class MockSSEHandler(BaseHTTPRequestHandler):
    """Minimal SSE endpoint that emits one instance_created event then closes."""

    # Events to emit, set per-test
    events_to_emit = []

    def log_message(self, format, *args):
        pass  # Suppress logs

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/notifications/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            for event in self.events_to_emit:
                payload = json.dumps(event)
                self.wfile.write(f"event: instance_created\ndata: {payload}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)

            # Keep connection briefly then close
            time.sleep(0.2)
        else:
            self.send_response(404)
            self.end_headers()


def start_mock_sse_server(events: list) -> HTTPServer:
    """Start the mock SSE server on MOCK_PORT."""
    MockSSEHandler.events_to_emit = events
    server = HTTPServer(("127.0.0.1", MOCK_PORT), MockSSEHandler)
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def fetch_mock_sse_events(max_events: int = 5) -> list:
    """Connect to the mock SSE server and collect events via raw socket."""
    events = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(("127.0.0.1", MOCK_PORT))
        sock.sendall(b"GET /api/notifications/stream HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")

        buffer = b""
        deadline = time.time() + 4  # 4s max
        while len(events) < max_events and time.time() < deadline:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
            except socket.timeout:
                break  # No more data

            # Parse complete SSE events (delimited by \n\n)
            while b"\n\n" in buffer:
                raw_event, buffer = buffer.split(b"\n\n", 1)
                lines = raw_event.decode("utf-8", errors="replace").strip().split("\n")
                event_type = event_data = None
                for line in lines:
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: "):
                        event_data = line[6:].strip()
                if event_type and event_data:
                    events.append({"type": event_type, "data": event_data})
        sock.close()
    except Exception as e:
        print(f"  [WARN] SSE fetch error: {e}")
    return events


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def check(name: str, condition: bool, evidence: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  ✅ PASS: {name}" + (f" — {evidence}" if evidence else ""))
    else:
        FAIL_COUNT += 1
        ERRORS.append(f"{name}: {evidence}")
        print(f"  ❌ FAIL: {name} — {evidence}")


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

def test_mock_sse_event_rendering():
    """Scenario Group 1: Mock SSE event rendering test."""
    section("Scenario Group 1: Mock SSE Event Rendering")

    # --- 1a: Full payload with project_id + instance_name ---
    print("\n  [1a] Full payload: project_id + instance_name present")
    mock_event_1a = {
        "event_type": "instance_created",
        "data": {
            "instance_id": "inst-aaa-111",
            "agent_id": "developer",
            "parent_id": None,
            "status": "COMPLETED",
            "project_id": "proj-abc-123",
            "instance_name": "Code Refactor Task",
            "title": "Refactor auth module",
            "children": [],
            "created_at": "2026-07-22T17:00:00Z",
        },
        "timestamp": "2026-07-22T17:00:01Z",
    }

    # Parse via instance_created handler
    instance_info = parse_instance_created_sse(json.dumps(mock_event_1a))
    check("1a-instance_id mapped", instance_info["instance_id"] == "inst-aaa-111")
    check("1a-project_id mapped", instance_info["project_id"] == "proj-abc-123")
    check("1a-instance_name mapped", instance_info["instance_name"] == "Code Refactor Task")
    check("1a-status mapped", instance_info["status"] == "COMPLETED")

    # Now simulate a notification event (flat shape) and render title
    notification = {
        "instance_id": "inst-aaa-111",
        "agent_id": "developer",
        "name": "developer",
        "status": "COMPLETED",
        "timestamp": "2026-07-22T17:00:01Z",
        "project_id": "proj-abc-123",
        "instance_name": "Code Refactor Task",
        "id": "inst-aaa-111",
        "read": False,
    }
    title = get_notification_title(notification)
    check("1a-title uses instance_name", title == "Code Refactor Task",
          f'got "{title}"')
    check("1a-project_label renders", should_show_project_label(notification) is True)

    # --- 1b: instance_name present but title absent ---
    print("\n  [1b] instance_name present, title absent (still renders)")
    notif_1b = {**notification, "instance_name": "My Custom Name", "name": "leader"}
    title_1b = get_notification_title(notif_1b)
    check("1b-title is instance_name", title_1b == "My Custom Name",
          f'got "{title_1b}"')

    # --- 1c: instance_name None, falls back to name ---
    print("\n  [1c] instance_name=None, falls back to name")
    notif_1c = {**notification, "instance_name": None, "name": "task-42"}
    title_1c = get_notification_title(notif_1c)
    check("1c-title falls back to name", title_1c == "task-42",
          f'got "{title_1c}"')

    # --- 1d: both instance_name and name None, falls back to agent_id ---
    print("\n  [1d] instance_name=None, name=None, falls back to agent_id")
    notif_1d = {**notification, "instance_name": None, "name": None}
    title_1d = get_notification_title(notif_1d)
    check("1d-title falls back to agent_id", title_1d == "developer",
          f'got "{title_1d}"')

    # --- 1e: KB agent filtered when showKb=False ---
    print("\n  [1e] KB agent filtered when showKb=False")
    kb_event = {
        "event_type": "instance_created",
        "data": {
            "instance_id": "inst-kb-001",
            "agent_id": "kb-writer",
            "status": "COMPLETED",
            "project_id": "proj-kb",
            "instance_name": "KB Write Task",
            "created_at": "2026-07-22T17:00:00Z",
        },
    }
    filtered = parse_instance_created_sse(json.dumps(kb_event), show_kb=False)
    check("1e-kb-agent filtered out", filtered is None, "should return None when showKb=False")

    unfiltered = parse_instance_created_sse(json.dumps(kb_event), show_kb=True)
    check("1e-kb-agent passes when showKb=True", unfiltered is not None)


def test_navigation_path_resolution():
    """Scenario Group 2: Navigation path resolution test."""
    section("Scenario Group 2: Navigation Path Resolution")

    # --- 2a: project_id present → project-scoped route ---
    print("\n  [2a] project_id present → /projects/{project_id}/instances/{instance_id}")
    notif_2a = {
        "instance_id": "inst-nav-001",
        "project_id": "proj-nav-456",
        "instance_name": "Navigation Test",
    }
    route_2a = build_navigation_route(notif_2a)
    route_str_2a = route_to_string(route_2a)
    expected_2a = "/projects/proj-nav-456/instances/inst-nav-001"
    check("2a-route correct", route_str_2a == expected_2a,
          f'got "{route_str_2a}", expected "{expected_2a}"')

    # --- 2b: project_id absent → 'all' fallback ---
    print("\n  [2b] project_id=None → /projects/all/instances/{instance_id}")
    notif_2b = {
        "instance_id": "inst-nav-002",
        "project_id": None,
        "instance_name": "No Project Test",
    }
    route_2b = build_navigation_route(notif_2b)
    route_str_2b = route_to_string(route_2b)
    expected_2b = "/projects/all/instances/inst-nav-002"
    check("2b-fallback route correct", route_str_2b == expected_2b,
          f'got "{route_str_2b}", expected "{expected_2b}"')

    # --- 2c: project_id empty string → 'all' fallback (edge case) ---
    print("\n  [2c] project_id='' → 'all' fallback (empty string is falsy)")
    notif_2c = {
        "instance_id": "inst-nav-003",
        "project_id": "",
        "instance_name": "Empty Project Test",
    }
    route_2c = build_navigation_route(notif_2c)
    route_str_2c = route_to_string(route_2c)
    expected_2c = "/projects/all/instances/inst-nav-003"
    check("2c-empty-string fallback correct", route_str_2c == expected_2c,
          f'got "{route_str_2c}", expected "{expected_2c}"')

    # --- 2d: project_id present, instance_name absent → still navigates ---
    print("\n  [2d] project_id present, instance_name absent → still navigates correctly")
    notif_2d = {
        "instance_id": "inst-nav-004",
        "project_id": "proj-nav-789",
        "instance_name": None,
        "agent_id": "tester",
    }
    route_2d = build_navigation_route(notif_2d)
    route_str_2d = route_to_string(route_2d)
    expected_2d = "/projects/proj-nav-789/instances/inst-nav-004"
    check("2d-navigates without instance_name", route_str_2d == expected_2d,
          f'got "{route_str_2d}"')


def test_edge_cases():
    """Scenario Group 3: Edge cases — graceful handling."""
    section("Scenario Group 3: Edge Cases")

    # --- 3a: project_id=None, instance_name=None → no crash ---
    print("\n  [3a] project_id=None, instance_name=None → no crash, fields optional")
    notif_3a = {
        "instance_id": "inst-edge-001",
        "agent_id": "developer",
        "name": "task-x",
        "status": "COMPLETED",
        "timestamp": "2026-07-22T17:00:01Z",
        "project_id": None,
        "instance_name": None,
        "id": "inst-edge-001",
        "read": False,
    }
    try:
        title_3a = get_notification_title(notif_3a)
        route_3a = build_navigation_route(notif_3a)
        label_3a = should_show_project_label(notif_3a)
        check("3a-no crash on title", title_3a == "task-x", f'got "{title_3a}"')
        check("3a-route uses 'all'", route_to_string(route_3a) == "/projects/all/instances/inst-edge-001")
        check("3a-project_label hidden", label_3a is False)
    except Exception as e:
        check("3a-no crash", False, f"Exception: {e}")

    # --- 3b: instance_name present but empty string → graceful ---
    print("\n  [3b] instance_name='' (empty string) → graceful fallback")
    notif_3b = {
        "instance_id": "inst-edge-002",
        "agent_id": "developer",
        "name": "fallback-name",
        "status": "ERROR",
        "timestamp": "2026-07-22T17:00:01Z",
        "project_id": "proj-edge-002",
        "instance_name": "",
        "id": "inst-edge-002",
        "read": False,
    }
    try:
        title_3b = get_notification_title(notif_3b)
        # JS: "" is falsy, so || chain skips to name
        check("3b-empty instance_name falls back to name", title_3b == "fallback-name",
              f'got "{title_3b}"')
    except Exception as e:
        check("3b-no crash", False, f"Exception: {e}")

    # --- 3c: project_id present but instance_name absent → navigates ---
    print("\n  [3c] project_id present, instance_name absent → navigates correctly")
    notif_3c = {
        "instance_id": "inst-edge-003",
        "agent_id": "leader",
        "name": None,
        "status": "COMPLETED",
        "timestamp": "2026-07-22T17:00:01Z",
        "project_id": "proj-edge-003",
        "instance_name": None,
        "id": "inst-edge-003",
        "read": False,
    }
    try:
        route_3c = build_navigation_route(notif_3c)
        route_str_3c = route_to_string(route_3c)
        check("3c-navigates to project route", route_str_3c == "/projects/proj-edge-003/instances/inst-edge-003",
              f'got "{route_str_3c}"')
        check("3c-project_label shows", should_show_project_label(notif_3c) is True)
        title_3c = get_notification_title(notif_3c)
        check("3c-title falls back to agent_id", title_3c == "leader", f'got "{title_3c}"')
    except Exception as e:
        check("3c-no crash", False, f"Exception: {e}")

    # --- 3d: Malformed JSON payload → no crash ---
    print("\n  [3d] Malformed JSON → no crash, returns None")
    result_3d = parse_instance_created_sse("{not valid json}")
    check("3d-malformed JSON returns None", result_3d is None)

    result_3d2 = parse_notification_sse("<<<broken>>>")
    check("3d-malformed notification returns None", result_3d2 is None)

    # --- 3e: Missing data wrapper → no crash ---
    print("\n  [3e] Missing 'data' wrapper → no crash")
    no_data_event = json.dumps({"event_type": "instance_created"})
    result_3e = parse_instance_created_sse(no_data_event)
    check("3e-missing data wrapper returns dict with None values", result_3e is not None)
    if result_3e:
        check("3e-instance_id is None", result_3e["instance_id"] is None)
        check("3e-agent_id is None", result_3e["agent_id"] is None)


def test_live_mock_sse_server():
    """Scenario Group 4: Live mock SSE server end-to-end."""
    section("Scenario Group 4: Live Mock SSE Server (port 10080)")

    events_to_emit = [
        {
            "event_type": "instance_created",
            "data": {
                "instance_id": "inst-live-001",
                "agent_id": "developer",
                "parent_id": None,
                "status": "COMPLETED",
                "project_id": "proj-live-abc",
                "instance_name": "Live SSE Test Task",
                "title": "Live test",
                "children": [],
                "created_at": "2026-07-22T17:00:00Z",
            },
            "timestamp": "2026-07-22T17:00:01Z",
        },
        {
            "event_type": "instance_created",
            "data": {
                "instance_id": "inst-live-002",
                "agent_id": "leader",
                "parent_id": None,
                "status": "ERROR",
                "project_id": None,
                "instance_name": None,
                "title": None,
                "children": [],
                "created_at": "2026-07-22T17:01:00Z",
            },
            "timestamp": "2026-07-22T17:01:01Z",
        },
    ]

    print(f"\n  Starting mock SSE server on port {MOCK_PORT}...")
    server = start_mock_sse_server(events_to_emit)
    time.sleep(0.3)  # Let server bind

    check("4-server started on port 10080", is_port_free(MOCK_PORT) is False,
          "port should be in use")

    print("  Fetching SSE events...")
    received = fetch_mock_sse_events()

    check("4-received 2 events", len(received) == 2,
          f"got {len(received)} events")

    if len(received) >= 1:
        first = received[0]
        check("4-first event type is instance_created", first["type"] == "instance_created",
              f'got "{first["type"]}"')

        # Parse the first event through the instance_created handler
        parsed = parse_instance_created_sse(first["data"])
        if parsed:
            check("4-event1 instance_id", parsed["instance_id"] == "inst-live-001")
            check("4-event1 project_id", parsed["project_id"] == "proj-live-abc")
            check("4-event1 instance_name", parsed["instance_name"] == "Live SSE Test Task")
        else:
            check("4-event1 parsed successfully", False, "parse returned None")

    if len(received) >= 2:
        second = received[1]
        parsed2 = parse_instance_created_sse(second["data"])
        if parsed2:
            check("4-event2 project_id is None", parsed2["project_id"] is None)
            check("4-event2 instance_name is None", parsed2["instance_name"] is None)
            check("4-event2 instance_id", parsed2["instance_id"] == "inst-live-002")

    # Cleanup server
    server.shutdown()
    server.server_close()
    time.sleep(0.2)
    check("4-server stopped, port freed", is_port_free(MOCK_PORT) is True,
          "port should be free after shutdown")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    start_time = time.time()

    print("=" * 64)
    print("=== Test Pack: notification_mock_sse_test ===")
    print("=" * 64)
    print(f"  Branch: feature/notification-ui-improvements (commit 4502aebb)")
    print(f"  Mock port: {MOCK_PORT}")
    print(f"  Internal timeout: {SCRIPT_TIMEOUT_S}s (4 min)")
    print()

    # Pre-test cleanup
    print("[CLEANUP] Pre-test port cleanup...")
    kill_port(MOCK_PORT, "pre-test")

    # Run all scenario groups
    test_mock_sse_event_rendering()
    test_navigation_path_resolution()
    test_edge_cases()
    test_live_mock_sse_server()

    # Post-test cleanup
    print("\n[CLEANUP] Post-test port cleanup...")
    kill_port(MOCK_PORT, "post-test")

    # Summary
    elapsed = time.time() - start_time
    total = PASS_COUNT + FAIL_COUNT
    print(f"\n{'=' * 64}")
    print(f"  Results: {PASS_COUNT} passed, {FAIL_COUNT} failed, {total} total")
    print(f"  Runtime: {elapsed:.1f}s")
    if ERRORS:
        print(f"\n  Failures:")
        for e in ERRORS:
            print(f"    - {e}")
    print(f"{'=' * 64}")

    # Final result
    if FAIL_COUNT == 0:
        print("RESULT: PASS")
        sys.exit(0)
    else:
        print(f"RESULT: FAIL ({PASS_COUNT}/{total} passed)")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        elapsed = 0
        print(f"\n[FATAL] Unexpected exception: {e}")
        traceback.print_exc()
        print("RESULT: FAIL")
        sys.exit(1)
    finally:
        # Ensure port is freed even on crash
        kill_port(MOCK_PORT, "finally-block")
        signal.alarm(0)  # Cancel the alarm
